"""Pruebas de búsqueda, actualización de yt-dlp, detección de enlaces y editor de etiquetas."""
import subprocess
from io import BytesIO
from types import SimpleNamespace

import pytest

import motor_descarga as m
from test_motor import generar_audio, necesita_ffmpeg, necesita_mutagen


# ------------------------------------------------------------------ enlaces
@pytest.mark.parametrize("texto", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=abc&list=xyz",
    "https://youtube.com/playlist?list=PL123",
    "https://youtu.be/dQw4w9WgXcQ",
    "http://m.youtube.com/shorts/abc",
    "  https://www.youtube.com/watch?v=x  ",
])
def test_es_enlace_youtube(texto):
    assert m.es_enlace_youtube(texto)


@pytest.mark.parametrize("texto", [
    "", "daft punk", "https://google.com", "https://www.youtube.com/",
    "mira esto https://youtu.be/abc", "https://notyoutube.com/watch?v=x", None,
])
def test_no_es_enlace_youtube(texto):
    assert not m.es_enlace_youtube(texto)


def test_extraer_enlaces_de_lo_soltado():
    datos = ("{https://www.youtube.com/watch?v=aaa} https://youtu.be/bbb\n"
             "https://google.com https://youtu.be/bbb \"https://music.youtube.com/watch?v=ccc\".")
    assert m.extraer_enlaces(datos) == [
        "https://www.youtube.com/watch?v=aaa", "https://youtu.be/bbb",
        "https://music.youtube.com/watch?v=ccc"]
    assert m.extraer_enlaces("") == []


# ------------------------------------------------------------------ búsqueda
def test_duracion_texto():
    assert m._duracion_texto(65) == "1:05"
    assert m._duracion_texto(3725) == "1:02:05"
    assert m._duracion_texto(None) == ""


def test_buscar_arma_resultados(monkeypatch):
    pedido = {}

    class FalsoYDL:
        def __init__(self, opciones):
            pedido["opciones"] = opciones

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            pedido["url"] = url
            return {"entries": [
                {"id": "abc", "title": "One More Time", "channel": "Daft Punk - Topic",
                 "duration": 320, "url": "https://www.youtube.com/watch?v=abc", "ie_key": "Youtube"},
                {"id": "lista", "title": "Un canal", "ie_key": "YoutubeTab"},  # no es una canción
                None,
            ]}

    monkeypatch.setattr(m, "YoutubeDL", FalsoYDL)
    res = m.buscar("daft punk one more time", 5)
    assert pedido["url"] == "ytsearch5:daft punk one more time"
    assert pedido["opciones"]["extract_flat"] == "in_playlist"
    assert res == [{"id": "abc", "titulo": "One More Time", "url": "https://www.youtube.com/watch?v=abc",
                    "miniatura": "https://i.ytimg.com/vi/abc/mqdefault.jpg",
                    "canal": "Daft Punk", "duracion": "5:20"}]


def test_buscar_texto_vacio_no_consulta():
    assert m.buscar("   ") == []


# ------------------------------------------------------------------ yt-dlp
def test_es_mas_nueva():
    assert m.es_mas_nueva("2026.11.10", "2026.10.26")
    assert m.es_mas_nueva("2026.10.26.1", "2026.10.26")
    assert not m.es_mas_nueva("2026.10.26", "2026.10.26")
    assert not m.es_mas_nueva("2025.12.31", "2026.01.01")


def test_version_ytdlp():
    assert m.es_mas_nueva(m.version_ytdlp(), "2000.1.1")


def test_actualizar_en_ejecutable_no_usa_pip(monkeypatch):
    monkeypatch.setattr(m.sys, "frozen", True, raising=False)
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: pytest.fail("no debe llamar a pip"))
    ok, mensaje = m.actualizar_ytdlp()
    assert not ok and "empaquetada" in mensaje


def test_actualizar_llama_a_pip(monkeypatch):
    llamado = {}

    def falso_run(cmd, **kw):
        llamado["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr(m.subprocess, "run", falso_run)
    assert m.actualizar_ytdlp() == (True, "yt-dlp se actualizó correctamente.")
    assert llamado["cmd"][1:6] == ["-m", "pip", "install", "-U", "yt-dlp"]
    assert ("ytmusicapi" in llamado["cmd"]) == m.HAY_YTMUSIC


def test_actualizar_informa_el_error_de_pip(monkeypatch):
    monkeypatch.setattr(m.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=1, stdout="", stderr="línea 1\nERROR: sin permisos"))
    ok, mensaje = m.actualizar_ytdlp()
    assert not ok and "sin permisos" in mensaje


def test_actualizar_sin_python(monkeypatch):
    def falla(*a, **k):
        raise subprocess.TimeoutExpired("pip", 600)
    monkeypatch.setattr(m.subprocess, "run", falla)
    assert m.actualizar_ytdlp()[0] is False


# ------------------------------------------------------------------ editor de etiquetas
def _jpeg(color="red", lado=64) -> bytes:
    from PIL import Image
    salida = BytesIO()
    Image.new("RGB", (lado, lado), color).save(salida, "JPEG")
    return salida.getvalue()


def test_se_puede_editar():
    assert m.se_puede_editar("a.mp3") == m.HAY_MUTAGEN
    assert not m.se_puede_editar("a.webm")


@necesita_ffmpeg
@necesita_mutagen
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".opus"])
def test_escribir_y_leer_etiquetas(tmp_path, ext):
    pytest.importorskip("PIL")
    ruta = generar_audio(tmp_path / f"a{ext}", segundos=2)
    assert m.leer_portada(ruta) is None
    datos = {"titulo": "Título ñ", "artista": "Artista", "album": "Álbum",
             "anio": "2021", "genero": "Pop"}
    portada = _jpeg()
    m.escribir_etiquetas(ruta, datos, portada)
    assert m.leer_etiquetas(ruta) == datos
    assert m.leer_portada(ruta) == portada

    # Un campo vacío borra la etiqueta; la portada se conserva si no se pasa otra
    m.escribir_etiquetas(ruta, dict(datos, genero="", titulo="Otro"))
    assert m.leer_etiquetas(ruta) == dict(datos, genero="", titulo="Otro")
    assert m.leer_portada(ruta) == portada

    # Reemplazar la portada deja solo la nueva
    nueva = _jpeg("blue")
    m.escribir_etiquetas(ruta, {}, nueva)
    assert m.leer_portada(ruta) == nueva


@necesita_ffmpeg
@necesita_mutagen
def test_editar_no_borra_replaygain(tmp_path):
    ruta = generar_audio(tmp_path / "a.mp3", segundos=2)
    vol = m.analizar_volumen(ruta, 2)
    m.escribir_replaygain(ruta, vol)
    m.escribir_etiquetas(ruta, {"titulo": "X"}, _jpeg())
    from mutagen.id3 import ID3
    assert ID3(ruta).getall("TXXX:REPLAYGAIN_TRACK_GAIN")


def test_formato_no_editable(tmp_path):
    (tmp_path / "a.webm").write_bytes(b"x")
    with pytest.raises(Exception):
        m.escribir_etiquetas(tmp_path / "a.webm", {"titulo": "x"})


# ------------------------------------------------------------------ renombrar el archivo
@pytest.mark.parametrize("texto, esperado", [
    ("Canción nueva", "Canción nueva"),
    ("  Canción nueva.mp3 ", "Canción nueva"),       # se quita la extensión si la escribe
    ("Canción nueva.MP3", "Canción nueva"),
    ("AC/DC: Back?", "AC⧸DC： Back？"),              # caracteres prohibidos en Windows
    ("Termina en punto.", "Termina en punto"),        # Windows no admite nombres con punto final
    ("   ", ""),
])
def test_nombre_archivo_valido(texto, esperado):
    assert m.nombre_archivo_valido(texto, ".mp3") == esperado


def test_renombrar_archivo(tmp_path):
    original = tmp_path / "viejo.mp3"
    original.write_bytes(b"audio")
    nueva = m.renombrar_archivo(original, "Nuevo nombre.mp3")
    assert nueva == tmp_path / "Nuevo nombre.mp3" and nueva.read_bytes() == b"audio"
    assert not original.exists()
    assert m.renombrar_archivo(nueva, "Nuevo nombre") == nueva  # mismo nombre: no hace nada


def test_renombrar_no_sobrescribe(tmp_path):
    a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
    a.write_bytes(b"A")
    b.write_bytes(b"B")
    with pytest.raises(FileExistsError):
        m.renombrar_archivo(a, "b")
    assert a.read_bytes() == b"A" and b.read_bytes() == b"B"


def test_renombrar_solo_mayusculas(tmp_path):
    a = tmp_path / "cancion.mp3"
    a.write_bytes(b"x")
    nueva = m.renombrar_archivo(a, "Cancion")
    assert nueva.name == "Cancion.mp3" and [p.name for p in tmp_path.iterdir()] == ["Cancion.mp3"]


def test_renombrar_vacio(tmp_path):
    a = tmp_path / "a.mp3"
    a.write_bytes(b"x")
    with pytest.raises(ValueError):
        m.renombrar_archivo(a, "  .mp3 ")
    assert a.exists()
