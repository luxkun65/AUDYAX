"""Cola de descargas: pausa, descargas en paralelo y exportar la lista."""
import csv
import functools
import http.server
import threading
import time
from pathlib import Path

import pytest

import motor_descarga as m
from test_motor import generar_audio, necesita_ffmpeg


# ------------------------------------------------------------------ utilidades
def test_descargas_simultaneas_se_limita():
    assert m.descargas_simultaneas({}) == 1
    assert m.descargas_simultaneas({"descargas_simultaneas": 2}) == 2
    assert m.descargas_simultaneas({"descargas_simultaneas": 99}) == m.MAX_SIMULTANEAS
    assert m.descargas_simultaneas({"descargas_simultaneas": 0}) == 1
    assert m.descargas_simultaneas({"descargas_simultaneas": "x"}) == 1
    assert m.CONFIG_POR_DEFECTO["descargas_simultaneas"] == 2


def test_esperar_si_pausa():
    pausa, cancelar = threading.Event(), threading.Event()
    m.esperar_si_pausa(pausa, cancelar)  # sin pausa: no espera
    m.esperar_si_pausa(None, None)

    pausa.set()
    inicio = time.monotonic()
    threading.Timer(0.5, pausa.clear).start()
    m.esperar_si_pausa(pausa, cancelar)
    assert time.monotonic() - inicio >= 0.4  # esperó hasta reanudar

    pausa.set()
    threading.Timer(0.3, cancelar.set).start()
    with pytest.raises(m.DownloadCancelled):  # cancelar también despierta la espera
        m.esperar_si_pausa(pausa, cancelar)


def test_reserva_de_nombres_entre_hilos(tmp_path):
    reservas = m.ReservaNombres()
    nombres = []
    hilos = [threading.Thread(target=lambda: nombres.append(reservas.reservar(tmp_path, "Intro", "X")))
             for _ in range(5)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sorted(nombres) == ["Intro", "Intro (X)", "Intro (X) 2", "Intro (X) 3", "Intro (X) 4"]


def test_nombre_unico_con_ocupados(tmp_path):
    assert m.nombre_unico(tmp_path, "A", "B", {"a"}) == "A (B)"


def test_configurar_log_no_reemplaza_el_mismo_archivo(tmp_path):
    log = m.configurar_log(tmp_path)
    handler = log.handlers[0]
    assert m.configurar_log(tmp_path).handlers == [handler]
    otra = tmp_path / "otra"
    otra.mkdir()
    assert m.configurar_log(otra).handlers[0] is not handler


def test_resolver_parte_las_playlists_en_canciones(tmp_path, monkeypatch):
    entradas = [{"id": f"v{i}", "titulo": f"T{i}", "url": f"https://www.youtube.com/watch?v=v{i}",
                 "miniatura": ""} for i in range(3)]
    monkeypatch.setattr(m, "analizar_url",
                        lambda url, carpeta, sub: (carpeta / "Lista", entradas, True, "Lista"))
    plan = m.resolver("https://www.youtube.com/playlist?list=PL1", tmp_path)
    assert plan["por_cancion"] and plan["album_defecto"] == "Lista"
    assert plan["urls"] == [e["url"] for e in entradas]

    monkeypatch.setattr(m, "analizar_url", lambda url, carpeta, sub: (carpeta, entradas[:1], False, ""))
    plan = m.resolver("https://youtu.be/v0", tmp_path)
    assert not plan["por_cancion"] and plan["urls"] == ["https://youtu.be/v0"]


# ------------------------------------------------------------------ exportar
def test_exportar_m3u(tmp_path):
    musica = tmp_path / "Música"
    (musica / "Daft Punk").mkdir(parents=True)
    a = musica / "Daft Punk" / "One More Time.mp3"
    a.write_bytes(b"x")
    canciones = [{"ruta": str(a), "titulo": "One More Time", "artista": "Daft Punk"},
                 {"ruta": str(musica / "no existe.mp3"), "titulo": "X"},
                 {"ruta": "", "titulo": "Sin archivo"}]
    n = m.exportar_m3u(musica / "lista.m3u", canciones)
    assert n == 1
    texto = (musica / "lista.m3u").read_text(encoding="utf-8")
    assert texto.splitlines() == ["#EXTM3U", "#EXTINF:-1,Daft Punk - One More Time",
                                  str(Path("Daft Punk") / "One More Time.mp3")]


def test_exportar_csv(tmp_path):
    filas = [{"num": 1, "estado": "Descargada", "titulo": "Canción ñ", "artista": "A; B",
              "album": "Disco", "anio": "2020", "ruta": "/x.mp3"},
             {"num": 2, "estado": "Error", "titulo": "=HYPERLINK(\"x\")", "motivo": "Video unavailable"}]
    destino = tmp_path / "lista.csv"
    assert m.exportar_csv(destino, filas) == 2
    assert destino.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM: Excel reconoce las tildes
    leidas = list(csv.reader(destino.open(encoding="utf-8-sig"), delimiter=";"))
    assert leidas[0] == [t for _, t in m.COLUMNAS_CSV]
    assert leidas[1][:5] == ["1", "Descargada", "Canción ñ", "A; B", "Disco"]
    assert leidas[2][2].startswith("'=")  # no se convierte en fórmula
    assert leidas[2][9] == "Video unavailable"


@necesita_ffmpeg
def test_duracion_segundos(tmp_path):
    assert m.duracion_segundos(generar_audio(tmp_path / "a.mp3", segundos=3)) in (3, 4)
    assert m.duracion_segundos(tmp_path / "nada.mp3") == -1


# ------------------------------------------------------------------ de punta a punta
@pytest.fixture
def servidor(tmp_path_factory, monkeypatch):
    """Sirve 4 audios por HTTP local, despacio, para poder pausar y medir la concurrencia."""
    raiz = tmp_path_factory.mktemp("srv")
    for i in range(1, 5):
        generar_audio(raiz / f"pista{i}.m4a", segundos=2)

    class Lento(http.server.SimpleHTTPRequestHandler):
        def copyfile(self, origen, destino):
            while True:  # ~0.6 s por archivo
                trozo = origen.read(4096)
                if not trozo:
                    break
                destino.write(trozo)
                time.sleep(0.05)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Lento, directory=str(raiz)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _plan_playlist(base, n=4):
    entradas = [{"id": f"pista{i}", "titulo": f"Pista {i}", "artista": "A", "album": "",
                 "url": f"{base}/pista{i}.m4a", "miniatura": ""} for i in range(1, n + 1)]

    def resolver(url, carpeta, sub="", log=None):
        return {"destino": carpeta / "Lista", "entradas": entradas, "es_playlist": True,
                "urls": [e["url"] for e in entradas],
                "extras": {e["id"]: {"titulo": e["titulo"], "artista": "A", "album": ""} for e in entradas},
                "album_defecto": "Lista", "por_cancion": True}
    return resolver


def _contar_concurrencia(monkeypatch):
    estado = {"activos": 0, "maximo": 0}
    candado = threading.Lock()
    real = m.YoutubeDL

    class Contador(real):
        def download(self, urls):
            with candado:
                estado["activos"] += 1
                estado["maximo"] = max(estado["maximo"], estado["activos"])
            try:
                return super().download(urls)
            finally:
                with candado:
                    estado["activos"] -= 1
    monkeypatch.setattr(m, "YoutubeDL", Contador)
    return estado


@necesita_ffmpeg
@pytest.mark.parametrize("n", [1, 2, 3])
def test_descargas_en_paralelo(servidor, tmp_path, monkeypatch, n):
    monkeypatch.setattr(m, "resolver", _plan_playlist(servidor))
    estado = _contar_concurrencia(monkeypatch)
    eventos = []
    cfg = dict(m.CONFIG_POR_DEFECTO, carpeta=str(tmp_path), formato="mp3", descargas_simultaneas=n)
    m.ejecutar_trabajo("playlist", cfg, eventos.append, threading.Event())
    listos = sorted(Path(e[2]["ruta"]).name for e in eventos if e[0] == "listo")
    assert listos == [f"Pista {i}.mp3" for i in range(1, 5)]
    assert estado["maximo"] == n
    assert not [e for e in eventos if e[0] == "error"]
    assert [e for e in eventos if e[0] == "rg"]  # ReplayGain de álbum al final, como antes


@necesita_ffmpeg
def test_limite_compartido_entre_trabajos(servidor, tmp_path, monkeypatch):
    """Dos trabajos a la vez con un semáforo de 2 nunca pasan de 2 descargas en total."""
    monkeypatch.setattr(m, "resolver", _plan_playlist(servidor, 2))
    estado = _contar_concurrencia(monkeypatch)
    limite = threading.BoundedSemaphore(2)
    cfg = dict(m.CONFIG_POR_DEFECTO, formato="mp3", replaygain=False, descargas_simultaneas=2)
    hilos = [threading.Thread(target=m.ejecutar_trabajo,
                              args=("x", dict(cfg, carpeta=str(tmp_path / str(i))), lambda e: None,
                                    threading.Event(), None, limite)) for i in range(2)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(60)
    assert estado["maximo"] == 2
    assert len(list(tmp_path.glob("*/Lista/*.mp3"))) == 4


@necesita_ffmpeg
def test_pausar_y_reanudar(servidor, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "resolver", _plan_playlist(servidor, 2))
    pausa, cancelar, eventos = threading.Event(), threading.Event(), []
    pausa.set()  # empieza en pausa
    cfg = dict(m.CONFIG_POR_DEFECTO, carpeta=str(tmp_path), formato="mp3", replaygain=False)
    hilo = threading.Thread(target=m.ejecutar_trabajo, args=("x", cfg, eventos.append, cancelar, pausa))
    hilo.start()
    time.sleep(2.5)
    assert not [e for e in eventos if e[0] == "listo"]  # en pausa no avanza
    assert hilo.is_alive()
    pausa.clear()
    hilo.join(60)
    assert len([e for e in eventos if e[0] == "listo"]) == 2


@necesita_ffmpeg
def test_cancelar_durante_la_pausa(servidor, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "resolver", _plan_playlist(servidor, 2))
    pausa, cancelar = threading.Event(), threading.Event()
    pausa.set()
    resultado = {}
    cfg = dict(m.CONFIG_POR_DEFECTO, carpeta=str(tmp_path), formato="mp3", replaygain=False)

    def trabajo():
        try:
            m.ejecutar_trabajo("x", cfg, lambda e: None, cancelar, pausa)
        except m.DownloadCancelled:
            resultado["cancelado"] = True
    hilo = threading.Thread(target=trabajo)
    hilo.start()
    time.sleep(1)
    cancelar.set()
    hilo.join(10)
    assert not hilo.is_alive() and resultado.get("cancelado")
    assert not list(tmp_path.glob("**/*.mp3"))
