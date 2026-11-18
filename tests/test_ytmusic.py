"""Playlists de YouTube Music: versiones sustituidas y enlaces de "radio"."""
from pathlib import Path

import pytest

import engine as m


# ------------------------------------------------------------------ limpiar_url
@pytest.mark.parametrize("entry, expected", [
    # Radio automática de YouTube Music: solo la canción
    ("https://music.youtube.com/watch?v=bDybRgaVD2Y&list=RDAMVMbDybRgaVD2Y",
     "https://www.youtube.com/watch?v=bDybRgaVD2Y"),
    ("https://www.youtube.com/watch?v=abc&list=RDabc&start_radio=1",
     "https://www.youtube.com/watch?v=abc"),
    # Canción suelta de YouTube Music -> www
    ("https://music.youtube.com/watch?v=bDybRgaVD2Y", "https://www.youtube.com/watch?v=bDybRgaVD2Y"),
    ("https://music.youtube.com/watch?v=bDybRgaVD2Y&si=xyz", "https://www.youtube.com/watch?v=bDybRgaVD2Y"),
    # Canción dentro de una playlist real: se mantiene la playlist
    ("https://www.youtube.com/watch?v=abc&list=PL123&index=3",
     "https://www.youtube.com/watch?v=abc&list=PL123"),
    ("https://music.youtube.com/watch?v=abc&list=PL123", "https://music.youtube.com/watch?v=abc&list=PL123"),
    # Otras cosas no se tocan
    ("https://music.youtube.com/playlist?list=PL123", "https://music.youtube.com/playlist?list=PL123"),
    ("https://youtu.be/abc", "https://youtu.be/abc"),
    ("https://example.com/watch?v=abc", "https://example.com/watch?v=abc"),
])
def test_clean_url(entry, expected):
    assert m.clean_url(entry) == expected


@pytest.mark.parametrize("url, expected", [
    ("https://music.youtube.com/playlist?list=PL123", True),
    ("https://music.youtube.com/playlist?list=OLAK5uy_abc", True),
    ("https://music.youtube.com/browse/MPREb_abc", True),
    ("https://music.youtube.com/watch?v=abc&list=PL123", True),
    ("https://music.youtube.com/watch?v=abc&list=RDAMVMabc", False),
    ("https://music.youtube.com/watch?v=abc", False),
    ("https://www.youtube.com/playlist?list=PL123", False),
])
def test_is_ytmusic_playlist(url, expected):
    assert m.is_ytmusic_playlist(url) is expected


# ------------------------------------------------------------------ pistas_ytmusic
class FakeClient:
    """Imita las respuestas de ytmusicapi (YTMusic.get_playlist / get_album)."""

    def __init__(self):
        self.requests_made = []

    def get_playlist(self, playlist_id, limit=100):
        self.requests_made.append(("playlist", playlist_id, limit))
        return {"title": "Mi Playlist", "tracks": [
            # YouTube Music reproduce bDybRgaVD2Y aunque la playlist guarda QQD-OvbCfCU
            {"videoId": "bDybRgaVD2Y", "title": "Canción", "isAvailable": True,
             "artists": [{"name": "Artista A"}, {"name": "Artista B"}],
             "album": {"name": "Disco", "id": "MPRE1"},
             "thumbnails": [{"url": "chica", "width": 60}, {"url": "big", "width": 544}]},
            {"videoId": "gris0000001", "title": "No disponible", "isAvailable": False,
             "artists": [{"name": "X"}], "album": None, "thumbnails": []},
            {"videoId": None, "title": "Sin id"},
        ]}

    def get_album(self, browse_id):
        self.requests_made.append(("album", browse_id))
        return {"title": "Álbum Z", "tracks": [
            {"videoId": "t1", "title": "Uno", "artists": [{"name": "Z"}], "album": "Álbum Z"},
            {"videoId": "t2", "title": "Dos", "artists": [{"name": "Z"}]},
        ]}


def test_ytmusic_tracks_use_version_youtube_music_plays():
    client = FakeClient()
    title, tracks = m.ytmusic_tracks("https://music.youtube.com/playlist?list=PL123&si=x", client)
    assert client.requests_made == [("playlist", "PL123", None)]  # None = todas las canciones
    assert title == "Mi Playlist"
    assert [p["id"] for p in tracks] == ["bDybRgaVD2Y", "gris0000001"]
    assert tracks[0] == {
        "id": "bDybRgaVD2Y", "title": "Canción", "artist": "Artista A, Artista B",
        "album": "Disco", "url": "https://music.youtube.com/watch?v=bDybRgaVD2Y",
        "thumbnail": "big", "available": True}
    assert tracks[1]["available"] is False
    assert tracks[1]["thumbnail"] == "https://i.ytimg.com/vi/gris0000001/hqdefault.jpg"


def test_ytmusic_tracks_album():
    title, tracks = m.ytmusic_tracks("https://music.youtube.com/browse/MPREb_abc", FakeClient())
    assert title == "Álbum Z"
    assert [(p["id"], p["album"]) for p in tracks] == [("t1", "Álbum Z"), ("t2", "Álbum Z")]


def test_ytmusic_tracks_without_library(monkeypatch):
    monkeypatch.setattr(m, "HAS_YTMUSIC", False)
    assert m.ytmusic_tracks("https://music.youtube.com/playlist?list=PL1") is None


# ------------------------------------------------------------------ resolver
def test_resolve_ytmusic_playlist(tmp_path, monkeypatch):
    real = m.ytmusic_tracks
    monkeypatch.setattr(m, "ytmusic_tracks", lambda url, **kw: real(url, FakeClient()))
    plan = m.resolve("https://music.youtube.com/playlist?list=PL123", tmp_path)
    assert plan["dest"] == tmp_path / "Mi Playlist"
    assert plan["is_playlist"]
    assert plan["urls"] == ["https://www.youtube.com/watch?v=bDybRgaVD2Y",
                            "https://www.youtube.com/watch?v=gris0000001"]
    assert plan["extras"]["bDybRgaVD2Y"] == {"title": "Canción", "artist": "Artista A, Artista B",
                                             "album": "Disco"}
    assert plan["default_album"] == "Mi Playlist"

    # Al reintentar dentro de la playlist se respeta la subcarpeta indicada
    assert m.resolve("https://music.youtube.com/playlist?list=PL123", tmp_path,
                      "Otra")["dest"] == tmp_path / "Otra"


def test_resolve_falls_back_to_ytdlp(tmp_path, monkeypatch):
    def fails(url, **kw):
        raise ConnectionError("sin internet")
    monkeypatch.setattr(m, "ytmusic_tracks", fails)
    monkeypatch.setattr(m, "analyze_url", lambda url, folder, sub, *a: (folder, [], True, ""))
    plan = m.resolve("https://music.youtube.com/playlist?list=PL123", tmp_path)
    assert plan["urls"] == ["https://music.youtube.com/playlist?list=PL123"]
    assert plan["extras"] == {}


def test_resolve_drops_radio(tmp_path, monkeypatch):
    views = []
    monkeypatch.setattr(m, "analyze_url",
                        lambda url, folder, sub, *a: views.append(url) or (folder, [], False, ""))
    plan = m.resolve("https://music.youtube.com/watch?v=abc&list=RDAMVMabc", tmp_path)
    assert views == plan["urls"] == ["https://www.youtube.com/watch?v=abc"]


# ------------------------------------------------------------------ LimpiarMetadata
def test_clean_metadata_uses_youtube_music_data(tmp_path):
    events = []
    pp = m.CleanMetadata(None, tmp_path, events.append, _NeverCancelled(),
                           extras={"v1": {"title": "Canción", "artist": "Artista A", "album": ""}},
                           default_album="Mi Playlist")
    _, info = pp.run({"id": "v1", "title": "Artista A - Canción (Official Video)",
                      "uploader": "ArtistaAVEVO", "track_number": 63})
    assert (info["title"], info["artist"], info["album"]) == ("Canción", "Artista A", "Mi Playlist")
    assert info["safe_filename"] == "Canción"
    assert info["track_number"] is None
    assert events[0][0] == "info"


def test_clean_metadata_without_extras_unchanged(tmp_path):
    pp = m.CleanMetadata(None, tmp_path, lambda e: None, _NeverCancelled())
    _, info = pp.run({"id": "v1", "title": "T", "uploader": "Canal - Topic"})
    assert (info["artist"], info["album"]) == ("Canal", "T")


class _NeverCancelled:
    def is_set(self):
        return False
