"""Tests for search, yt-dlp updates, link detection and the tag editor."""
import subprocess
from io import BytesIO
from types import SimpleNamespace

import pytest
from test_engine import make_audio, needs_ffmpeg, needs_mutagen

from audyax.core import lyrics, replaygain, tags
from audyax.downloader import updates, youtube


# ------------------------------------------------------------------ links
@pytest.mark.parametrize("text", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=abc&list=xyz",
    "https://youtube.com/playlist?list=PL123",
    "https://youtu.be/dQw4w9WgXcQ",
    "http://m.youtube.com/shorts/abc",
    "  https://www.youtube.com/watch?v=x  ",
])
def test_is_youtube_link(text):
    assert youtube.is_youtube_link(text)


@pytest.mark.parametrize("text", [
    "", "daft punk", "https://google.com", "https://www.youtube.com/",
    "mira esto https://youtu.be/abc", "https://notyoutube.com/watch?v=x", None,
])
def test_not_youtube_link(text):
    assert not youtube.is_youtube_link(text)


def test_extract_links_from_drop():
    data = ("{https://www.youtube.com/watch?v=aaa} https://youtu.be/bbb\n"
             "https://google.com https://youtu.be/bbb \"https://music.youtube.com/watch?v=ccc\".")
    assert youtube.extract_links(data) == [
        "https://www.youtube.com/watch?v=aaa", "https://youtu.be/bbb",
        "https://music.youtube.com/watch?v=ccc"]
    assert youtube.extract_links("") == []


# ------------------------------------------------------------------ search
def test_duration_text():
    assert youtube._duration_text(65) == "1:05"
    assert youtube._duration_text(3725) == "1:02:05"
    assert youtube._duration_text(None) == ""


def test_search_builds_results(monkeypatch):
    request_info = {}

    class FakeYDL:
        def __init__(self, options):
            request_info["options"] = options

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            request_info["url"] = url
            return {"entries": [
                {"id": "abc", "title": "One More Time", "channel": "Daft Punk - Topic",
                 "duration": 320, "url": "https://www.youtube.com/watch?v=abc", "ie_key": "Youtube"},
                {"id": "channel", "title": "Un canal", "ie_key": "YoutubeTab"},  # not a song
                None,
            ]}

    monkeypatch.setattr(youtube, "YoutubeDL", FakeYDL)
    res = youtube.search("daft punk one more time", 5)
    assert request_info["url"] == "ytsearch5:daft punk one more time"
    assert request_info["options"]["extract_flat"] == "in_playlist"
    assert res == [{"id": "abc", "title": "One More Time", "url": "https://www.youtube.com/watch?v=abc",
                    "thumbnail": "https://i.ytimg.com/vi/abc/mqdefault.jpg",
                    "canal": "Daft Punk", "duration": "5:20"}]


def test_search_empty_text_does_not_query():
    assert youtube.search("   ") == []


# ------------------------------------------------------------------ yt-dlp
def test_is_newer():
    assert updates.is_newer("2026.11.10", "2026.10.26")
    assert updates.is_newer("2026.10.26.1", "2026.10.26")
    assert not updates.is_newer("2026.10.26", "2026.10.26")
    assert not updates.is_newer("2025.12.31", "2026.01.01")


def test_ytdlp_version():
    assert updates.is_newer(updates.ytdlp_version(), "2000.1.1")


def test_update_in_executable_does_not_use_pip(monkeypatch):
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates.subprocess, "run", lambda *a, **k: pytest.fail("no debe llamar a pip"))
    ok, message = updates.update_ytdlp()
    assert not ok and "empaquetada" in message


def test_update_calls_pip(monkeypatch):
    called = {}

    def fake_run(cmd, **kw):
        called["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr(updates.subprocess, "run", fake_run)
    assert updates.update_ytdlp() == (True, "yt-dlp se actualizó correctamente.")
    assert called["cmd"][1:6] == ["-m", "pip", "install", "-U", "yt-dlp"]
    assert ("ytmusicapi" in called["cmd"]) == youtube.HAS_YTMUSIC


def test_update_reports_pip_error(monkeypatch):
    monkeypatch.setattr(updates.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=1, stdout="", stderr="línea 1\nERROR: sin permisos"))
    ok, message = updates.update_ytdlp()
    assert not ok and "sin permisos" in message


def test_update_without_python(monkeypatch):
    def fails(*a, **k):
        raise subprocess.TimeoutExpired("pip", 600)
    monkeypatch.setattr(updates.subprocess, "run", fails)
    assert updates.update_ytdlp()[0] is False


# ------------------------------------------------------------------ tag editor
def _jpeg(color="red", side_px=64) -> bytes:
    from PIL import Image
    output = BytesIO()
    Image.new("RGB", (side_px, side_px), color).save(output, "JPEG")
    return output.getvalue()


def test_is_editable():
    assert tags.is_editable("a.mp3") == lyrics.HAS_MUTAGEN
    assert not tags.is_editable("a.webm")


@needs_ffmpeg
@needs_mutagen
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".opus"])
def test_write_and_read_tags(tmp_path, ext):
    pytest.importorskip("PIL")
    path = make_audio(tmp_path / f"a{ext}", seconds=2)
    assert tags.read_cover(path) is None
    data = {"title": "Título ñ", "artist": "Artista", "album": "Álbum",
             "year": "2021", "genre": "Pop"}
    cover = _jpeg()
    tags.write_tags(path, data, cover)
    assert tags.read_tags(path) == data
    assert tags.read_cover(path) == cover

    # An empty field removes the tag; the cover is kept if no other one is given
    tags.write_tags(path, dict(data, genre="", title="Otro"))
    assert tags.read_tags(path) == dict(data, genre="", title="Otro")
    assert tags.read_cover(path) == cover

    # Replacing the cover leaves only the new one
    new_cover = _jpeg("blue")
    tags.write_tags(path, {}, new_cover)
    assert tags.read_cover(path) == new_cover


@needs_ffmpeg
@needs_mutagen
def test_editing_keeps_replaygain(tmp_path):
    path = make_audio(tmp_path / "a.mp3", seconds=2)
    vol = replaygain.analyze_loudness(path, 2)
    replaygain.write_replaygain(path, vol)
    tags.write_tags(path, {"title": "X"}, _jpeg())
    from mutagen.id3 import ID3
    assert ID3(path).getall("TXXX:REPLAYGAIN_TRACK_GAIN")


def test_non_editable_format(tmp_path):
    (tmp_path / "a.webm").write_bytes(b"x")
    with pytest.raises(Exception):
        tags.write_tags(tmp_path / "a.webm", {"title": "x"})


# ------------------------------------------------------------------ renaming the file
@pytest.mark.parametrize("text, expected", [
    ("Canción nueva", "Canción nueva"),
    ("  Canción nueva.mp3 ", "Canción nueva"),       # the extension is removed if typed
    ("Canción nueva.MP3", "Canción nueva"),
    ("AC/DC: Back?", "AC⧸DC： Back？"),              # characters forbidden on Windows
    ("Termina en punto.", "Termina en punto"),        # Windows doesn't allow names ending in a dot
    ("   ", ""),
])
def test_valid_filename(text, expected):
    assert tags.valid_filename(text, ".mp3") == expected


def test_rename_file(tmp_path):
    original = tmp_path / "viejo.mp3"
    original.write_bytes(b"audio")
    new_path = tags.rename_file(original, "Nuevo nombre.mp3")
    assert new_path == tmp_path / "Nuevo nombre.mp3" and new_path.read_bytes() == b"audio"
    assert not original.exists()
    assert tags.rename_file(new_path, "Nuevo nombre") == new_path  # same name: does nothing


def test_rename_does_not_overwrite(tmp_path):
    a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
    a.write_bytes(b"A")
    b.write_bytes(b"B")
    with pytest.raises(FileExistsError):
        tags.rename_file(a, "b")
    assert a.read_bytes() == b"A" and b.read_bytes() == b"B"


def test_rename_case_only(tmp_path):
    a = tmp_path / "cancion.mp3"
    a.write_bytes(b"x")
    new_path = tags.rename_file(a, "Cancion")
    assert new_path.name == "Cancion.mp3" and [p.name for p in tmp_path.iterdir()] == ["Cancion.mp3"]


def test_rename_empty(tmp_path):
    a = tmp_path / "a.mp3"
    a.write_bytes(b"x")
    with pytest.raises(ValueError):
        tags.rename_file(a, "  .mp3 ")
    assert a.exists()
