"""Motor del mini reproductor (con una salida de audio simulada: los servidores de pruebas
no tienen tarjeta de sonido)."""
import sys
import threading
import time

import pytest

import player as r
from test_engine import make_audio, needs_ffmpeg

np = pytest.importorskip("numpy")
SPEEDUP = 8  # la salida falsa "escucha" 8 veces más rápido que unos parlantes


class FakeOutput:
    """Consume el audio al ritmo de unos parlantes (acelerado) y guarda lo que "suena"."""
    instances = []

    def __init__(self, callback, on_finish):
        self.callback, self.on_finish = callback, on_finish
        self.played = bytearray()
        self.closed = threading.Event()
        FakeOutput.instances.append(self)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        n = r.FRAMES_PER_BLOCK
        while not self.closed.is_set():
            buf = bytearray(n * r.BYTES_PER_FRAME)
            try:
                self.callback(buf, n)
            except r.StopPlayback:
                self.played += buf
                self.on_finish()
                return
            self.played += buf
            time.sleep(n / r.SAMPLE_RATE / SPEEDUP)

    def close(self):
        self.closed.set()
        self.on_finish()  # como sounddevice: el aviso de "terminado" llega también al cerrar


@pytest.fixture
def engine():
    FakeOutput.instances.clear()
    end = threading.Event()
    mo = r.AudioEngine(on_finish=end.set, output_factory=FakeOutput)
    mo.end = end
    yield mo
    mo.stop()


def _amplitude(data: bytes) -> float:
    samples = np.frombuffer(bytes(data), dtype=np.int16)
    return float(np.abs(samples).mean()) if len(samples) else 0.0


@needs_ffmpeg
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".opus"])
def test_plays_to_the_end(engine, tmp_path, ext):
    path = make_audio(tmp_path / f"tono{ext}", volume_db=-6, seconds=2)
    engine.play(path)
    assert engine.status == "playing" and 1.9 < engine.duration < 2.2
    assert engine.end.wait(10), "la canción no terminó"
    assert engine.status == "stopped"
    assert engine.position == pytest.approx(engine.duration, abs=0.15)
    assert _amplitude(FakeOutput.instances[-1].played) > 200  # sonó algo (el silencio es 0)


@needs_ffmpeg
def test_pause_and_resume(engine, tmp_path):
    engine.play(make_audio(tmp_path / "t.mp3", seconds=4))
    time.sleep(0.1)
    engine.pause_playback()
    assert engine.status == "paused"
    pos = engine.position
    time.sleep(0.3)
    assert engine.position == pos  # en pausa no avanza
    engine.resume()
    time.sleep(0.2)
    assert engine.position > pos and engine.status == "playing"
    engine.toggle()
    assert engine.status == "paused"


@needs_ffmpeg
def test_seek(engine, tmp_path):
    engine.play(make_audio(tmp_path / "t.mp3", seconds=4))
    engine.search(3.0)
    assert engine.position >= 3.0
    assert engine.end.wait(5)
    assert engine.position == pytest.approx(engine.duration, abs=0.15)
    # al adelantar se cierra la salida anterior, pero eso NO cuenta como "terminó la canción"
    assert len(FakeOutput.instances) == 2


@needs_ffmpeg
def test_seek_while_paused_stays_paused(engine, tmp_path):
    engine.play(make_audio(tmp_path / "t.mp3", seconds=4))
    engine.pause_playback()
    engine.search(2.0)
    time.sleep(0.2)
    assert engine.status == "paused" and engine.position == pytest.approx(2.0, abs=0.01)


@needs_ffmpeg
def test_volume(engine, tmp_path):
    path = make_audio(tmp_path / "t.mp3", volume_db=-6, seconds=1)
    engine.volume = 1.0
    engine.play(path)
    engine.end.wait(10)
    strong = _amplitude(FakeOutput.instances[-1].played)
    engine.end.clear()
    engine.volume = 0.5
    engine.play(path)
    engine.end.wait(10)
    assert _amplitude(FakeOutput.instances[-1].played) == pytest.approx(strong / 2, rel=0.1)
    engine.volume = 7
    assert engine.volume == 1.0


@needs_ffmpeg
def test_stop_does_not_count_as_end(engine, tmp_path):
    engine.play(make_audio(tmp_path / "t.mp3", seconds=4))
    time.sleep(0.1)
    process = engine._process
    engine.stop()
    assert engine.status == "stopped" and not engine.end.is_set()
    assert process.poll() is not None  # FFmpeg cerrado


def test_format_time():
    assert r._format_time(0) == "0:00"
    assert r._format_time(65.9) == "1:05"
    assert r._format_time(-3) == "0:00"


def test_audio_available_returns_reason():
    ok, reason = r.audio_available()
    assert isinstance(ok, bool) and (ok or reason)


def test_audio_available_when_portaudio_fails(monkeypatch):
    """En Linux sin servidor de sonido, importar sounddevice lanza PortAudioError (no OSError)."""
    import builtins
    original = builtins.__import__

    class PortAudioError(Exception):
        pass

    def fake_import(name, *a, **k):
        if name == "sounddevice":
            raise PortAudioError("Error initializing PortAudio")
        return original(name, *a, **k)
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    ok, reason = r.audio_available()
    assert not ok and "no se pudo iniciar el audio" in reason
