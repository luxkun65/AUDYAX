"""Watched playlists: detect new songs and download only those."""
import threading
from datetime import datetime, timedelta

import pytest

from audyax.core import config
from audyax.downloader import engine
from audyax.downloader import watched as v


@pytest.mark.parametrize("url, expected", [
    ("https://www.youtube.com/playlist?list=PL123", True),
    ("https://music.youtube.com/playlist?list=OLAK5uy_abc", True),
    ("https://music.youtube.com/browse/MPREb_abc", True),
    ("https://www.youtube.com/watch?v=x&list=PL123", True),
    ("https://music.youtube.com/watch?v=x&list=RDAMVMx", False),   # automatic radio
    ("https://www.youtube.com/watch?v=x", False),
    ("https://example.com/playlist?list=PL1", False),
    ("", False),
])
def test_is_playlist(url, expected):
    assert v.is_playlist(url) is expected


def test_canonical_playlist_url():
    assert v.playlist_url("https://www.youtube.com/watch?v=x&list=PL123&index=4") == \
        "https://www.youtube.com/playlist?list=PL123"
    assert v.playlist_url("https://music.youtube.com/playlist?list=PL9&si=abc") == \
        "https://music.youtube.com/playlist?list=PL9"
    assert v.playlist_url("https://music.youtube.com/browse/MPREb_abc?x=1") == \
        "https://music.youtube.com/browse/MPREb_abc"


def test_add_and_remove(tmp_path):
    watch_list = []
    a = v.add(watch_list, "https://www.youtube.com/watch?v=x&list=PL1", tmp_path)
    b = v.add(watch_list, "https://www.youtube.com/playlist?list=PL1", tmp_path)  # the same one
    assert a is b and len(watch_list) == 1
    with pytest.raises(ValueError):
        v.add(watch_list, "https://www.youtube.com/watch?v=solo", tmp_path)
    v.remove(watch_list, a["url"])
    assert watch_list == []


def _plan(entries, is_playlist=True, title="Mi Lista"):
    return lambda url, folder, sub="", log=None, net=None: {
        "dest": folder / title, "entries": entries, "is_playlist": is_playlist,
        "urls": [e["url"] for e in entries], "extras": {}, "default_album": title, "per_song": True}


def _entry(i):
    return {"id": f"id{i}", "title": f"T{i}", "url": f"https://www.youtube.com/watch?v=id{i}", "thumbnail": ""}


def test_check_finds_new_songs(tmp_path, monkeypatch):
    (tmp_path / "descargados.txt").write_text("youtube id1\nyoutube id3\n", encoding="utf-8")
    monkeypatch.setattr(engine, "resolve", _plan([_entry(i) for i in range(1, 5)]))
    watch = v.create("https://www.youtube.com/playlist?list=PL1", tmp_path)
    new_songs = v.check(watch)
    assert [e["id"] for e in new_songs] == ["id2", "id4"]
    assert (watch["name"], watch["total"], watch["new_count"], watch["error"]) == ("Mi Lista", 4, 2, "")
    assert "4 canciones · 2 nuevas · revisada hace un momento" == v.check_summary(watch)


def test_check_with_error(tmp_path, monkeypatch):
    def fails(*a, **k):
        raise ConnectionError("sin internet")
    monkeypatch.setattr(engine, "resolve", fails)
    watch = v.create("https://www.youtube.com/playlist?list=PL1", tmp_path)
    assert v.check(watch) == [] and "sin internet" in watch["error"]
    assert v.check_summary(watch).startswith("No se pudo revisar")
    monkeypatch.setattr(engine, "resolve", _plan([], is_playlist=True))
    assert v.check(watch) == [] and "privada" in watch["error"]


def test_time_ago():
    now = datetime(2026, 11, 17, 12, 0, 0)
    assert v.time_ago((now - timedelta(seconds=10)).isoformat(), now) == "hace un momento"
    assert v.time_ago((now - timedelta(minutes=5)).isoformat(), now) == "hace 5 min"
    assert v.time_ago((now - timedelta(hours=3)).isoformat(), now) == "hace 3 h"
    assert v.time_ago((now - timedelta(days=1)).isoformat(), now) == "hace 1 día"
    assert v.time_ago((now - timedelta(days=4)).isoformat(), now) == "hace 4 días"
    assert v.time_ago("no es fecha", now) == ""


def test_only_new_skips_existing_songs(tmp_path, monkeypatch):
    (tmp_path / "descargados.txt").write_text("youtube id1\n", encoding="utf-8")
    monkeypatch.setattr(engine, "resolve", _plan([_entry(1), _entry(2)]))
    requested = []

    class Fake:
        def __init__(self, params):
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def add_post_processor(self, *a, **k):
            pass

        def download(self, urls):
            requested.extend(urls)
    monkeypatch.setattr(engine, "YoutubeDL", Fake)
    events = []
    cfg = dict(config.DEFAULT_CONFIG, folder=str(tmp_path), only_new=True)
    engine.run_job("https://www.youtube.com/playlist?list=PL1", cfg, events.append, threading.Event())
    assert [e[1] for e in events if e[0] == "new"] == ["id2"]
    assert not [e for e in events if e[0] == "skipped"]
    assert requested == ["https://www.youtube.com/watch?v=id2"]
    assert ("playlist", "https://www.youtube.com/playlist?list=PL1", "Mi Lista") in events
