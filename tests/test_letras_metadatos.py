"""Letras (LRCLIB) y metadatos/portadas (MusicBrainz + Cover Art Archive)."""
import functools
import http.server
import io
import threading
import urllib.error
from pathlib import Path

import pytest

import letras
import metadatos
import motor_descarga as m
# Se importan aquí (antes de que conftest las reemplace) para probar las funciones reales
from letras import buscar_letra as buscar_letra_real
from metadatos import buscar as buscar_mb_real
from test_motor import generar_audio, necesita_ffmpeg, necesita_mutagen

LRC = "[ar:Daft Punk]\n[00:01.50] Primera\n[00:03.00][00:10.25] Estribillo\n[00:05:5] Otra\n[01:02] Final\n"


# ==============================================================================
# LETRAS
# ==============================================================================
def test_parsear_lrc():
    assert letras.parsear_lrc(LRC) == [(1.5, "Primera"), (3.0, "Estribillo"), (5.5, "Otra"),
                                       (10.25, "Estribillo"), (62.0, "Final")]
    assert letras.parsear_lrc("solo texto\nsin marcas") == []
    assert letras.parsear_lrc(None) == []


def test_linea_actual():
    lineas = letras.parsear_lrc(LRC)
    assert letras.linea_actual(lineas, 0.5) == -1
    assert letras.linea_actual(lineas, 1.5) == 0
    assert letras.linea_actual(lineas, 9.9) == 2
    assert letras.linea_actual(lineas, 500) == 4


def _lrclib_falso(monkeypatch, get=None, search=()):
    pedidos = []

    def pedir(ruta, parametros, timeout=10, proxy=""):
        pedidos.append((ruta, dict(parametros)))
        return get if ruta == "get" else list(search)
    monkeypatch.setattr(letras, "_pedir", pedir)
    return pedidos


def test_buscar_letra_exacta(monkeypatch):
    pedidos = _lrclib_falso(monkeypatch, get={"id": 1, "syncedLyrics": "[00:01.00] Hola", "plainLyrics": "Hola"})
    r = buscar_letra_real("One More Time", "Daft Punk", "Discovery", 320.4)
    assert r["sincronizada"] == "[00:01.00] Hola" and r["texto"] == "Hola"
    assert pedidos == [("get", {"track_name": "One More Time", "artist_name": "Daft Punk",
                                "album_name": "Discovery", "duration": 320})]


def test_buscar_letra_por_busqueda(monkeypatch):
    resultados = [
        {"id": 1, "duration": 200, "plainLyrics": "otra versión", "syncedLyrics": "[00:01] x"},  # duración lejana
        {"id": 2, "duration": 321, "plainLyrics": "sin sincronizar", "syncedLyrics": None},
        {"id": 3, "duration": 319, "plainLyrics": "texto", "syncedLyrics": "[00:01.00] sincronizada"},
        {"id": 4, "duration": 320, "plainLyrics": None, "syncedLyrics": None},  # sin letra
    ]
    _lrclib_falso(monkeypatch, get=None, search=resultados)
    r = buscar_letra_real("One More Time", "Daft Punk", "", 320)
    assert r["id"] == 3  # la de duración parecida que además está sincronizada
    _lrclib_falso(monkeypatch, get=None, search=[])
    assert buscar_letra_real("x", "y", "", 100) is None
    assert buscar_letra_real("", "y") is None


def test_interruptor_corta_tras_fallos(monkeypatch):
    from servicios import Interruptor, ServicioNoDisponible
    monkeypatch.setattr(letras, "interruptor", Interruptor(fallos_max=2, pausa=60))

    class AbridorQueFalla:
        def open(self, *a, **k):
            raise urllib.error.URLError("sin conexión")
    monkeypatch.setattr(letras.urllib.request, "build_opener", lambda *a: AbridorQueFalla())
    for _ in range(2):
        with pytest.raises(urllib.error.URLError):
            letras._pedir("get", {"track_name": "x"})
    with pytest.raises(ServicioNoDisponible):  # ya no intenta conectarse
        letras._pedir("get", {"track_name": "x"})


@necesita_ffmpeg
@necesita_mutagen
@pytest.mark.parametrize("ext", [".mp3", ".m4a", ".opus"])
def test_guardar_y_leer_letra(tmp_path, ext):
    ruta = generar_audio(tmp_path / f"a{ext}", segundos=1)
    letra = {"sincronizada": "[00:00.50] Hola\n[00:01.00] Chao", "texto": "Hola\nChao"}
    assert letras.guardar_letra(ruta, letra, "incrustada") == ["incrustada"]
    assert not letras.ruta_lrc(ruta).exists()
    assert letras.leer_letra(ruta) == letra["sincronizada"]
    assert letras.guardar_letra(ruta, letra, "ambas") == ["lrc", "incrustada"]
    assert letras.ruta_lrc(ruta).read_text(encoding="utf-8").strip() == letra["sincronizada"]
    assert letras.guardar_letra(ruta, letra, "no") == []
    # sin versión sincronizada se guarda el texto
    letras.ruta_lrc(ruta).unlink()
    letras.guardar_letra(ruta, {"sincronizada": None, "texto": "Solo texto"}, "incrustada")
    assert letras.leer_letra(ruta) == "Solo texto"


# ==============================================================================
# METADATOS
# ==============================================================================
def test_normalizar_y_coincidir():
    assert metadatos.normalizar("One More Time (Official Video)") == "one more time"
    assert metadatos.normalizar("Canción Ñoña [Lyric Video] feat. Otro") == "cancion nona"
    assert metadatos._coinciden("Daft Punk", "DAFT PUNK")
    assert metadatos._coinciden("Simon & Garfunkel", "Simon and Garfunkel")
    assert not metadatos._coinciden("Queen", "Queens of the Stone Age")
    assert not metadatos._coinciden("", "x")


def _grabacion(**k):
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


def test_elegir_prefiere_el_album_original():
    r = metadatos.elegir([_grabacion()], "One More Time", "Daft Punk", 320)
    assert r == {"album": "Discovery", "anio": "2000", "release_id": "disc1",
                 "release_group_id": "rg-disc", "grabacion_id": "rec1"}


@pytest.mark.parametrize("cambio, titulo, artista, duracion", [
    ({"score": 60}, "One More Time", "Daft Punk", 320),                       # puntaje bajo
    ({}, "One More Time", "Otro Artista", 320),                                # otro artista
    ({}, "Otra Canción", "Daft Punk", 320),                                    # otro título
    ({}, "One More Time", "Daft Punk", 200),                                   # duración muy distinta
    ({"releases": []}, "One More Time", "Daft Punk", 320),                     # sin lanzamientos
])
def test_elegir_es_conservador(cambio, titulo, artista, duracion):
    assert metadatos.elegir([_grabacion(**cambio)], titulo, artista, duracion) is None


def test_buscar_mb_consulta_y_cachea(monkeypatch):
    consultas = []

    def pedir(ruta, parametros, timeout=15, proxy=""):
        consultas.append(parametros["query"])
        return {"recordings": [_grabacion()]}
    monkeypatch.setattr(metadatos, "_pedir_mb", pedir)
    monkeypatch.setattr(metadatos, "_cache", {})
    r = buscar_mb_real("One More Time (Official Video)", "Daft Punk", 320)
    assert r["album"] == "Discovery"
    assert consultas == ['recording:"One More Time" AND artist:"Daft Punk"']
    buscar_mb_real("One More Time (Official Video)", "Daft Punk", 321)
    assert len(consultas) == 1  # segunda vez: de la caché
    assert buscar_mb_real("", "Daft Punk") is None


def test_escapar_caracteres_especiales():
    assert metadatos._escapar('AC/DC: "Hells" (Bells)') == 'AC\\/DC\\: \\"Hells\\" \\(Bells\\)'


def test_urls_portada_y_datos_pobres():
    assert metadatos.urls_portada({"release_group_id": "g", "release_id": "r"}, 500) == [
        "https://coverartarchive.org/release-group/g/front-500",
        "https://coverartarchive.org/release/r/front-500"]
    assert metadatos.urls_portada(None) == []
    assert metadatos.datos_pobres({"album": "", "release_year": 2001})
    assert metadatos.datos_pobres({"album": "Discovery"})
    assert not metadatos.datos_pobres({"album": "Discovery", "release_year": 2001})


# ==============================================================================
# DE PUNTA A PUNTA (descarga real desde un servidor local)
# ==============================================================================
@pytest.fixture
def servidor(tmp_path_factory, monkeypatch):
    from PIL import Image
    raiz = tmp_path_factory.mktemp("srv")
    generar_audio(raiz / "cancion.m4a", segundos=2)
    Image.new("RGB", (300, 300), (200, 30, 30)).save(raiz / "portada_album.jpg")
    manejador = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(raiz))
    manejador.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), manejador)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _bajar(base, carpeta, **cfg_extra):
    eventos = []
    cfg = dict(m.CONFIG_POR_DEFECTO, carpeta=str(carpeta), formato="mp3", replaygain=False, **cfg_extra)
    m.ejecutar_trabajo(f"{base}/cancion.m4a", cfg, eventos.append, threading.Event())
    listo = [e[2] for e in eventos if e[0] == "listo"]
    assert listo, eventos
    return listo[0], eventos


@necesita_ffmpeg
@necesita_mutagen
@pytest.mark.parametrize("primera_existe", [True, False])
def test_musicbrainz_completa_album_anio_y_portada(servidor, tmp_path, monkeypatch, primera_existe):
    from PIL import Image
    monkeypatch.setattr(metadatos, "buscar", lambda *a, **k: {
        "album": "Discovery", "anio": "2001", "release_id": "r", "release_group_id": "g", "grabacion_id": "x"})
    # La "portada del álbum" sale del servidor local; si la primera opción no existe (404),
    # se usa la siguiente
    urls = ([f"{servidor}/portada_album.jpg"] if primera_existe
            else [f"{servidor}/no_existe.jpg", f"{servidor}/portada_album.jpg"])
    monkeypatch.setattr(metadatos, "urls_portada", lambda hallado, tamano=1200: urls)
    listo, eventos = _bajar(servidor, tmp_path, musicbrainz=True, portada_caa=True)
    assert (listo["album"], listo["anio"]) == ("Discovery", "2001")
    assert listo.get("musicbrainz") is True
    assert m.leer_etiquetas(listo["ruta"])["anio"] == "2001"  # el año queda también EN el archivo
    portada = Image.open(io.BytesIO(m.leer_portada(listo["ruta"])))
    assert portada.getpixel((150, 150))[0] > 150  # es la roja del "álbum"
    assert ("estado", "cancion", "Buscando álbum en MusicBrainz…") in eventos


@necesita_ffmpeg
def test_musicbrainz_apagado_no_consulta(servidor, tmp_path, monkeypatch):
    llamado = []
    monkeypatch.setattr(metadatos, "buscar", lambda *a, **k: llamado.append(1))
    _bajar(servidor, tmp_path, musicbrainz=False)
    assert not llamado


@necesita_ffmpeg
def test_musicbrainz_caido_no_rompe_la_descarga(servidor, tmp_path, monkeypatch):
    def caido(*a, **k):
        raise urllib.error.URLError("sin conexión")
    monkeypatch.setattr(metadatos, "buscar", caido)
    listo, _ = _bajar(servidor, tmp_path, musicbrainz=True)
    assert listo["ruta"] and Path(listo["ruta"]).exists()


@necesita_ffmpeg
@necesita_mutagen
def test_descarga_con_letra(servidor, tmp_path, monkeypatch):
    pedidas = []
    monkeypatch.setattr(letras, "buscar_letra", lambda *a, **k: pedidas.append(a) or {
        "sincronizada": "[00:00.50] Hola", "texto": "Hola", "instrumental": False})
    listo, eventos = _bajar(servidor, tmp_path, letras="ambas")
    assert listo["letra"] == "sincronizada"
    assert letras.ruta_lrc(listo["ruta"]).exists()
    assert letras.leer_letra(listo["ruta"]) == "[00:00.50] Hola"
    assert ("estado", "cancion", "Buscando letra…") in eventos


@necesita_ffmpeg
def test_letras_apagadas_por_defecto(servidor, tmp_path, monkeypatch):
    llamado = []
    monkeypatch.setattr(letras, "buscar_letra", lambda *a, **k: llamado.append(1))
    assert m.CONFIG_POR_DEFECTO["letras"] == "no"
    listo, _ = _bajar(servidor, tmp_path)
    assert not llamado and "letra" not in listo
