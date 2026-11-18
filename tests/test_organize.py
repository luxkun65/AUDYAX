"""Organizing downloads into folders (Artist / Album)."""
import functools
import http.server
import threading
from pathlib import Path

import pytest

import engine as m
from test_engine import make_audio, needs_ffmpeg


class _NeverCancelled:
    def is_set(self):
        return False


# ------------------------------------------------------------------ template and mode
def test_does_not_organize_by_default():
    assert m.DEFAULT_CONFIG["organize"] == "flat"
    assert m.DEFAULT_CONFIG["playlists_in_folder"] is True


def test_output_template(tmp_path):
    assert m.output_template(tmp_path) == str(tmp_path / "%(safe_filename)s.%(ext)s")
    assert m.output_template(tmp_path, "artist") == str(
        tmp_path / "%(org_artist)s" / "%(safe_filename)s.%(ext)s")
    assert m.output_template(tmp_path, "artist_album") == str(
        tmp_path / "%(org_artist)s" / "%(org_album)s" / "%(safe_filename)s.%(ext)s")
    assert m.output_template(tmp_path, "inventado") == m.output_template(tmp_path)


@pytest.mark.parametrize("cfg, is_playlist, expected", [
    ({"organize": "flat"}, False, "flat"),
    ({"organize": "artist_album"}, False, "artist_album"),
    ({"organize": "artist_album", "playlists_in_folder": True}, True, "flat"),
    ({"organize": "artist_album", "playlists_in_folder": False}, True, "artist_album"),
    ({"organize": "artist", "subfolder": "Mi Playlist"}, False, "flat"),  # retry
    ({"organize": "cualquier cosa"}, False, "flat"),
    ({}, False, "flat"),
])
def test_organize_mode(cfg, is_playlist, expected):
    assert m.organize_mode(cfg, is_playlist) == expected


def test_yt_dlp_builds_path_and_escapes_slashes(tmp_path):
    from yt_dlp import YoutubeDL
    info = {"id": "x", "ext": "mp3", "title": "T", "org_artist": "AC/DC",
            "org_album": "Back in Black", "safe_filename": "Hells Bells"}
    with YoutubeDL({"outtmpl": {"default": m.output_template(tmp_path, "artist_album")},
                    "quiet": True}) as ydl:
        path = Path(ydl.prepare_filename(info))
    assert path.parent.parent.parent == tmp_path  # exactly two folders, not three
    assert path.parent.name == "Back in Black" and path.name == "Hells Bells.mp3"


def test_clean_metadata_checks_clashes_in_organized_folder(tmp_path):
    (tmp_path / "Intro.mp3").touch()                       # another loose "Intro" in the root
    (tmp_path / "Artista B" / "Disco").mkdir(parents=True)
    (tmp_path / "Artista B" / "Disco" / "Intro.mp3").touch()
    pp = m.CleanMetadata(None, tmp_path, lambda e: None, _NeverCancelled(), organize="artist_album")

    _, info = pp.run({"id": "1", "title": "Intro", "artist": "Artista A", "album": "Disco"})
    assert (info["org_artist"], info["org_album"], info["safe_filename"]) == ("Artista A", "Disco", "Intro")

    _, info = pp.run({"id": "2", "title": "Intro", "artist": "Artista B", "album": "Disco"})
    assert info["safe_filename"] == "Intro (Artista B)"


# ------------------------------------------------------------------ end to end
@pytest.fixture
def server(tmp_path_factory, monkeypatch):
    """Serves an audio file over local HTTP so yt-dlp can 'download' it without internet."""
    root = tmp_path_factory.mktemp("srv")
    make_audio(root / "cancion.m4a", seconds=2)
    request_handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    request_handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), request_handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    yield f"http://127.0.0.1:{httpd.server_address[1]}/cancion.m4a"
    httpd.shutdown()


def _on_download(monkeypatch, url, folder, is_playlist, **cfg_extra):
    def fake_plan(_url, base_folder, sub="", log=None, net=None):
        data = {"title": "One More Time", "artist": "Daft Punk", "album": "Discovery"}
        return {"dest": base_folder / (sub or "Mi Playlist") if is_playlist else base_folder,
                "entries": [dict(data, id="cancion", url=url, thumbnail="")],
                "is_playlist": is_playlist, "urls": [url], "extras": {"cancion": data},
                "default_album": "Mi Playlist" if is_playlist else ""}
    monkeypatch.setattr(m, "resolve", fake_plan)
    events = []
    cfg = dict(m.DEFAULT_CONFIG, folder=str(folder), audio_format="mp3", replaygain=False, **cfg_extra)
    m.run_job(url, cfg, events.append, threading.Event())
    done_songs = [e[2]["path"] for e in events if e[0] == "done"]
    assert len(done_songs) == 1, events
    return Path(done_songs[0]).relative_to(folder)


@needs_ffmpeg
@pytest.mark.parametrize("cfg_extra, is_playlist, expected", [
    ({}, False, "One More Time.mp3"),
    ({"organize": "artist"}, False, "Daft Punk/One More Time.mp3"),
    ({"organize": "artist_album"}, False, "Daft Punk/Discovery/One More Time.mp3"),
    ({"organize": "artist_album"}, True, "Mi Playlist/One More Time.mp3"),
    ({"organize": "artist_album", "playlists_in_folder": False}, True,
     "Daft Punk/Discovery/One More Time.mp3"),
])
def test_organized_download(server, tmp_path, monkeypatch, cfg_extra, is_playlist, expected):
    path = _on_download(monkeypatch, server, tmp_path, is_playlist, **cfg_extra)
    assert path == Path(expected)
