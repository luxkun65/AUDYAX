"""Lyrics (LRCLIB) and metadata/covers (MusicBrainz + Cover Art Archive)."""
import functools
import http.server
import io
import threading
import urllib.error
from pathlib import Path

import pytest
from test_engine import make_audio, needs_ffmpeg, needs_mutagen

from audyax.core import config, lyrics, metadata, tags
# Imported here (before conftest replaces them) to test the real functions
from audyax.core.lyrics import find_lyrics as real_find_lyrics
from audyax.core.metadata import search as real_mb_search
from audyax.downloader import engine

LRC = "[ar:Daft Punk]\n[00:01.50] Primera\n[00:03.00][00:10.25] Estribillo\n[00:05:5] Otra\n[01:02] Final\n"


# ==============================================================================
# LYRICS
# ==============================================================================
def test_parse_lrc():
    assert lyrics.parse_lrc(LRC) == [(1.5, "Primera"), (3.0, "Estribillo"), (5.5, "Otra"),
                                       (10.25, "Estribillo"), (62.0, "Final")]
    assert lyrics.parse_lrc("solo texto\nsin marcas") == []
    assert lyrics.parse_lrc(None) == []


def test_current_line():
    lines = lyrics.parse_lrc(LRC)
    assert lyrics.current_line(lines, 0.5) == -1
    assert lyrics.current_line(lines, 1.5) == 0
    assert lyrics.current_line(lines, 9.9) == 2
    assert lyrics.current_line(lines, 500) == 4


def _fake_lrclib(monkeypatch, get=None, search=()):
    requests_made = []

    def ask(path, query_params, timeout=10, proxy=""):
        requests_made.append((path, dict(query_params)))
        return get if path == "get" else list(search)
    monkeypatch.setattr(lyrics, "_request", ask)
    return requests_made


def test_find_lyrics_exact(monkeypatch):
    requests_made = _fake_lrclib(monkeypatch, get={"id": 1, "syncedLyrics": "[00:01.00] Hola", "plainLyrics": "Hola"})
    r = real_find_lyrics("One More Time", "Daft Punk", "Discovery", 320.4)
    assert r["synced"] == "[00:01.00] Hola" and r["text"] == "Hola"
    assert requests_made == [("get", {"track_name": "One More Time", "artist_name": "Daft Punk",
                                "album_name": "Discovery", "duration": 320})]


def test_find_lyrics_by_search(monkeypatch):
    results = [
        {"id": 1, "duration": 200, "plainLyrics": "otra versión", "syncedLyrics": "[00:01] x"},  # duration too far off
        {"id": 2, "duration": 321, "plainLyrics": "sin sincronizar", "syncedLyrics": None},
        {"id": 3, "duration": 319, "plainLyrics": "text", "syncedLyrics": "[00:01.00] sincronizada"},
        {"id": 4, "duration": 320, "plainLyrics": None, "syncedLyrics": None},  # no lyrics
    ]
    _fake_lrclib(monkeypatch, get=None, search=results)
    r = real_find_lyrics("One More Time", "Daft Punk", "", 320)
    assert r["id"] == 3  # the one with a close duration that is also synced
    _fake_lrclib(monkeypatch, get=None, search=[])
    assert real_find_lyrics("x", "y", "", 100) is None
    assert real_find_lyrics("", "y") is None


def test_breaker_trips_after_failures(monkeypatch):
    from audyax.core.services import CircuitBreaker, ServiceUnavailable
    monkeypatch.setattr(lyrics, "breaker", CircuitBreaker(max_failures=2, pause=60))

    class FailingOpener:
        def open(self, *a, **k):
            raise urllib.error.URLError("sin conexión")
    monkeypatch.setattr(lyrics.urllib.request, "build_opener", lambda *a: FailingOpener())
    for _ in range(2):
        with pytest.raises(urllib.error.URLError):
            lyrics._request("get", {"track_name": "x"})
    with pytest.raises(ServiceUnavailable):  # no longer tries to connect
        lyrics._request("get", {"track_name": "x"})


@needs_ffmpeg
@needs_mutagen
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".opus"])
def test_save_and_read_lyrics(tmp_path, ext):
    path = make_audio(tmp_path / f"a{ext}", seconds=1)
    lyric = {"synced": "[00:00.50] Hola\n[00:01.00] Chao", "text": "Hola\nChao"}
    assert lyrics.save_lyrics(path, lyric, "embedded") == ["embedded"]
    assert not lyrics.lrc_path(path).exists()
    assert lyrics.read_lyrics(path) == lyric["synced"]
    assert lyrics.save_lyrics(path, lyric, "both") == ["lrc", "embedded"]
    assert lyrics.lrc_path(path).read_text(encoding="utf-8").strip() == lyric["synced"]
    assert lyrics.save_lyrics(path, lyric, "off") == []
    # without a synced version, the plain text is saved
    lyrics.lrc_path(path).unlink()
    lyrics.save_lyrics(path, {"synced": None, "text": "Solo texto"}, "embedded")
    assert lyrics.read_lyrics(path) == "Solo texto"


# ==============================================================================
# METADATA
# ==============================================================================
def test_normalize_and_match():
    assert metadata.normalize("One More Time (Official Video)") == "one more time"
    assert metadata.normalize("Canción Ñoña [Lyric Video] feat. Otro") == "cancion nona"
    assert metadata._same_name("Daft Punk", "DAFT PUNK")
    assert metadata._same_name("Simon & Garfunkel", "Simon and Garfunkel")
    assert not metadata._same_name("Queen", "Queens of the Stone Age")
    assert not metadata._same_name("", "x")


def _recording(**k):
    base = {
        "id": "rec1", "score": 100, "title": "One More Time", "length": 320357,
        "artist-credit": [{"name": "Daft Punk", "joinphrase": ""}], "first-release-date": "2000-11-30",
        "releases": [
            {"id": "comp", "title": "Grandes Éxitos 2005", "status": "Official", "date": "2005-01-01",
             "release-group": {"id": "rg-comp", "primary-type": "Album", "secondary-types": ["Compilation"]}},
            {"id": "single", "title": "One More Time", "status": "Official", "date": "2000-11-13",
             "release-group": {"id": "rg-single", "primary-type": "Single"}},
            {"id": "disc2", "title": "Discovery", "status": "Official", "date": "2001-03-12",
             "release-group": {"id": "rg-disc", "primary-type": "Album"}},
            {"id": "disc1", "title": "Discovery", "status": "Official", "date": "2001-03-07",
             "release-group": {"id": "rg-disc", "primary-type": "Album"}},
            {"id": "boot", "title": "Pirata", "status": "Bootleg", "date": "1999",
             "release-group": {"id": "rg-boot", "primary-type": "Album"}},
        ]}
    base.update(k)
    return base


def test_choose_prefers_original_album():
    r = metadata.choose([_recording()], "One More Time", "Daft Punk", 320)
    assert r == {"album": "Discovery", "year": "2000", "release_id": "disc1",
                 "release_group_id": "rg-disc", "recording_id": "rec1"}


@pytest.mark.parametrize("changed, title, artist, duration", [
    ({"score": 60}, "One More Time", "Daft Punk", 320),                       # low score
    ({}, "One More Time", "Otro Artista", 320),                                # another artist
    ({}, "Otra Canción", "Daft Punk", 320),                                    # another title
    ({}, "One More Time", "Daft Punk", 200),                                   # very different duration
    ({"releases": []}, "One More Time", "Daft Punk", 320),                     # no releases
])
def test_choose_is_conservative(changed, title, artist, duration):
    assert metadata.choose([_recording(**changed)], title, artist, duration) is None


def test_mb_search_queries_and_caches(monkeypatch):
    queries = []

    def ask(path, query_params, timeout=15, proxy=""):
        queries.append(query_params["query"])
        return {"recordings": [_recording()]}
    monkeypatch.setattr(metadata, "_request_mb", ask)
    monkeypatch.setattr(metadata, "_cache", {})
    r = real_mb_search("One More Time (Official Video)", "Daft Punk", 320)
    assert r["album"] == "Discovery"
    assert queries == ['recording:"One More Time" AND artist:"Daft Punk"']
    real_mb_search("One More Time (Official Video)", "Daft Punk", 321)
    assert len(queries) == 1  # second time: from the cache
    assert real_mb_search("", "Daft Punk") is None


def test_escape_special_characters():
    assert metadata._escape('AC/DC: "Hells" (Bells)') == 'AC\\/DC\\: \\"Hells\\" \\(Bells\\)'


def test_cover_urls_and_poor_data():
    assert metadata.cover_urls({"release_group_id": "g", "release_id": "r"}, 500) == [
        "https://coverartarchive.org/release-group/g/front-500",
        "https://coverartarchive.org/release/r/front-500"]
    assert metadata.cover_urls(None) == []
    assert metadata.poor_data({"album": "", "release_year": 2001})
    assert metadata.poor_data({"album": "Discovery"})
    assert not metadata.poor_data({"album": "Discovery", "release_year": 2001})


# ==============================================================================
# END TO END (real download from a local server)
# ==============================================================================
@pytest.fixture
def server(tmp_path_factory, monkeypatch):
    from PIL import Image
    root = tmp_path_factory.mktemp("srv")
    make_audio(root / "cancion.m4a", seconds=2)
    Image.new("RGB", (300, 300), (200, 30, 30)).save(root / "portada_album.jpg")
    request_handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    request_handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), request_handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _download(base, folder, **cfg_extra):
    events = []
    cfg = dict(config.DEFAULT_CONFIG, folder=str(folder), audio_format="mp3", replaygain=False, **cfg_extra)
    engine.run_job(f"{base}/cancion.m4a", cfg, events.append, threading.Event())
    done = [e[2] for e in events if e[0] == "done"]
    assert done, events
    return done[0], events


@needs_ffmpeg
@needs_mutagen
@pytest.mark.parametrize("first_exists", [True, False])
def test_musicbrainz_fills_album_year_and_cover(server, tmp_path, monkeypatch, first_exists):
    from PIL import Image
    monkeypatch.setattr(metadata, "search", lambda *a, **k: {
        "album": "Discovery", "year": "2001", "release_id": "r", "release_group_id": "g", "recording_id": "x"})
    # The "album cover" comes from the local server; if the first option doesn't exist (404),
    # the next one is used
    urls = ([f"{server}/portada_album.jpg"] if first_exists
            else [f"{server}/no_existe.jpg", f"{server}/portada_album.jpg"])
    monkeypatch.setattr(metadata, "cover_urls", lambda found, file_size=1200: urls)
    done, events = _download(server, tmp_path, musicbrainz=True, caa_cover=True)
    assert (done["album"], done["year"]) == ("Discovery", "2001")
    assert done.get("musicbrainz") is True
    assert tags.read_tags(done["path"])["year"] == "2001"  # the year is also stored IN the file
    cover = Image.open(io.BytesIO(tags.read_cover(done["path"])))
    assert cover.getpixel((150, 150))[0] > 150  # it's the red "album" one
    assert ("status", "cancion", "Buscando álbum en MusicBrainz…") in events


@needs_ffmpeg
def test_musicbrainz_off_does_not_query(server, tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(metadata, "search", lambda *a, **k: called.append(1))
    _download(server, tmp_path, musicbrainz=False)
    assert not called


@needs_ffmpeg
def test_musicbrainz_down_does_not_break_download(server, tmp_path, monkeypatch):
    def down(*a, **k):
        raise urllib.error.URLError("sin conexión")
    monkeypatch.setattr(metadata, "search", down)
    done, _ = _download(server, tmp_path, musicbrainz=True)
    assert done["path"] and Path(done["path"]).exists()


@needs_ffmpeg
@needs_mutagen
def test_download_with_lyrics(server, tmp_path, monkeypatch):
    requested = []
    monkeypatch.setattr(lyrics, "find_lyrics", lambda *a, **k: requested.append(a) or {
        "synced": "[00:00.50] Hola", "text": "Hola", "instrumental": False})
    done, events = _download(server, tmp_path, lyrics="both")
    assert done["lyrics"] == "synced"
    assert lyrics.lrc_path(done["path"]).exists()
    assert lyrics.read_lyrics(done["path"]) == "[00:00.50] Hola"
    assert ("status", "cancion", "Buscando letra…") in events


@needs_ffmpeg
def test_lyrics_off_by_default(server, tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(lyrics, "find_lyrics", lambda *a, **k: called.append(1))
    assert config.DEFAULT_CONFIG["lyrics"] == "off"
    done, _ = _download(server, tmp_path)
    assert not called and "lyrics" not in done
