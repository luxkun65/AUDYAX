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

import engine as m

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg no está instalado")
needs_mutagen = pytest.mark.skipif(not m.HAS_MUTAGEN, reason="mutagen no está instalado")


def make_audio(path: Path, volume_db: float = -20.0, seconds: int = 6):
    """Crea un archivo de audio de prueba (un tono) con FFmpeg."""
    codecs = {".m4a": ["-c:a", "aac", "-b:a", "128k"], ".mp3": ["-c:a", "libmp3lame"],
              ".opus": ["-c:a", "libopus"]}
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}", "-af", f"volume={volume_db}dB",
         *codecs[path.suffix], str(path)],
        check=True)
    return path


# ------------------------------------------------------------------ funciones puras
def test_tags_use_upload_year_without_release():
    d = m.tags({"title": "T", "artist": "A", "upload_date": "20190305",
                     "genres": ["Rock"], "webpage_url": "https://x"})
    assert d == {"title": "T", "artist": "A", "album": "", "year": "2019",
                 "genre": "Rock", "url": "https://x"}


def test_empty_tags():
    assert m.tags({})["year"] == ""


def test_album_gain_with_equal_tracks():
    p = {"lufs": -10.0, "peak": 0.5, "gain": -8.0, "dur": 100}
    album = m.album_gain([p, dict(p, peak=0.9)])
    assert album["lufs"] == pytest.approx(-10.0)
    assert album["gain"] == pytest.approx(m.REFERENCE_LUFS + 10.0)
    assert album["peak"] == 0.9


def test_album_gain_weights_by_duration():
    long_text = {"lufs": -10.0, "peak": 1, "dur": 300}
    short = {"lufs": -20.0, "peak": 1, "dur": 10}
    assert -11 < m.album_gain([long_text, short])["lufs"] < -10


def test_rg_text():
    assert m.rg_text({"gain": -3.456}) == "pista -3.46 dB"
    assert m.rg_text({"gain": 1}, {"gain": 2}) == "pista 1.00 dB · álbum 2.00 dB"


def test_should_normalize():
    assert m.should_normalize({"normalize": True, "audio_format": "m4a"})
    assert not m.should_normalize({"normalize": True, "audio_format": "original"})
    assert not m.should_normalize({"normalize": False, "audio_format": "mp3"})


def test_relative_subfolder(tmp_path):
    assert m.relative_subfolder(tmp_path, tmp_path) == ""
    assert m.relative_subfolder(tmp_path / "Mi lista", tmp_path) == "Mi lista"
    assert m.relative_subfolder(Path("/otro/sitio"), tmp_path) == ""


# ------------------------------------------------------------------ nombres de archivo
def test_unique_name_without_conflict(tmp_path):
    assert m.unique_name(tmp_path, "Canción", "Artista") == "Canción"
    assert m.unique_name(tmp_path / "no_existe", "Canción", "Artista") == "Canción"


def test_unique_name_adds_artist(tmp_path):
    (tmp_path / "Canción.mp3").touch()
    assert m.unique_name(tmp_path, "Canción", "Artista") == "Canción (Artista)"


def test_unique_name_no_overwrite_when_artist_version_exists(tmp_path):
    (tmp_path / "Canción.mp3").touch()
    (tmp_path / "Canción (Artista).mp3").touch()
    (tmp_path / "Canción (Artista) 2.mp3").touch()
    assert m.unique_name(tmp_path, "Canción", "Artista") == "Canción (Artista) 3"


def test_unique_name_ignores_case(tmp_path):
    (tmp_path / "canción.mp3").touch()
    assert m.unique_name(tmp_path, "CANCIÓN", "X") == "CANCIÓN (X)"


# ------------------------------------------------------------------ opciones y config
def test_build_options(tmp_path):
    cfg = dict(m.DEFAULT_CONFIG, normalize=True, audio_format="m4a")
    op = m.build_options(cfg, tmp_path, tmp_path / "song_list", None, None, None)
    assert "postprocessors" not in op  # se añaden con agregar_postprocesadores
    assert op["download_archive"] == str(tmp_path / "descargados.txt")
    assert op["outtmpl"]["default"].startswith(str(tmp_path / "song_list"))
    assert "loudnorm" in " ".join(op["postprocessor_args"]["extractaudio+ffmpeg_o"])

    cfg["normalize"] = False
    op = m.build_options(cfg, tmp_path, tmp_path, None, None, None)
    assert "extractaudio+ffmpeg_o" not in op["postprocessor_args"]


def test_add_postprocessors_in_order(tmp_path):
    cfg = dict(m.DEFAULT_CONFIG, normalize=True, audio_format="m4a")
    with YoutubeDL({"quiet": True}) as ydl:
        m.add_postprocessors(ydl, cfg, tmp_path, lambda e: None, None, [])
        names = [pp.pp_key() for pp in ydl._pps["post_process"]]
        extract = ydl._pps["post_process"][0]
    assert names == ["ExtractAudio", "Metadata", "EmbedThumbnail"]
    assert isinstance(extract, m.ExtractAudio) and extract.force


def test_save_config_skips_job_data(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CONFIG_PATH", tmp_path / "cfg.json")
    m.save_config(dict(m.DEFAULT_CONFIG, subfolder="Lista", audio_format="opus"))
    saved = json.loads((tmp_path / "cfg.json").read_text(encoding="utf-8"))
    assert "subfolder" not in saved
    assert m.load_config()["audio_format"] == "opus"


def test_ids_in_archive(tmp_path):
    (tmp_path / "descargados.txt").write_text("youtube abc\nyoutube def\n\n", encoding="utf-8")
    assert m.ids_in_archive(tmp_path) == {"abc", "def"}
    assert m.ids_in_archive(tmp_path / "nada") == set()


# ------------------------------------------------------------------ con FFmpeg
def _extract(path: Path, force: bool, normalize: bool):
    params = {"quiet": True}
    if normalize:
        params["postprocessor_args"] = {
            "extractaudio+ffmpeg_o": ["-af", "loudnorm=I=-14:TP=-1:LRA=11"]}
    with YoutubeDL(params) as ydl:
        pp = m.ExtractAudio(ydl, force=force, preferredcodec="m4a", preferredquality="192")
        _, info = pp.run({"filepath": str(path), "ext": path.suffix[1:], "id": "x"})
    return Path(info["filepath"])


@needs_ffmpeg
def test_m4a_without_normalization_is_copied_untouched(tmp_path):
    path = make_audio(tmp_path / "a.m4a")
    before = path.read_bytes()
    assert _extract(path, force=False, normalize=False).read_bytes() == before


@needs_ffmpeg
def test_normalized_m4a_changes_volume(tmp_path):
    """Antes del arreglo la normalización se ignoraba en m4a (el audio se copiaba)."""
    path = make_audio(tmp_path / "a.m4a", volume_db=-25)
    before = m.analyze_loudness(path)["lufs"]
    later = m.analyze_loudness(_extract(path, force=True, normalize=True))["lufs"]
    assert before < -24
    assert later == pytest.approx(-14, abs=1.5)


@needs_ffmpeg
@needs_mutagen
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".opus"])
def test_replaygain_and_track_number(tmp_path, ext):
    from mutagen import File as MutagenFile
    path = make_audio(tmp_path / f"a{ext}")
    vol = m.analyze_loudness(path, 6)
    assert vol is not None
    m.write_replaygain(path, vol, m.album_gain([vol]))
    tags = {k.lower() for k in (MutagenFile(path).tags or {}).keys()}
    if ext == ".opus":
        assert {"r128_track_gain", "r128_album_gain"} <= tags
    else:
        assert any("replaygain_track_gain" in k for k in tags)
        assert any("replaygain_album_gain" in k for k in tags)
    m.strip_track_number(path)  # no debe fallar aunque no haya número de pista


def test_file_info_missing_file(tmp_path):
    d = m.file_info(tmp_path / "x.mp3")
    assert d["audio_format"] == "MP3" and d["size"] == ""
