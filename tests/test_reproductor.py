"""Motor del mini reproductor (con una salida de audio simulada: los servidores de pruebas
no tienen tarjeta de sonido)."""
import sys
import threading
import time

import pytest

import reproductor as r
from test_motor import generar_audio, necesita_ffmpeg

np = pytest.importorskip("numpy")
VELOCIDAD = 8  # la salida falsa "escucha" 8 veces más rápido que unos parlantes


class SalidaFalsa:
    """Consume el audio al ritmo de unos parlantes (acelerado) y guarda lo que "suena"."""
    instancias = []

    def __init__(self, callback, al_terminar):
        self.callback, self.al_terminar = callback, al_terminar
        self.sonado = bytearray()
        self.cerrada = threading.Event()
        SalidaFalsa.instancias.append(self)

    def iniciar(self):
        threading.Thread(target=self._bucle, daemon=True).start()

    def _bucle(self):
        n = r.CUADROS_POR_BLOQUE
        while not self.cerrada.is_set():
            buf = bytearray(n * r.BYTES_POR_CUADRO)
            try:
                self.callback(buf, n)
            except r.Detener:
                self.sonado += buf
                self.al_terminar()
                return
            self.sonado += buf
            time.sleep(n / r.TASA / VELOCIDAD)

    def cerrar(self):
        self.cerrada.set()
        self.al_terminar()  # como sounddevice: el aviso de "terminado" llega también al cerrar


@pytest.fixture
def motor():
    SalidaFalsa.instancias.clear()
    fin = threading.Event()
    mo = r.MotorAudio(al_terminar=fin.set, fabrica_salida=SalidaFalsa)
    mo.fin = fin
    yield mo
    mo.detener()


def _amplitud(datos: bytes) -> float:
    muestras = np.frombuffer(bytes(datos), dtype=np.int16)
    return float(np.abs(muestras).mean()) if len(muestras) else 0.0


@necesita_ffmpeg
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".opus"])
def test_reproduce_hasta_el_final(motor, tmp_path, ext):
    ruta = generar_audio(tmp_path / f"tono{ext}", volumen_db=-6, segundos=2)
    motor.reproducir(ruta)
    assert motor.estado == "reproduciendo" and 1.9 < motor.duracion < 2.2
    assert motor.fin.wait(10), "la canción no terminó"
    assert motor.estado == "detenido"
    assert motor.posicion == pytest.approx(motor.duracion, abs=0.15)
    assert _amplitud(SalidaFalsa.instancias[-1].sonado) > 200  # sonó algo (el silencio es 0)


@necesita_ffmpeg
def test_pausar_y_reanudar(motor, tmp_path):
    motor.reproducir(generar_audio(tmp_path / "t.mp3", segundos=4))
    time.sleep(0.1)
    motor.pausar()
    assert motor.estado == "pausado"
    pos = motor.posicion
    time.sleep(0.3)
    assert motor.posicion == pos  # en pausa no avanza
    motor.reanudar()
    time.sleep(0.2)
    assert motor.posicion > pos and motor.estado == "reproduciendo"
    motor.alternar()
    assert motor.estado == "pausado"


@necesita_ffmpeg
def test_adelantar(motor, tmp_path):
    motor.reproducir(generar_audio(tmp_path / "t.mp3", segundos=4))
    motor.buscar(3.0)
    assert motor.posicion >= 3.0
    assert motor.fin.wait(5)
    assert motor.posicion == pytest.approx(motor.duracion, abs=0.15)
    # al adelantar se cierra la salida anterior, pero eso NO cuenta como "terminó la canción"
    assert len(SalidaFalsa.instancias) == 2


@necesita_ffmpeg
def test_adelantar_en_pausa_sigue_en_pausa(motor, tmp_path):
    motor.reproducir(generar_audio(tmp_path / "t.mp3", segundos=4))
    motor.pausar()
    motor.buscar(2.0)
    time.sleep(0.2)
    assert motor.estado == "pausado" and motor.posicion == pytest.approx(2.0, abs=0.01)


@necesita_ffmpeg
def test_volumen(motor, tmp_path):
    ruta = generar_audio(tmp_path / "t.mp3", volumen_db=-6, segundos=1)
    motor.volumen = 1.0
    motor.reproducir(ruta)
    motor.fin.wait(10)
    fuerte = _amplitud(SalidaFalsa.instancias[-1].sonado)
    motor.fin.clear()
    motor.volumen = 0.5
    motor.reproducir(ruta)
    motor.fin.wait(10)
    assert _amplitud(SalidaFalsa.instancias[-1].sonado) == pytest.approx(fuerte / 2, rel=0.1)
    motor.volumen = 7
    assert motor.volumen == 1.0


@necesita_ffmpeg
def test_detener_no_cuenta_como_fin(motor, tmp_path):
    motor.reproducir(generar_audio(tmp_path / "t.mp3", segundos=4))
    time.sleep(0.1)
    proceso = motor._proceso
    motor.detener()
    assert motor.estado == "detenido" and not motor.fin.is_set()
    assert proceso.poll() is not None  # FFmpeg cerrado


def test_formato_tiempo():
    assert r._formato_tiempo(0) == "0:00"
    assert r._formato_tiempo(65.9) == "1:05"
    assert r._formato_tiempo(-3) == "0:00"


def test_audio_disponible_devuelve_motivo():
    ok, motivo = r.audio_disponible()
    assert isinstance(ok, bool) and (ok or motivo)


def test_audio_disponible_si_portaudio_no_arranca(monkeypatch):
    """En Linux sin servidor de sonido, importar sounddevice lanza PortAudioError (no OSError)."""
    import builtins
    original = builtins.__import__

    class PortAudioError(Exception):
        pass

    def importar(nombre, *a, **k):
        if nombre == "sounddevice":
            raise PortAudioError("Error initializing PortAudio")
        return original(nombre, *a, **k)
    monkeypatch.delitem(sys.modules, "sounddevice", raising=False)
    monkeypatch.setattr(builtins, "__import__", importar)
    ok, motivo = r.audio_disponible()
    assert not ok and "no se pudo iniciar el audio" in motivo
