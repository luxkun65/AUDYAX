"""Playlists de YouTube Music: versiones sustituidas y enlaces de "radio"."""
from pathlib import Path

import pytest

import motor_descarga as m


# ------------------------------------------------------------------ limpiar_url
@pytest.mark.parametrize("entrada, esperado", [
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
def test_limpiar_url(entrada, esperado):
    assert m.limpiar_url(entrada) == esperado


@pytest.mark.parametrize("url, es", [
    ("https://music.youtube.com/playlist?list=PL123", True),
    ("https://music.youtube.com/playlist?list=OLAK5uy_abc", True),
    ("https://music.youtube.com/browse/MPREb_abc", True),
    ("https://music.youtube.com/watch?v=abc&list=PL123", True),
    ("https://music.youtube.com/watch?v=abc&list=RDAMVMabc", False),
    ("https://music.youtube.com/watch?v=abc", False),
    ("https://www.youtube.com/playlist?list=PL123", False),
])
def test_es_playlist_ytmusic(url, es):
    assert m.es_playlist_ytmusic(url) is es


# ------------------------------------------------------------------ pistas_ytmusic
class ClienteFalso:
    """Imita las respuestas de ytmusicapi (YTMusic.get_playlist / get_album)."""

    def __init__(self):
        self.pedidos = []

    def get_playlist(self, playlist_id, limit=100):
        self.pedidos.append(("playlist", playlist_id, limit))
        return {"title": "Mi Playlist", "tracks": [
            # YouTube Music reproduce bDybRgaVD2Y aunque la playlist guarda QQD-OvbCfCU
            {"videoId": "bDybRgaVD2Y", "title": "Canción", "isAvailable": True,
             "artists": [{"name": "Artista A"}, {"name": "Artista B"}],
             "album": {"name": "Disco", "id": "MPRE1"},
             "thumbnails": [{"url": "chica", "width": 60}, {"url": "grande", "width": 544}]},
            {"videoId": "gris0000001", "title": "No disponible", "isAvailable": False,
             "artists": [{"name": "X"}], "album": None, "thumbnails": []},
            {"videoId": None, "title": "Sin id"},
        ]}

    def get_album(self, browse_id):
        self.pedidos.append(("album", browse_id))
        return {"title": "Álbum Z", "tracks": [
            {"videoId": "t1", "title": "Uno", "artists": [{"name": "Z"}], "album": "Álbum Z"},
            {"videoId": "t2", "title": "Dos", "artists": [{"name": "Z"}]},
        ]}


def test_pistas_ytmusic_usa_la_version_que_suena_en_youtube_music():
    cliente = ClienteFalso()
    titulo, pistas = m.pistas_ytmusic("https://music.youtube.com/playlist?list=PL123&si=x", cliente)
    assert cliente.pedidos == [("playlist", "PL123", None)]  # None = todas las canciones
    assert titulo == "Mi Playlist"
    assert [p["id"] for p in pistas] == ["bDybRgaVD2Y", "gris0000001"]
    assert pistas[0] == {
        "id": "bDybRgaVD2Y", "titulo": "Canción", "artista": "Artista A, Artista B",
        "album": "Disco", "url": "https://music.youtube.com/watch?v=bDybRgaVD2Y",
        "miniatura": "grande", "disponible": True}
    assert pistas[1]["disponible"] is False
    assert pistas[1]["miniatura"] == "https://i.ytimg.com/vi/gris0000001/hqdefault.jpg"


def test_pistas_ytmusic_album():
    titulo, pistas = m.pistas_ytmusic("https://music.youtube.com/browse/MPREb_abc", ClienteFalso())
    assert titulo == "Álbum Z"
    assert [(p["id"], p["album"]) for p in pistas] == [("t1", "Álbum Z"), ("t2", "Álbum Z")]


def test_pistas_ytmusic_sin_libreria(monkeypatch):
    monkeypatch.setattr(m, "HAY_YTMUSIC", False)
    assert m.pistas_ytmusic("https://music.youtube.com/playlist?list=PL1") is None


# ------------------------------------------------------------------ resolver
def test_resolver_playlist_ytmusic(tmp_path, monkeypatch):
    real = m.pistas_ytmusic
    monkeypatch.setattr(m, "pistas_ytmusic", lambda url: real(url, ClienteFalso()))
    plan = m.resolver("https://music.youtube.com/playlist?list=PL123", tmp_path)
    assert plan["destino"] == tmp_path / "Mi Playlist"
    assert plan["es_playlist"]
    assert plan["urls"] == ["https://www.youtube.com/watch?v=bDybRgaVD2Y",
                            "https://www.youtube.com/watch?v=gris0000001"]
    assert plan["extras"]["bDybRgaVD2Y"] == {"titulo": "Canción", "artista": "Artista A, Artista B",
                                             "album": "Disco"}
    assert plan["album_defecto"] == "Mi Playlist"

    # Al reintentar dentro de la playlist se respeta la subcarpeta indicada
    assert m.resolver("https://music.youtube.com/playlist?list=PL123", tmp_path,
                      "Otra")["destino"] == tmp_path / "Otra"


def test_resolver_si_ytmusic_falla_usa_ytdlp(tmp_path, monkeypatch):
    def falla(url):
        raise ConnectionError("sin internet")
    monkeypatch.setattr(m, "pistas_ytmusic", falla)
    monkeypatch.setattr(m, "analizar_url", lambda url, carpeta, sub: (carpeta, [], True))
    plan = m.resolver("https://music.youtube.com/playlist?list=PL123", tmp_path)
    assert plan["urls"] == ["https://music.youtube.com/playlist?list=PL123"]
    assert plan["extras"] == {}


def test_resolver_quita_la_radio(tmp_path, monkeypatch):
    vistas = []
    monkeypatch.setattr(m, "analizar_url",
                        lambda url, carpeta, sub: vistas.append(url) or (carpeta, [], False))
    plan = m.resolver("https://music.youtube.com/watch?v=abc&list=RDAMVMabc", tmp_path)
    assert vistas == plan["urls"] == ["https://www.youtube.com/watch?v=abc"]


# ------------------------------------------------------------------ LimpiarMetadata
def test_limpiar_metadata_usa_datos_de_youtube_music(tmp_path):
    eventos = []
    pp = m.LimpiarMetadata(None, tmp_path, eventos.append, _NuncaCancelado(),
                           extras={"v1": {"titulo": "Canción", "artista": "Artista A", "album": ""}},
                           album_defecto="Mi Playlist")
    _, info = pp.run({"id": "v1", "title": "Artista A - Canción (Official Video)",
                      "uploader": "ArtistaAVEVO", "track_number": 63})
    assert (info["title"], info["artist"], info["album"]) == ("Canción", "Artista A", "Mi Playlist")
    assert info["nombre_archivo"] == "Canción"
    assert info["track_number"] is None
    assert eventos[0][0] == "info"


def test_limpiar_metadata_sin_extras_igual_que_antes(tmp_path):
    pp = m.LimpiarMetadata(None, tmp_path, lambda e: None, _NuncaCancelado())
    _, info = pp.run({"id": "v1", "title": "T", "uploader": "Canal - Topic"})
    assert (info["artist"], info["album"]) == ("Canal", "T")


class _NuncaCancelado:
    def is_set(self):
        return False
