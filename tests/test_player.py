"""Mini player engine (with a simulated audio output: test servers
have no sound card)."""
import sys
import threading
import time

import pytest
from test_engine import make_audio, needs_ffmpeg

from audyax.player import bar
from audyax.player import engine as player_engine

np = pytest.importorskip("numpy")
SPEEDUP = 8  # the fake output "listens" 8 times faster than real speakers


class FakeOutput:
    """Consumes the audio at speaker pace (sped up) and keeps what "plays"."""
    instances = []

    def __init__(self, callback, on_finish):
        self.callback, self.on_finish = callback, on_finish
        self.played = bytearray()
        self.closed = threading.Event()
        FakeOutput.instances.append(self)

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        n = player_engine.FRAMES_PER_BLOCK
        while not self.closed.is_set():
            buf = bytearray(n * player_engine.BYTES_PER_FRAME)
            try:
                self.callback(buf, n)
            except player_engine.StopPlayback:
                self.played += buf
                self.on_finish()
                return
            self.played += buf
            time.sleep(n / player_engine.SAMPLE_RATE / SPEEDUP)

    def close(self):
        self.closed.set()
        self.on_finish()  # like sounddevice: the "finished" notice also arrives on close


@pytest.fixture
def engine():
    FakeOutput.instances.clear()
    end = threading.Event()
    eng = player_engine.AudioEngine(on_finish=end.set, output_factory=FakeOutput)
    eng.end = end
    yield eng
    eng.stop()


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
    assert _amplitude(FakeOutput.instances[-1].played) > 200  # something played (silence is 0)


@needs_ffmpeg
def test_pause_and_resume(engine, tmp_path):
    engine.play(make_audio(tmp_path / "t.mp3", seconds=4))
    time.sleep(0.1)
    engine.pause_playback()
    assert engine.status == "paused"
    pos = engine.position
    time.sleep(0.3)
    assert engine.position == pos  # doesn't advance while paused
    engine.resume()
    time.sleep(0.2)
    assert engine.position > pos and engine.status == "playing"
    engine.toggle()
    assert engine.status == "paused"


@needs_ffmpeg
def test_seek(engine, tmp_path):
    engine.play(make_audio(tmp_path / "t.mp3", seconds=4))
    engine.seek(3.0)
    assert engine.position >= 3.0
    assert engine.end.wait(5)
    assert engine.position == pytest.approx(engine.duration, abs=0.15)
    # seeking closes the previous output, but that does NOT count as "the song ended"
    assert len(FakeOutput.instances) == 2


@needs_ffmpeg
def test_seek_while_paused_stays_paused(engine, tmp_path):
    engine.play(make_audio(tmp_path / "t.mp3", seconds=4))
    engine.pause_playback()
    engine.seek(2.0)
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
    process = engine._decoders[0].process
    engine.stop()
    assert engine.status == "stopped" and not engine.end.is_set()
    assert process.poll() is not None  # FFmpeg closed


def test_format_time():
    assert bar._format_time(0) == "0:00"
    assert bar._format_time(65.9) == "1:05"
    assert bar._format_time(-3) == "0:00"


def test_audio_available_returns_reason():
    ok, reason = player_engine.audio_available()
    assert isinstance(ok, bool) and (ok or reason)


def test_audio_available_when_portaudio_fails(monkeypatch):
    """On Linux without a sound server, importing sounddevice raises PortAudioError (not OSError)."""
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
    ok, reason = player_engine.audio_available()
    assert not ok and "no se pudo iniciar el audio" in reason


# ------------------------------------------------------------------ equalizer
def test_eq_filter():
    assert player_engine.eq_filter(None) == ""
    assert player_engine.eq_filter([0] * 10) == ""
    f = player_engine.eq_filter([6, 0, 0, 0, 0, 0, 0, 0, 0, -20])
    assert f.startswith("volume=-6.0dB,equalizer=f=31:t=o:w=1:g=6.0,")   # headroom for the boost
    assert "f=16000:t=o:w=1:g=-12.0" in f                                 # limited to ±12 dB
    assert "f=62" not in f and f.endswith("alimiter=limit=0.97:level=false")
    assert player_engine.eq_filter([-3] * 10).startswith("equalizer=")    # only cuts: no headroom
    assert all(len(g) == len(player_engine.EQ_BANDS) for g in player_engine.EQ_PRESETS.values())


def _band_energy(data: bytes, freq: float) -> float:
    samples = np.frombuffer(bytes(data), dtype=np.int16).astype(np.float32)[::2]
    spectrum = np.abs(np.fft.rfft(samples))
    freqs = np.fft.rfftfreq(len(samples), 1 / player_engine.SAMPLE_RATE)
    return float(spectrum[(freqs > freq * 0.8) & (freqs < freq * 1.2)].sum())


@needs_ffmpeg
def test_equalizer_changes_the_sound(engine, tmp_path):
    song = tmp_path / "t.wav"
    import subprocess
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=100:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=8000:duration=3", "-filter_complex", "amix=inputs=2",
                    str(song)], check=True)
    engine.play(song)
    assert engine.end.wait(10)
    flat = FakeOutput.instances[-1].played
    engine.end.clear()
    engine.set_equalizer(player_engine.EQ_PRESETS["Graves+"])     # stopped: just remembered
    assert engine.equalizer == list(player_engine.EQ_PRESETS["Graves+"])
    engine.play(song)
    assert engine.end.wait(10)
    bass = FakeOutput.instances[-1].played
    ratio_flat = _band_energy(flat, 100) / _band_energy(flat, 8000)
    ratio_bass = _band_energy(bass, 100) / _band_energy(bass, 8000)
    assert ratio_bass > ratio_flat * 1.5


@needs_ffmpeg
def test_changing_the_equalizer_keeps_the_position(engine, tmp_path):
    engine.play(make_audio(tmp_path / "t.mp3", seconds=6))
    time.sleep(0.2)
    before = engine.position
    engine.set_equalizer([3] * 10)
    assert engine.status == "playing" and engine.position >= before - 0.05
    engine.set_equalizer([3] * 10)            # same sound: doesn't restart
    assert len(FakeOutput.instances) == 2


# ------------------------------------------------------------------ gapless / crossfade
def _tone(path, freq, seconds):
    import subprocess
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={seconds}", str(path)], check=True)
    return path


@needs_ffmpeg
def test_gapless_continues_with_the_next_song(engine, tmp_path):
    a, b = _tone(tmp_path / "a.wav", 440, 1), _tone(tmp_path / "b.wav", 880, 1)
    advanced = []
    # the queue "a, b": after a (ahead=0 while a sounds) comes b; after b, nothing
    engine.peek_next = lambda ahead: str(b) if ahead == 0 and not advanced else None
    engine.on_advance = advanced.append
    engine.play(a)
    assert engine.end.wait(10)
    assert advanced == [str(b)] and engine.path == str(b)
    assert len(FakeOutput.instances) == 1                     # one output: no gap between them
    played = np.frombuffer(bytes(FakeOutput.instances[0].played), dtype=np.int16)[::2]
    total = len(played) / player_engine.SAMPLE_RATE
    assert 1.9 < total < 2.3
    # no silence where they meet (the tone is continuous across the boundary)
    middle = played[int(0.9 * player_engine.SAMPLE_RATE):int(1.1 * player_engine.SAMPLE_RATE)]
    window = player_engine.SAMPLE_RATE // 100
    quietest = min(np.abs(middle[i:i + window].astype(np.float32)).mean()
                   for i in range(0, len(middle) - window, window))
    assert quietest > 1000


@needs_ffmpeg
def test_crossfade_mixes_the_end_and_the_start(engine, tmp_path):
    a, b = _tone(tmp_path / "a.wav", 440, 3), _tone(tmp_path / "b.wav", 880, 3)
    advanced = []
    engine.crossfade = 1.0
    engine.peek_next = lambda ahead: str(b) if ahead == 0 and not advanced else None
    engine.on_advance = advanced.append
    engine.play(a)
    assert engine.end.wait(15)
    played = np.frombuffer(bytes(FakeOutput.instances[0].played), dtype=np.int16)
    total = len(played) / 2 / player_engine.SAMPLE_RATE
    assert 4.7 < total < 5.3                  # 3 + 3 - 1 s overlapped
    sr = player_engine.SAMPLE_RATE
    during = bytes(played[int(2.3 * sr) * 2:int(2.7 * sr) * 2])
    assert _band_energy(during, 440) > 0 and _band_energy(during, 880) > 0   # both at once
    after = bytes(played[int(3.5 * sr) * 2:int(4.0 * sr) * 2])
    assert _band_energy(after, 440) < _band_energy(after, 880) / 20          # only the new one


@needs_ffmpeg
def test_no_next_song_ends_normally(engine, tmp_path):
    engine.peek_next = lambda ahead: None
    engine.play(_tone(tmp_path / "a.wav", 440, 1))
    assert engine.end.wait(10) and engine.status == "stopped"


@needs_ffmpeg
def test_repeat_one_gapless(engine, tmp_path):
    a = _tone(tmp_path / "a.wav", 440, 1)
    plays, peeks = [], []

    def peek(ahead):     # repeat "one", twice: a, a, a
        peeks.append(ahead)
        return str(a) if len(peeks) <= 2 else None
    engine.peek_next = peek
    engine.on_advance = plays.append
    engine.play(a)
    assert engine.end.wait(10)
    assert plays == [str(a), str(a)]


def test_mix_fades_out_and_in():
    one = (np.ones(400, dtype=np.int16) * 10000).tobytes()       # 200 frames
    out = np.frombuffer(player_engine.mix(one, one, 0, 200), dtype=np.int16)[::2]
    assert abs(int(out[0]) - 10000) < 50 and abs(int(out[-1]) - 10000) < 200   # equal power
    silent = bytes(len(one))
    fade_out = np.frombuffer(player_engine.mix(one, silent, 0, 200), dtype=np.int16)[::2]
    assert fade_out[0] > 9900 and fade_out[-1] < 200
    short = np.frombuffer(player_engine.mix(one, one[:100], 0, 200), dtype=np.int16)
    assert len(short) == 400                                     # the shorter one is padded
