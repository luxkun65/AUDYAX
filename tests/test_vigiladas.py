"""Playlists vigiladas: detectar canciones nuevas y bajar solo esas."""
import threading
from datetime import datetime, timedelta

import pytest

import motor_descarga as m
import vigiladas as v


@pytest.mark.parametrize("url, es", [
    ("https://www.youtube.com/playlist?list=PL123", True),
    ("https://music.youtube.com/playlist?list=OLAK5uy_abc", True),
    ("https://music.youtube.com/browse/MPREb_abc", True),
    ("https://www.youtube.com/watch?v=x&list=PL123", True),
    ("https://music.youtube.com/watch?v=x&list=RDAMVMx", False),   # radio automática
    ("https://www.youtube.com/watch?v=x", False),
    ("https://example.com/playlist?list=PL1", False),
    ("", False),
])
def test_es_playlist(url, es):
    assert v.es_playlist(url) is es


def test_url_playlist_canonica():
    assert v.url_playlist("https://www.youtube.com/watch?v=x&list=PL123&index=4") == \
        "https://www.youtube.com/playlist?list=PL123"
    assert v.url_playlist("https://music.youtube.com/playlist?list=PL9&si=abc") == \
        "https://music.youtube.com/playlist?list=PL9"
    assert v.url_playlist("https://music.youtube.com/browse/MPREb_abc?x=1") == \
        "https://music.youtube.com/browse/MPREb_abc"


def test_agregar_y_quitar(tmp_path):
    lista = []
    a = v.agregar(lista, "https://www.youtube.com/watch?v=x&list=PL1", tmp_path)
    b = v.agregar(lista, "https://www.youtube.com/playlist?list=PL1", tmp_path)  # la misma
    assert a is b and len(lista) == 1
    with pytest.raises(ValueError):
        v.agregar(lista, "https://www.youtube.com/watch?v=solo", tmp_path)
    v.quitar(lista, a["url"])
    assert lista == []


def _plan(entradas, es_playlist=True, titulo="Mi Lista"):
    return lambda url, carpeta, sub="", log=None, red=None: {
        "destino": carpeta / titulo, "entradas": entradas, "es_playlist": es_playlist,
        "urls": [e["url"] for e in entradas], "extras": {}, "album_defecto": titulo, "por_cancion": True}


def _entrada(i):
    return {"id": f"id{i}", "titulo": f"T{i}", "url": f"https://www.youtube.com/watch?v=id{i}", "miniatura": ""}


def test_revisar_detecta_las_nuevas(tmp_path, monkeypatch):
    (tmp_path / "descargados.txt").write_text("youtube id1\nyoutube id3\n", encoding="utf-8")
    monkeypatch.setattr(m, "resolver", _plan([_entrada(i) for i in range(1, 5)]))
    vig = v.nueva("https://www.youtube.com/playlist?list=PL1", tmp_path)
    nuevas = v.revisar(vig)
    assert [e["id"] for e in nuevas] == ["id2", "id4"]
    assert (vig["nombre"], vig["total"], vig["nuevas"], vig["error"]) == ("Mi Lista", 4, 2, "")
    assert "4 canciones · 2 nuevas · revisada hace un momento" == v.texto_revision(vig)


def test_revisar_con_error(tmp_path, monkeypatch):
    def falla(*a, **k):
        raise ConnectionError("sin internet")
    monkeypatch.setattr(m, "resolver", falla)
    vig = v.nueva("https://www.youtube.com/playlist?list=PL1", tmp_path)
    assert v.revisar(vig) == [] and "sin internet" in vig["error"]
    assert v.texto_revision(vig).startswith("No se pudo revisar")
    monkeypatch.setattr(m, "resolver", _plan([], es_playlist=True))
    assert v.revisar(vig) == [] and "privada" in vig["error"]


def test_hace():
    ahora = datetime(2026, 11, 17, 12, 0, 0)
    assert v.hace((ahora - timedelta(seconds=10)).isoformat(), ahora) == "hace un momento"
    assert v.hace((ahora - timedelta(minutes=5)).isoformat(), ahora) == "hace 5 min"
    assert v.hace((ahora - timedelta(hours=3)).isoformat(), ahora) == "hace 3 h"
    assert v.hace((ahora - timedelta(days=1)).isoformat(), ahora) == "hace 1 día"
    assert v.hace((ahora - timedelta(days=4)).isoformat(), ahora) == "hace 4 días"
    assert v.hace("no es fecha", ahora) == ""


def test_solo_nuevas_no_muestra_ni_baja_las_que_ya_estan(tmp_path, monkeypatch):
    (tmp_path / "descargados.txt").write_text("youtube id1\n", encoding="utf-8")
    monkeypatch.setattr(m, "resolver", _plan([_entrada(1), _entrada(2)]))
    pedidas = []

    class Falso:
        def __init__(self, params):
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def add_post_processor(self, *a, **k):
            pass

        def download(self, urls):
            pedidas.extend(urls)
    monkeypatch.setattr(m, "YoutubeDL", Falso)
    eventos = []
    cfg = dict(m.CONFIG_POR_DEFECTO, carpeta=str(tmp_path), solo_nuevas=True)
    m.ejecutar_trabajo("https://www.youtube.com/playlist?list=PL1", cfg, eventos.append, threading.Event())
    assert [e[1] for e in eventos if e[0] == "nuevo"] == ["id2"]
    assert not [e for e in eventos if e[0] == "omitida"]
    assert pedidas == ["https://www.youtube.com/watch?v=id2"]
    assert ("playlist", "https://www.youtube.com/playlist?list=PL1", "Mi Lista") in eventos
