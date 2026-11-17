"""Pruebas del motor de descarga (no necesitan internet).

Las que generan audio de prueba necesitan FFmpeg; si no está instalado se saltan.
Ejecutar con:  python -m pytest
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from yt_dlp import YoutubeDL

import motor_descarga as m

HAY_FFMPEG = shutil.which("ffmpeg") is not None
necesita_ffmpeg = pytest.mark.skipif(not HAY_FFMPEG, reason="FFmpeg no está instalado")
necesita_mutagen = pytest.mark.skipif(not m.HAY_MUTAGEN, reason="mutagen no está instalado")


def generar_audio(ruta: Path, volumen_db: float = -20.0, segundos: int = 6):
    """Crea un archivo de audio de prueba (un tono) con FFmpeg."""
    codecs = {".m4a": ["-c:a", "aac", "-b:a", "128k"], ".mp3": ["-c:a", "libmp3lame"],
              ".opus": ["-c:a", "libopus"]}
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={segundos}", "-af", f"volume={volumen_db}dB",
         *codecs[ruta.suffix], str(ruta)],
        check=True)
    return ruta


# ------------------------------------------------------------------ funciones puras
def test_etiquetas_usa_anio_de_subida_si_no_hay_lanzamiento():
    d = m.etiquetas({"title": "T", "artist": "A", "upload_date": "20190305",
                     "genres": ["Rock"], "webpage_url": "https://x"})
    assert d == {"titulo": "T", "artista": "A", "album": "", "anio": "2019",
                 "genero": "Rock", "url": "https://x"}


def test_etiquetas_vacias():
    assert m.etiquetas({})["anio"] == ""


def test_calcular_album_con_pistas_iguales():
    p = {"lufs": -10.0, "pico": 0.5, "gain": -8.0, "dur": 100}
    album = m.calcular_album([p, dict(p, pico=0.9)])
    assert album["lufs"] == pytest.approx(-10.0)
    assert album["gain"] == pytest.approx(m.REFERENCIA_LUFS + 10.0)
    assert album["pico"] == 0.9


def test_calcular_album_pondera_por_duracion():
    larga = {"lufs": -10.0, "pico": 1, "dur": 300}
    corta = {"lufs": -20.0, "pico": 1, "dur": 10}
    assert -11 < m.calcular_album([larga, corta])["lufs"] < -10


def test_texto_rg():
    assert m.texto_rg({"gain": -3.456}) == "pista -3.46 dB"
    assert m.texto_rg({"gain": 1}, {"gain": 2}) == "pista 1.00 dB · álbum 2.00 dB"


def test_debe_normalizar():
    assert m.debe_normalizar({"normalizar": True, "formato": "m4a"})
    assert not m.debe_normalizar({"normalizar": True, "formato": "original"})
    assert not m.debe_normalizar({"normalizar": False, "formato": "mp3"})


def test_subcarpeta_relativa(tmp_path):
    assert m.subcarpeta_relativa(tmp_path, tmp_path) == ""
    assert m.subcarpeta_relativa(tmp_path / "Mi lista", tmp_path) == "Mi lista"
    assert m.subcarpeta_relativa(Path("/otro/sitio"), tmp_path) == ""


# ------------------------------------------------------------------ nombres de archivo
def test_nombre_unico_sin_conflicto(tmp_path):
    assert m.nombre_unico(tmp_path, "Canción", "Artista") == "Canción"
    assert m.nombre_unico(tmp_path / "no_existe", "Canción", "Artista") == "Canción"


def test_nombre_unico_agrega_artista(tmp_path):
    (tmp_path / "Canción.mp3").touch()
    assert m.nombre_unico(tmp_path, "Canción", "Artista") == "Canción (Artista)"


def test_nombre_unico_no_sobrescribe_si_tambien_existe_con_artista(tmp_path):
    (tmp_path / "Canción.mp3").touch()
    (tmp_path / "Canción (Artista).mp3").touch()
    (tmp_path / "Canción (Artista) 2.mp3").touch()
    assert m.nombre_unico(tmp_path, "Canción", "Artista") == "Canción (Artista) 3"


def test_nombre_unico_ignora_mayusculas(tmp_path):
    (tmp_path / "canción.mp3").touch()
    assert m.nombre_unico(tmp_path, "CANCIÓN", "X") == "CANCIÓN (X)"


# ------------------------------------------------------------------ opciones y config
def test_construir_opciones(tmp_path):
    cfg = dict(m.CONFIG_POR_DEFECTO, normalizar=True, formato="m4a")
    op = m.construir_opciones(cfg, tmp_path, tmp_path / "lista", None, None, None)
    assert "postprocessors" not in op  # se añaden con agregar_postprocesadores
    assert op["download_archive"] == str(tmp_path / "descargados.txt")
    assert op["outtmpl"]["default"].startswith(str(tmp_path / "lista"))
    assert "loudnorm" in " ".join(op["postprocessor_args"]["extractaudio+ffmpeg_o"])

    cfg["normalizar"] = False
    op = m.construir_opciones(cfg, tmp_path, tmp_path, None, None, None)
    assert "extractaudio+ffmpeg_o" not in op["postprocessor_args"]


def test_agregar_postprocesadores_en_orden(tmp_path):
    cfg = dict(m.CONFIG_POR_DEFECTO, normalizar=True, formato="m4a")
    with YoutubeDL({"quiet": True}) as ydl:
        m.agregar_postprocesadores(ydl, cfg, tmp_path, lambda e: None, None, [])
        nombres = [pp.pp_key() for pp in ydl._pps["post_process"]]
        extraer = ydl._pps["post_process"][0]
    assert nombres == ["ExtractAudio", "Metadata", "EmbedThumbnail"]
    assert isinstance(extraer, m.ExtraerAudio) and extraer.forzar


def test_guardar_config_no_guarda_datos_del_trabajo(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CONFIG_PATH", tmp_path / "cfg.json")
    m.guardar_config(dict(m.CONFIG_POR_DEFECTO, subcarpeta="Lista", formato="opus"))
    guardado = json.loads((tmp_path / "cfg.json").read_text(encoding="utf-8"))
    assert "subcarpeta" not in guardado
    assert m.cargar_config()["formato"] == "opus"


def test_ids_en_archivo(tmp_path):
    (tmp_path / "descargados.txt").write_text("youtube abc\nyoutube def\n\n", encoding="utf-8")
    assert m.ids_en_archivo(tmp_path) == {"abc", "def"}
    assert m.ids_en_archivo(tmp_path / "nada") == set()


# ------------------------------------------------------------------ con FFmpeg
def _extraer(ruta: Path, forzar: bool, normalizar: bool):
    params = {"quiet": True}
    if normalizar:
        params["postprocessor_args"] = {
            "extractaudio+ffmpeg_o": ["-af", "loudnorm=I=-14:TP=-1:LRA=11"]}
    with YoutubeDL(params) as ydl:
        pp = m.ExtraerAudio(ydl, forzar=forzar, preferredcodec="m4a", preferredquality="192")
        _, info = pp.run({"filepath": str(ruta), "ext": ruta.suffix[1:], "id": "x"})
    return Path(info["filepath"])


@necesita_ffmpeg
def test_m4a_sin_normalizar_se_copia_sin_tocar(tmp_path):
    ruta = generar_audio(tmp_path / "a.m4a")
    antes = ruta.read_bytes()
    assert _extraer(ruta, forzar=False, normalizar=False).read_bytes() == antes


@necesita_ffmpeg
def test_m4a_normalizado_cambia_el_volumen(tmp_path):
    """Antes del arreglo la normalización se ignoraba en m4a (el audio se copiaba)."""
    ruta = generar_audio(tmp_path / "a.m4a", volumen_db=-25)
    antes = m.analizar_volumen(ruta)["lufs"]
    despues = m.analizar_volumen(_extraer(ruta, forzar=True, normalizar=True))["lufs"]
    assert antes < -24
    assert despues == pytest.approx(-14, abs=1.5)


@necesita_ffmpeg
@necesita_mutagen
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".opus"])
def test_replaygain_y_numero_de_pista(tmp_path, ext):
    from mutagen import File as MutagenFile
    ruta = generar_audio(tmp_path / f"a{ext}")
    vol = m.analizar_volumen(ruta, 6)
    assert vol is not None
    m.escribir_replaygain(ruta, vol, m.calcular_album([vol]))
    tags = {k.lower() for k in (MutagenFile(ruta).tags or {}).keys()}
    if ext == ".opus":
        assert {"r128_track_gain", "r128_album_gain"} <= tags
    else:
        assert any("replaygain_track_gain" in k for k in tags)
        assert any("replaygain_album_gain" in k for k in tags)
    m.quitar_numero_pista(ruta)  # no debe fallar aunque no haya número de pista


def test_info_archivo_sin_archivo(tmp_path):
    d = m.info_archivo(tmp_path / "x.mp3")
    assert d["formato"] == "MP3" and d["tamano"] == ""
