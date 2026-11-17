"""Organizar descargas en carpetas (Artista / Álbum)."""
import functools
import http.server
import threading
from pathlib import Path

import pytest

import motor_descarga as m
from test_motor import generar_audio, necesita_ffmpeg


class _NuncaCancelado:
    def is_set(self):
        return False


# ------------------------------------------------------------------ plantilla y modo
def test_por_defecto_no_organiza():
    assert m.CONFIG_POR_DEFECTO["organizar"] == "plano"
    assert m.CONFIG_POR_DEFECTO["playlists_en_carpeta"] is True


def test_plantilla_salida(tmp_path):
    assert m.plantilla_salida(tmp_path) == str(tmp_path / "%(nombre_archivo)s.%(ext)s")
    assert m.plantilla_salida(tmp_path, "artista") == str(
        tmp_path / "%(org_artista)s" / "%(nombre_archivo)s.%(ext)s")
    assert m.plantilla_salida(tmp_path, "artista_album") == str(
        tmp_path / "%(org_artista)s" / "%(org_album)s" / "%(nombre_archivo)s.%(ext)s")
    assert m.plantilla_salida(tmp_path, "inventado") == m.plantilla_salida(tmp_path)


@pytest.mark.parametrize("cfg, es_playlist, esperado", [
    ({"organizar": "plano"}, False, "plano"),
    ({"organizar": "artista_album"}, False, "artista_album"),
    ({"organizar": "artista_album", "playlists_en_carpeta": True}, True, "plano"),
    ({"organizar": "artista_album", "playlists_en_carpeta": False}, True, "artista_album"),
    ({"organizar": "artista", "subcarpeta": "Mi Playlist"}, False, "plano"),  # reintento
    ({"organizar": "cualquier cosa"}, False, "plano"),
    ({}, False, "plano"),
])
def test_modo_organizar(cfg, es_playlist, esperado):
    assert m.modo_organizar(cfg, es_playlist) == esperado


def test_yt_dlp_arma_la_ruta_y_protege_las_barras(tmp_path):
    from yt_dlp import YoutubeDL
    info = {"id": "x", "ext": "mp3", "title": "T", "org_artista": "AC/DC",
            "org_album": "Back in Black", "nombre_archivo": "Hells Bells"}
    with YoutubeDL({"outtmpl": {"default": m.plantilla_salida(tmp_path, "artista_album")},
                    "quiet": True}) as ydl:
        ruta = Path(ydl.prepare_filename(info))
    assert ruta.parent.parent.parent == tmp_path  # exactamente dos carpetas, no tres
    assert ruta.parent.name == "Back in Black" and ruta.name == "Hells Bells.mp3"


def test_limpiar_metadata_busca_choques_en_la_carpeta_organizada(tmp_path):
    (tmp_path / "Intro.mp3").touch()                       # otro "Intro" suelto en la raíz
    (tmp_path / "Artista B" / "Disco").mkdir(parents=True)
    (tmp_path / "Artista B" / "Disco" / "Intro.mp3").touch()
    pp = m.LimpiarMetadata(None, tmp_path, lambda e: None, _NuncaCancelado(), organizar="artista_album")

    _, info = pp.run({"id": "1", "title": "Intro", "artist": "Artista A", "album": "Disco"})
    assert (info["org_artista"], info["org_album"], info["nombre_archivo"]) == ("Artista A", "Disco", "Intro")

    _, info = pp.run({"id": "2", "title": "Intro", "artist": "Artista B", "album": "Disco"})
    assert info["nombre_archivo"] == "Intro (Artista B)"


# ------------------------------------------------------------------ de punta a punta
@pytest.fixture
def servidor(tmp_path_factory, monkeypatch):
    """Sirve un audio por HTTP local para que yt-dlp lo 'descargue' sin internet."""
    raiz = tmp_path_factory.mktemp("srv")
    generar_audio(raiz / "cancion.m4a", segundos=2)
    manejador = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(raiz))
    manejador.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), manejador)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    yield f"http://127.0.0.1:{httpd.server_address[1]}/cancion.m4a"
    httpd.shutdown()


def _descargar(monkeypatch, url, carpeta, es_playlist, **cfg_extra):
    def plan_falso(_url, carpeta_base, sub="", log=None):
        datos = {"titulo": "One More Time", "artista": "Daft Punk", "album": "Discovery"}
        return {"destino": carpeta_base / (sub or "Mi Playlist") if es_playlist else carpeta_base,
                "entradas": [dict(datos, id="cancion", url=url, miniatura="")],
                "es_playlist": es_playlist, "urls": [url], "extras": {"cancion": datos},
                "album_defecto": "Mi Playlist" if es_playlist else ""}
    monkeypatch.setattr(m, "resolver", plan_falso)
    eventos = []
    cfg = dict(m.CONFIG_POR_DEFECTO, carpeta=str(carpeta), formato="mp3", replaygain=False, **cfg_extra)
    m.ejecutar_trabajo(url, cfg, eventos.append, threading.Event())
    listos = [e[2]["ruta"] for e in eventos if e[0] == "listo"]
    assert len(listos) == 1, eventos
    return Path(listos[0]).relative_to(carpeta)


@necesita_ffmpeg
@pytest.mark.parametrize("cfg_extra, es_playlist, esperado", [
    ({}, False, "One More Time.mp3"),
    ({"organizar": "artista"}, False, "Daft Punk/One More Time.mp3"),
    ({"organizar": "artista_album"}, False, "Daft Punk/Discovery/One More Time.mp3"),
    ({"organizar": "artista_album"}, True, "Mi Playlist/One More Time.mp3"),
    ({"organizar": "artista_album", "playlists_en_carpeta": False}, True,
     "Daft Punk/Discovery/One More Time.mp3"),
])
def test_descarga_organizada(servidor, tmp_path, monkeypatch, cfg_extra, es_playlist, esperado):
    ruta = _descargar(monkeypatch, servidor, tmp_path, es_playlist, **cfg_extra)
    assert ruta == Path(esperado)
