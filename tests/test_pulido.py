"""Opciones avanzadas (velocidad, proxy, cookies), versión de la app, FFmpeg incluido,
notificaciones y bandeja."""
import json
import os
import sys
import threading
from pathlib import Path

import pytest

import escritorio
import motor_descarga as m
from test_motor import necesita_ffmpeg


# ------------------------------------------------------------------ opciones avanzadas
def test_avanzadas_apagadas_por_defecto():
    cfg = m.CONFIG_POR_DEFECTO
    assert not cfg["limite_velocidad_activo"] and not cfg["proxy_activo"] and not cfg["cookies_activo"]
    assert m.opciones_red(cfg) == {}
    assert cfg["notificaciones"] and cfg["bandeja"]


def test_limite_de_velocidad_se_reparte():
    cfg = dict(m.CONFIG_POR_DEFECTO, limite_velocidad_activo=True, limite_velocidad_mb=3,
               descargas_simultaneas=3)
    assert m.opciones_red(cfg)["ratelimit"] == 1024 * 1024  # 3 MB/s entre 3 descargas
    cfg["limite_velocidad_mb"] = "1,5"  # coma decimal, como se escribe en español
    cfg["descargas_simultaneas"] = 1
    assert m.opciones_red(cfg)["ratelimit"] == int(1.5 * 1024 * 1024)
    cfg["limite_velocidad_mb"] = "abc"
    assert "ratelimit" not in m.opciones_red(cfg)
    cfg["limite_velocidad_mb"] = 0.001  # nunca menos de 32 KB/s
    assert m.opciones_red(cfg)["ratelimit"] == 32 * 1024


@pytest.mark.parametrize("proxy, valido", [
    ("http://servidor:8080", True), ("https://p.example.com:443", True),
    ("socks5://127.0.0.1:1080", True), ("socks5h://usuario:clave@host:1080", True),
    ("servidor:8080", False), ("ftp://x:21", False), ("http://", False), ("", False),
    ("http://a b:80", False),
])
def test_proxy(proxy, valido):
    cfg = dict(m.CONFIG_POR_DEFECTO, proxy_activo=True, proxy=proxy)
    assert (m.opciones_red(cfg).get("proxy") == proxy) is valido
    cfg["proxy_activo"] = False
    assert "proxy" not in m.opciones_red(cfg)


def test_cookies(tmp_path):
    cfg = dict(m.CONFIG_POR_DEFECTO, cookies_activo=True)
    assert m.opciones_red(cfg) == {"cookiesfrombrowser": ("firefox",)}
    cfg["cookies_origen"] = "edge"
    assert m.opciones_red(cfg) == {"cookiesfrombrowser": ("edge",)}
    cfg["cookies_origen"] = "inventado"
    assert m.opciones_red(cfg) == {}
    cfg["cookies_origen"] = "archivo"
    assert m.opciones_red(cfg) == {}  # sin archivo elegido
    archivo = tmp_path / "cookies.txt"
    archivo.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    cfg["cookies_archivo"] = str(archivo)
    assert m.opciones_red(cfg) == {"cookiefile": str(archivo)}


def test_cookies_txt_no_se_modifica(tmp_path):
    """yt-dlp reescribe el archivo de cookies al terminar: se le pasa una copia temporal."""
    archivo = tmp_path / "cookies.txt"
    original = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tf6=8\n"
    archivo.write_text(original, encoding="utf-8")
    opciones = {"cookiefile": str(archivo), "quiet": True}
    with m._CookiesSoloLectura(opciones) as op:
        copia = op["cookiefile"]
        assert copia != str(archivo) and Path(copia).read_text(encoding="utf-8") == original
        with m.YoutubeDL(op) as ydl:
            ydl.cookiejar  # carga las cookies; al salir, yt-dlp las vuelve a guardar en la copia
    assert not Path(copia).exists()
    assert archivo.read_text(encoding="utf-8") == original


def test_las_opciones_llegan_a_yt_dlp(tmp_path, monkeypatch):
    vistas = []

    class Espia:
        def __init__(self, opciones):
            vistas.append(dict(opciones))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            return {"entries": []}

    monkeypatch.setattr(m, "YoutubeDL", Espia)
    red = {"proxy": "http://p:1", "ratelimit": 1000}
    m.buscar("algo", 3, red)
    m.analizar_url("https://www.youtube.com/watch?v=x", tmp_path, "", red)
    cfg = dict(m.CONFIG_POR_DEFECTO, proxy_activo=True, proxy="http://p:1")
    opciones = m.construir_opciones(cfg, tmp_path, tmp_path, None, None, None)
    assert all(v["proxy"] == "http://p:1" for v in vistas) and len(vistas) == 2
    assert opciones["proxy"] == "http://p:1"


def test_ytmusic_usa_el_proxy(monkeypatch):
    creados = []

    class YTMusicFalso:
        def __init__(self, proxies=None):
            creados.append(proxies)

        def get_playlist(self, pid, limit=None):
            return {"title": "L", "tracks": []}
    monkeypatch.setattr(m, "YTMusic", YTMusicFalso, raising=False)
    monkeypatch.setattr(m, "HAY_YTMUSIC", True)
    m.pistas_ytmusic("https://music.youtube.com/playlist?list=PL1", proxy="socks5://h:1")
    m.pistas_ytmusic("https://music.youtube.com/playlist?list=PL1")
    assert creados == [{"http": "socks5://h:1", "https": "socks5://h:1"}, None]


# ------------------------------------------------------------------ versión y FFmpeg incluido
def test_version():
    assert m.es_mas_nueva(m.VERSION, "0.0.1")


def test_ultima_version_app(monkeypatch):
    class Respuesta:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"tag_name": "v1.2.3"}).encode()
    pedidas = []
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda req, timeout: pedidas.append(req.full_url) or Respuesta())
    assert m.ultima_version_app() == "1.2.3"
    assert pedidas == [f"https://api.github.com/repos/{m.REPO}/releases/latest"]


def test_preparar_ffmpeg_incluido(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "carpeta_programa", lambda: tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin")
    assert m.preparar_ffmpeg_incluido() == ""
    assert os.environ["PATH"] == "/usr/bin"
    (tmp_path / "ffmpeg").mkdir()
    (tmp_path / "ffmpeg" / ("ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg")).write_bytes(b"")
    assert m.preparar_ffmpeg_incluido() == str(tmp_path / "ffmpeg")
    assert os.environ["PATH"].split(os.pathsep)[0] == str(tmp_path / "ffmpeg")


# ------------------------------------------------------------------ notificaciones y bandeja
def test_resumen_lote():
    assert escritorio.resumen_lote(78, 2) == "78 canciones descargadas · 2 con error"
    assert escritorio.resumen_lote(1, 0) == "1 canción descargada"
    assert escritorio.resumen_lote(0, 0, 3) == "3 ya estaban descargadas"
    assert escritorio.resumen_lote(2, 1, 1) == "2 canciones descargadas · 1 ya estaba descargada · 1 con error"
    assert escritorio.resumen_lote(0, 0) == "No se descargó ninguna canción"


def test_notificar_nunca_falla(monkeypatch):
    import types

    def explota(**kw):
        raise RuntimeError("sin servicio de notificaciones")
    falso = types.ModuleType("plyer")
    falso.notification = types.SimpleNamespace(notify=explota)
    monkeypatch.setitem(sys.modules, "plyer", falso)
    assert escritorio.notificar("t", "m") is False

    enviadas = []
    falso.notification = types.SimpleNamespace(notify=lambda **kw: enviadas.append(kw))
    assert escritorio.notificar("Título", "Mensaje") is True
    assert enviadas[0]["title"] == "Título" and enviadas[0]["app_name"] == "AUDYAX"


def test_bandeja_sin_libreria_no_rompe(monkeypatch):
    monkeypatch.setattr(escritorio, "_pystray", None)
    monkeypatch.setattr(escritorio, "_pystray_error", "ImportError: no hay pystray")
    b = escritorio.Bandeja(None, lambda: None, lambda: None, lambda: None, lambda: False)
    assert not escritorio.bandeja_disponible()
    assert b.mostrar() is False and not b.visible
    assert b.notificar("t", "m") is False
    b.ocultar()  # no falla


def test_bandeja_no_esconde_si_el_icono_no_aparece(monkeypatch):
    """Si el sistema no tiene bandeja, mostrar() debe decir False (y la ventana no se esconde)."""
    import types

    class IconoQueNoSeVe:
        visible = False
        detenido = threading.Event()

        def __init__(self, *a, **k):
            pass

        def run(self):
            pass

        def stop(self):
            IconoQueNoSeVe.detenido.set()
    falso = types.SimpleNamespace(Icon=IconoQueNoSeVe, Menu=lambda *a: None, MenuItem=lambda *a, **k: None)
    falso.Menu.SEPARATOR = None
    monkeypatch.setattr(escritorio, "_pystray", falso)
    b = escritorio.Bandeja(None, lambda: None, lambda: None, lambda: None, lambda: False)
    assert b.mostrar() is False and not b.visible
    assert IconoQueNoSeVe.detenido.wait(2)


def test_anclado():
    import types
    assert not escritorio._anclado(types.SimpleNamespace(visible=False))
    assert escritorio._anclado(types.SimpleNamespace(visible=True))  # Windows / macOS
    assert not escritorio._anclado(types.SimpleNamespace(visible=True, _systray_manager=None))  # X11 sin bandeja
    assert escritorio._anclado(types.SimpleNamespace(visible=True, _systray_manager=123))


# ------------------------------------------------------------------ descarga con límite (de punta a punta)
@necesita_ffmpeg
def test_descarga_con_limite_de_velocidad(tmp_path, monkeypatch):
    """Con el límite activo yt-dlp recibe 'ratelimit' y la descarga igual termina."""
    import functools
    import http.server

    from test_motor import generar_audio
    raiz = tmp_path / "srv"
    raiz.mkdir()
    generar_audio(raiz / "c.m4a", segundos=2)
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(raiz)))
    httpd.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    vistas = []
    real = m.YoutubeDL

    class Espia(real):
        def __init__(self, params=None, *a, **k):
            vistas.append((params or {}).get("ratelimit"))
            super().__init__(params, *a, **k)
    monkeypatch.setattr(m, "YoutubeDL", Espia)
    eventos = []
    cfg = dict(m.CONFIG_POR_DEFECTO, carpeta=str(tmp_path / "dl"), formato="mp3", replaygain=False,
               limite_velocidad_activo=True, limite_velocidad_mb=4, descargas_simultaneas=2)
    try:
        m.ejecutar_trabajo(f"http://127.0.0.1:{httpd.server_address[1]}/c.m4a", cfg, eventos.append,
                           threading.Event())
    finally:
        httpd.shutdown()
    assert [e for e in eventos if e[0] == "listo"]
    assert 2 * 1024 * 1024 in vistas  # 4 MB/s repartidos entre 2 descargas
