"""
Desktop integration: system tray icon and notifications.

Everything is optional: if the libraries aren't installed or the system doesn't support them
(for example, Linux without a tray), AUDYAX works the same, just without these features.
The libraries are loaded on first use: pystray tries to connect to the desktop
when imported and, if it can't, raises an error that isn't ImportError.

    pip install pystray plyer
"""
import os
import sys
import threading
import time

_pystray = None          # pystray module, once loaded
_pystray_error = None    # why it couldn't be loaded (text), or None


def _load_pystray():
    global _pystray, _pystray_error
    if _pystray is None and _pystray_error is None:
        try:
            import pystray
            _pystray = pystray
        except Exception as e:  # ImportError, or no desktop/tray on this system
            _pystray_error = f"{type(e).__name__}: {e}"
            # On Linux pystray tries GTK/AppIndicator first; if they're missing, retry with X11
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
    """AUDYAX icon next to the clock, with a menu: Abrir, Pausar/Reanudar and Salir.

    The functions passed in (on_open, on_pause, on_quit) are called from the tray's
    thread, NOT the window's: whoever passes them must forward them to the window
    (AUDYAX puts them in its event queue)."""

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
                # "daemon" thread: if the library hangs, it doesn't prevent AUDYAX from closing
                threading.Thread(target=icon.run, daemon=True, name="tray").start()
            except Exception:
                return False
            # Only accepted if the icon really appears. If the system has no tray
            # (some Linux setups), the window must NOT be hidden: there'd be no way to reopen it.
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
        """Notification from the icon itself (Windows shows it as a notification)."""
        if self.icon is None or not getattr(self.icon, "HAS_NOTIFICATION", False):
            return False
        try:
            self.icon.notify(message, title)
            return True
        except Exception:
            return False


def _anchored(icon) -> bool:
    """The icon is really visible. On Linux/X11 pystray marks it 'visible' even when there's
    no tray to put it in (it leaves it pending); there we check whether it found the tray."""
    if not icon.visible:
        return False
    if hasattr(icon, "_systray_manager"):  # only exists in X11 mode
        return bool(icon._systray_manager)
    return True


def _stop_in_background(icon):
    """icon.stop() can take a few seconds on some systems: don't freeze the window."""
    def stop():
        try:
            icon.stop()
        except Exception:
            pass
    threading.Thread(target=stop, daemon=True).start()


def notify(title: str, message: str, ico_icon: str = "") -> bool:
    """System notification (on Windows, the popup in the bottom right corner).
    Returns False if it couldn't be shown; never raises exceptions."""
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
    """Text of the final notification, e.g. '78 canciones descargadas · 2 con error'."""
    parts = []
    if downloaded:
        parts.append(f"{downloaded} {'canción descargada' if downloaded == 1 else 'canciones descargadas'}")
    if skipped:
        parts.append(f"{skipped} ya estaba{'n' if skipped != 1 else ''} descargada{'s' if skipped != 1 else ''}")
    if errors:
        parts.append(f"{errors} con error")
    return " · ".join(parts) or "No se descargó ninguna canción"
