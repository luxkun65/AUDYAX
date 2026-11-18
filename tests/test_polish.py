"""Opciones avanzadas (velocidad, proxy, cookies), versión de la app, FFmpeg incluido,
notificaciones y bandeja."""
import json
import os
import sys
import threading
from pathlib import Path

import pytest

import desktop
import engine as m
from test_engine import needs_ffmpeg


# ------------------------------------------------------------------ opciones avanzadas
def test_advanced_options_off_by_default():
    cfg = m.DEFAULT_CONFIG
    assert not cfg["speed_limit_enabled"] and not cfg["proxy_enabled"] and not cfg["cookies_enabled"]
    assert m.network_options(cfg) == {}
    assert cfg["notifications"] and cfg["keep_in_background"]


def test_speed_limit_is_split():
    cfg = dict(m.DEFAULT_CONFIG, speed_limit_enabled=True, speed_limit_mb=3,
               parallel_downloads=3)
    assert m.network_options(cfg)["ratelimit"] == 1024 * 1024  # 3 MB/s entre 3 descargas
    cfg["speed_limit_mb"] = "1,5"  # coma decimal, como se escribe en español
    cfg["parallel_downloads"] = 1
    assert m.network_options(cfg)["ratelimit"] == int(1.5 * 1024 * 1024)
    cfg["speed_limit_mb"] = "abc"
    assert "ratelimit" not in m.network_options(cfg)
    cfg["speed_limit_mb"] = 0.001  # nunca menos de 32 KB/s
    assert m.network_options(cfg)["ratelimit"] == 32 * 1024


@pytest.mark.parametrize("proxy, is_valid", [
    ("http://servidor:8080", True), ("https://p.example.com:443", True),
    ("socks5://127.0.0.1:1080", True), ("socks5h://usuario:clave@host:1080", True),
    ("servidor:8080", False), ("ftp://x:21", False), ("http://", False), ("", False),
    ("http://a b:80", False),
])
def test_proxy(proxy, is_valid):
    cfg = dict(m.DEFAULT_CONFIG, proxy_enabled=True, proxy=proxy)
    assert (m.network_options(cfg).get("proxy") == proxy) is is_valid
    cfg["proxy_enabled"] = False
    assert "proxy" not in m.network_options(cfg)


def test_cookies(tmp_path):
    cfg = dict(m.DEFAULT_CONFIG, cookies_enabled=True)
    assert m.network_options(cfg) == {}  # las cookies nunca van en las opciones generales
    assert m.cookie_options(cfg) == {"cookiesfrombrowser": ("firefox",)}
    cfg["cookies_source"] = "edge"
    assert m.cookie_options(cfg) == {"cookiesfrombrowser": ("edge",)}
    cfg["cookies_source"] = "inventado"
    assert m.cookie_options(cfg) == {}
    cfg["cookies_source"] = "file"
    assert m.cookie_options(cfg) == {}  # sin archivo elegido
    file = tmp_path / "cookies.txt"
    file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    cfg["cookies_file"] = str(file)
    assert m.cookie_options(cfg) == {"cookiefile": str(file)}
    cfg["cookies_enabled"] = False
    assert m.cookie_options(cfg) == {}


@pytest.mark.parametrize("reason, is_age", [
    ("Sign in to confirm your age. This video may be inappropriate for some users.", True),
    ("This video is age-restricted and only available on YouTube", True),
    ("Sign in to confirm you're not a bot", False),  # comprobación de bot: NO es por edad
    ("Video unavailable. This video is not available", False),
    ("Private video. Sign in if you've been granted access", False),
])
def test_age_restriction_detection(reason, is_age):
    assert bool(m.AGE_RESTRICTION_RE.search(reason)) is is_age


ID_AGE, ID_NORMAL, ID_OTHER = "edadEdad123", "normalNorm1", "otroOtro123"


def _fake_youtube(monkeypatch, tmp_path, second_attempt_fails=False):
    """YoutubeDL falso: ID_EDAD pide confirmar la edad si no lleva cookies; ID_OTRO no existe."""
    calls = []

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
            with_cookies = "cookiesfrombrowser" in self.params or "cookiefile" in self.params
            for url in urls:
                vid = url.rsplit("=", 1)[-1]
                calls.append((vid, with_cookies))
                log = self.params["logger"]
                if vid == ID_AGE and (not with_cookies or second_attempt_fails):
                    log.error(f"ERROR: [youtube] {vid}: Sign in to confirm your age. This video may "
                              "be inappropriate for some users.")
                elif vid == ID_OTHER:
                    log.error(f"ERROR: [youtube] {vid}: Video unavailable. This video is not available")
    monkeypatch.setattr(m, "YoutubeDL", Fake)
    entries = [{"id": v, "title": v, "url": f"https://www.youtube.com/watch?v={v}", "thumbnail": ""}
                for v in (ID_NORMAL, ID_AGE, ID_OTHER)]
    monkeypatch.setattr(m, "resolve", lambda url, folder, sub="", log=None, net=None: {
        "dest": folder, "entries": entries, "is_playlist": True,
        "urls": [e["url"] for e in entries], "extras": {}, "default_album": "L", "per_song": True})
    return calls


def _run(tmp_path, **cfg_extra):
    events = []
    cfg = dict(m.DEFAULT_CONFIG, folder=str(tmp_path), audio_format="mp3", replaygain=False,
               parallel_downloads=1, **cfg_extra)
    m.run_job("song_list", cfg, events.append, threading.Event())
    errors = {e[1]: e[2] for e in events if e[0] == "error"}
    return events, errors


def test_cookies_only_for_age_restriction(tmp_path, monkeypatch):
    calls = _fake_youtube(monkeypatch, tmp_path)
    events, errors = _run(tmp_path, cookies_enabled=True)
    # Todas sin cookies; SOLO la de edad se reintenta con cookies; la que no existe no se reintenta
    assert calls == [(ID_NORMAL, False), (ID_AGE, False), (ID_OTHER, False), (ID_AGE, True)]
    assert list(errors) == [ID_OTHER]  # la de edad no cuenta como error
    assert ("status", ID_AGE, "Restricción de edad: usando tu sesión…") in events
    assert ID_AGE not in (tmp_path / "faltantes.txt").read_text(encoding="utf-8")


def test_cookies_disabled_are_not_used_and_explained(tmp_path, monkeypatch):
    calls = _fake_youtube(monkeypatch, tmp_path)
    _, errors = _run(tmp_path, cookies_enabled=False)
    assert all(not con for _, con in calls) and len(calls) == 3
    assert "Configuración → Avanzado → Cookies" in errors[ID_AGE]


def test_failure_with_cookies_is_an_error(tmp_path, monkeypatch):
    calls = _fake_youtube(monkeypatch, tmp_path, second_attempt_fails=True)
    events, errors = _run(tmp_path, cookies_enabled=True)
    assert calls.count((ID_AGE, True)) == 1  # un solo reintento
    assert ID_AGE in errors and len([e for e in events if e[0] == "error" and e[1] == ID_AGE]) == 1


def test_cookies_txt_is_not_modified(tmp_path):
    """yt-dlp reescribe el archivo de cookies al terminar: se le pasa una copia temporal."""
    file = tmp_path / "cookies.txt"
    original = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tf6=8\n"
    file.write_text(original, encoding="utf-8")
    options = {"cookiefile": str(file), "quiet": True}
    with m._ReadOnlyCookies(options) as op:
        snapshot = op["cookiefile"]
        assert snapshot != str(file) and Path(snapshot).read_text(encoding="utf-8") == original
        with m.YoutubeDL(op) as ydl:
            ydl.cookiejar  # carga las cookies; al salir, yt-dlp las vuelve a guardar en la copia
    assert not Path(snapshot).exists()
    assert file.read_text(encoding="utf-8") == original


def test_options_reach_yt_dlp(tmp_path, monkeypatch):
    views = []

    class Spy:
        def __init__(self, options):
            views.append(dict(options))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            return {"entries": []}

    monkeypatch.setattr(m, "YoutubeDL", Spy)
    net = {"proxy": "http://p:1", "ratelimit": 1000}
    m.search("algo", 3, net)
    m.analyze_url("https://www.youtube.com/watch?v=x", tmp_path, "", net)
    cfg = dict(m.DEFAULT_CONFIG, proxy_enabled=True, proxy="http://p:1")
    options = m.build_options(cfg, tmp_path, tmp_path, None, None, None)
    assert all(v["proxy"] == "http://p:1" for v in views) and len(views) == 2
    assert options["proxy"] == "http://p:1"


def test_ytmusic_uses_proxy(monkeypatch):
    created = []

    class FakeYTMusic:
        def __init__(self, proxies=None):
            created.append(proxies)

        def get_playlist(self, pid, limit=None):
            return {"title": "L", "tracks": []}
    monkeypatch.setattr(m, "YTMusic", FakeYTMusic, raising=False)
    monkeypatch.setattr(m, "HAS_YTMUSIC", True)
    m.ytmusic_tracks("https://music.youtube.com/playlist?list=PL1", proxy="socks5://h:1")
    m.ytmusic_tracks("https://music.youtube.com/playlist?list=PL1")
    assert created == [{"http": "socks5://h:1", "https": "socks5://h:1"}, None]


# ------------------------------------------------------------------ versión y FFmpeg incluido
def test_version():
    assert m.is_newer(m.VERSION, "0.0.1")


def test_latest_app_version(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"tag_name": "v1.2.3"}).encode()
    requested = []
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda req, timeout: requested.append(req.full_url) or FakeResponse())
    assert m.latest_app_version() == "1.2.3"
    assert requested == [f"https://api.github.com/repos/{m.REPO}/releases/latest"]


def test_setup_bundled_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "program_folder", lambda: tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin")
    assert m.setup_bundled_ffmpeg() == ""
    assert os.environ["PATH"] == "/usr/bin"
    (tmp_path / "ffmpeg").mkdir()
    (tmp_path / "ffmpeg" / ("ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg")).write_bytes(b"")
    assert m.setup_bundled_ffmpeg() == str(tmp_path / "ffmpeg")
    assert os.environ["PATH"].split(os.pathsep)[0] == str(tmp_path / "ffmpeg")


# ------------------------------------------------------------------ notificaciones y bandeja
def test_batch_summary():
    assert desktop.batch_summary(78, 2) == "78 canciones descargadas · 2 con error"
    assert desktop.batch_summary(1, 0) == "1 canción descargada"
    assert desktop.batch_summary(0, 0, 3) == "3 ya estaban descargadas"
    assert desktop.batch_summary(2, 1, 1) == "2 canciones descargadas · 1 ya estaba descargada · 1 con error"
    assert desktop.batch_summary(0, 0) == "No se descargó ninguna canción"


def test_notify_never_fails(monkeypatch):
    import types

    def explodes(**kw):
        raise RuntimeError("sin servicio de notificaciones")
    fake = types.ModuleType("plyer")
    fake.notification = types.SimpleNamespace(notify=explodes)
    monkeypatch.setitem(sys.modules, "plyer", fake)
    assert desktop.notify("t", "m") is False

    sent = []
    fake.notification = types.SimpleNamespace(notify=lambda **kw: sent.append(kw))
    assert desktop.notify("Título", "Mensaje") is True
    assert sent[0]["title"] == "Título" and sent[0]["app_name"] == "AUDYAX"


def test_tray_without_library_does_not_break(monkeypatch):
    monkeypatch.setattr(desktop, "_pystray", None)
    monkeypatch.setattr(desktop, "_pystray_error", "ImportError: no hay pystray")
    b = desktop.Tray(None, lambda: None, lambda: None, lambda: None, lambda: False)
    assert not desktop.tray_available()
    assert b.show() is False and not b.visible
    assert b.notify("t", "m") is False
    b.hide()  # no falla


def test_tray_does_not_hide_if_icon_does_not_appear(monkeypatch):
    """Si el sistema no tiene bandeja, mostrar() debe decir False (y la ventana no se esconde)."""
    import types

    class InvisibleIcon:
        visible = False
        stopped = threading.Event()

        def __init__(self, *a, **k):
            pass

        def run(self):
            pass

        def stop(self):
            InvisibleIcon.stopped.set()
    fake = types.SimpleNamespace(Icon=InvisibleIcon, Menu=lambda *a: None, MenuItem=lambda *a, **k: None)
    fake.Menu.SEPARATOR = None
    monkeypatch.setattr(desktop, "_pystray", fake)
    b = desktop.Tray(None, lambda: None, lambda: None, lambda: None, lambda: False)
    assert b.show() is False and not b.visible
    assert InvisibleIcon.stopped.wait(2)


def test_anchored():
    import types
    assert not desktop._anchored(types.SimpleNamespace(visible=False))
    assert desktop._anchored(types.SimpleNamespace(visible=True))  # Windows / macOS
    assert not desktop._anchored(types.SimpleNamespace(visible=True, _systray_manager=None))  # X11 sin bandeja
    assert desktop._anchored(types.SimpleNamespace(visible=True, _systray_manager=123))


# ------------------------------------------------------------------ descarga con límite (de punta a punta)
@needs_ffmpeg
def test_download_with_speed_limit(tmp_path, monkeypatch):
    """Con el límite activo yt-dlp recibe 'ratelimit' y la descarga igual termina."""
    import functools
    import http.server

    from test_engine import make_audio
    root = tmp_path / "srv"
    root.mkdir()
    make_audio(root / "c.m4a", seconds=2)
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root)))
    httpd.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    views = []
    real = m.YoutubeDL

    class Spy(real):
        def __init__(self, params=None, *a, **k):
            views.append((params or {}).get("ratelimit"))
            super().__init__(params, *a, **k)
    monkeypatch.setattr(m, "YoutubeDL", Spy)
    events = []
    cfg = dict(m.DEFAULT_CONFIG, folder=str(tmp_path / "dl"), audio_format="mp3", replaygain=False,
               speed_limit_enabled=True, speed_limit_mb=4, parallel_downloads=2)
    try:
        m.run_job(f"http://127.0.0.1:{httpd.server_address[1]}/c.m4a", cfg, events.append,
                           threading.Event())
    finally:
        httpd.shutdown()
    assert [e for e in events if e[0] == "done"]
    assert 2 * 1024 * 1024 in views  # 4 MB/s repartidos entre 2 descargas
