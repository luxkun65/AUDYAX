"""
Integración con el escritorio: icono en la bandeja del sistema y notificaciones.

Todo es opcional: si las librerías no están instaladas o el sistema no las soporta
(por ejemplo, Linux sin bandeja), AUDYAX funciona igual, solo que sin estas funciones.
Las librerías se cargan al usarlas por primera vez: pystray intenta conectarse al
escritorio al importarse y, si no puede, lanza un error que no es ImportError.

    pip install pystray plyer
"""
import os
import sys
import threading
import time

_pystray = None          # módulo pystray, una vez cargado
_pystray_error = None    # por qué no se pudo cargar (texto), o None


def _load_pystray():
    global _pystray, _pystray_error
    if _pystray is None and _pystray_error is None:
        try:
            import pystray
            _pystray = pystray
        except Exception as e:  # ImportError, o sin escritorio/bandeja en este sistema
            _pystray_error = f"{type(e).__name__}: {e}"
            # En Linux pystray prueba primero GTK/AppIndicator; si no están, se reintenta con X11
            if not sys.platform.startswith(("win", "darwin")) and not os.environ.get("PYSTRAY_BACKEND"):
                for name in [m for m in sys.modules if m == "pystray" or m.startswith("pystray.")]:
                    del sys.modules[name]
                os.environ["PYSTRAY_BACKEND"] = "xorg"
                try:
                    import pystray
                    _pystray, _pystray_error = pystray, None
                except Exception as e2:
                    _pystray_error = f"{type(e2).__name__}: {e2}"
    return _pystray


def tray_available() -> bool:
    return _load_pystray() is not None


def no_tray_reason() -> str:
    _load_pystray()
    return _pystray_error or ""


def notifications_available() -> bool:
    try:
        from plyer import notification  # noqa: F401
        return True
    except Exception:
        return False


class Tray:
    """Icono de AUDYAX junto al reloj, con menú: Abrir, Pausar/Reanudar y Salir.

    Las funciones que se pasan (al_abrir, al_pausar, al_salir) se llaman desde el hilo de
    la bandeja, NO desde el de la ventana: quien las pase debe reenviarlas a la ventana
    (AUDYAX las mete en su cola de eventos)."""

    def __init__(self, image, on_open, on_pause, on_quit, is_paused):
        self.image = image
        self.on_open, self.on_pause, self.on_quit = on_open, on_pause, on_quit
        self.is_paused = is_paused
        self.icon = None
        self._lock = threading.Lock()

    @property
    def visible(self) -> bool:
        return self.icon is not None

    def show(self, text: str = "AUDYAX") -> bool:
        pystray = _load_pystray()
        if pystray is None:
            return False
        with self._lock:
            if self.icon is not None:
                return True
            menu = pystray.Menu(
                pystray.MenuItem("Abrir AUDYAX", lambda *a: self.on_open(), default=True),
                pystray.MenuItem(lambda item: "Reanudar descargas" if self.is_paused()
                                 else "Pausar descargas", lambda *a: self.on_pause()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", lambda *a: self.on_quit()),
            )
            try:
                icon = pystray.Icon("AUDYAX", self.image, text, menu)
                # Hilo "daemon": si la librería se queda colgada, no impide cerrar AUDYAX
                threading.Thread(target=icon.run, daemon=True, name="tray").start()
            except Exception:
                return False
            # Solo se da por bueno si el icono aparece de verdad. Si el sistema no tiene bandeja
            # (algunos Linux), NO se debe esconder la ventana: no habría cómo volver a abrirla.
            limit = time.monotonic() + 3
            while time.monotonic() < limit and not _anchored(icon):
                time.sleep(0.05)
            if not _anchored(icon):
                _stop_in_background(icon)
                return False
            self.icon = icon
        return True

    def hide(self):
        with self._lock:
            icon, self.icon = self.icon, None
        if icon is not None:
            _stop_in_background(icon)

    def update_text(self, text: str):
        if self.icon is not None:
            try:
                self.icon.title = text
                self.icon.update_menu()
            except Exception:
                pass

    def notify(self, title: str, message: str) -> bool:
        """Aviso desde el propio icono (Windows lo muestra como notificación)."""
        if self.icon is None or not getattr(self.icon, "HAS_NOTIFICATION", False):
            return False
        try:
            self.icon.notify(message, title)
            return True
        except Exception:
            return False


def _anchored(icon) -> bool:
    """El icono se ve de verdad. En Linux/X11 pystray lo marca 'visible' aunque no haya
    bandeja donde ponerlo (lo deja pendiente); ahí se mira si encontró la bandeja."""
    if not icon.visible:
        return False
    if hasattr(icon, "_systray_manager"):  # solo existe en el modo X11
        return bool(icon._systray_manager)
    return True


def _stop_in_background(icon):
    """icon.stop() puede tardar unos segundos en algunos sistemas: que no congele la ventana."""
    def stop():
        try:
            icon.stop()
        except Exception:
            pass
    threading.Thread(target=stop, daemon=True).start()


def notify(title: str, message: str, ico_icon: str = "") -> bool:
    """Notificación del sistema (en Windows, el aviso de la esquina inferior derecha).
    Devuelve False si no se pudo mostrar; nunca lanza excepciones."""
    try:
        from plyer import notification
        kw = {"title": title[:63], "message": message[:255], "app_name": "AUDYAX", "timeout": 8}
        if ico_icon and sys.platform.startswith("win"):
            kw["app_icon"] = ico_icon
        notification.notify(**kw)
        return True
    except Exception:
        return False


def batch_summary(downloaded: int, errors: int, skipped: int = 0) -> str:
    """Texto de la notificación final, p. ej. '78 canciones descargadas · 2 con error'."""
    parts = []
    if downloaded:
        parts.append(f"{downloaded} {'canción descargada' if downloaded == 1 else 'canciones descargadas'}")
    if skipped:
        parts.append(f"{skipped} ya estaba{'n' if skipped != 1 else ''} descargada{'s' if skipped != 1 else ''}")
    if errors:
        parts.append(f"{errors} con error")
    return " · ".join(parts) or "No se descargó ninguna canción"
