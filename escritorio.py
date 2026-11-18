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


def _cargar_pystray():
    global _pystray, _pystray_error
    if _pystray is None and _pystray_error is None:
        try:
            import pystray
            _pystray = pystray
        except Exception as e:  # ImportError, o sin escritorio/bandeja en este sistema
            _pystray_error = f"{type(e).__name__}: {e}"
            # En Linux pystray prueba primero GTK/AppIndicator; si no están, se reintenta con X11
            if not sys.platform.startswith(("win", "darwin")) and not os.environ.get("PYSTRAY_BACKEND"):
                for nombre in [m for m in sys.modules if m == "pystray" or m.startswith("pystray.")]:
                    del sys.modules[nombre]
                os.environ["PYSTRAY_BACKEND"] = "xorg"
                try:
                    import pystray
                    _pystray, _pystray_error = pystray, None
                except Exception as e2:
                    _pystray_error = f"{type(e2).__name__}: {e2}"
    return _pystray


def bandeja_disponible() -> bool:
    return _cargar_pystray() is not None


def motivo_sin_bandeja() -> str:
    _cargar_pystray()
    return _pystray_error or ""


def notificaciones_disponibles() -> bool:
    try:
        from plyer import notification  # noqa: F401
        return True
    except Exception:
        return False


class Bandeja:
    """Icono de AUDYAX junto al reloj, con menú: Abrir, Pausar/Reanudar y Salir.

    Las funciones que se pasan (al_abrir, al_pausar, al_salir) se llaman desde el hilo de
    la bandeja, NO desde el de la ventana: quien las pase debe reenviarlas a la ventana
    (AUDYAX las mete en su cola de eventos)."""

    def __init__(self, imagen, al_abrir, al_pausar, al_salir, esta_en_pausa):
        self.imagen = imagen
        self.al_abrir, self.al_pausar, self.al_salir = al_abrir, al_pausar, al_salir
        self.esta_en_pausa = esta_en_pausa
        self.icono = None
        self._candado = threading.Lock()

    @property
    def visible(self) -> bool:
        return self.icono is not None

    def mostrar(self, texto: str = "AUDYAX") -> bool:
        pystray = _cargar_pystray()
        if pystray is None:
            return False
        with self._candado:
            if self.icono is not None:
                return True
            menu = pystray.Menu(
                pystray.MenuItem("Abrir AUDYAX", lambda *a: self.al_abrir(), default=True),
                pystray.MenuItem(lambda item: "Reanudar descargas" if self.esta_en_pausa()
                                 else "Pausar descargas", lambda *a: self.al_pausar()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Salir", lambda *a: self.al_salir()),
            )
            try:
                icono = pystray.Icon("AUDYAX", self.imagen, texto, menu)
                # Hilo "daemon": si la librería se queda colgada, no impide cerrar AUDYAX
                threading.Thread(target=icono.run, daemon=True, name="bandeja").start()
            except Exception:
                return False
            # Solo se da por bueno si el icono aparece de verdad. Si el sistema no tiene bandeja
            # (algunos Linux), NO se debe esconder la ventana: no habría cómo volver a abrirla.
            limite = time.monotonic() + 3
            while time.monotonic() < limite and not _anclado(icono):
                time.sleep(0.05)
            if not _anclado(icono):
                _detener_en_segundo_plano(icono)
                return False
            self.icono = icono
        return True

    def ocultar(self):
        with self._candado:
            icono, self.icono = self.icono, None
        if icono is not None:
            _detener_en_segundo_plano(icono)

    def actualizar_texto(self, texto: str):
        if self.icono is not None:
            try:
                self.icono.title = texto
                self.icono.update_menu()
            except Exception:
                pass

    def notificar(self, titulo: str, mensaje: str) -> bool:
        """Aviso desde el propio icono (Windows lo muestra como notificación)."""
        if self.icono is None or not getattr(self.icono, "HAS_NOTIFICATION", False):
            return False
        try:
            self.icono.notify(mensaje, titulo)
            return True
        except Exception:
            return False


def _anclado(icono) -> bool:
    """El icono se ve de verdad. En Linux/X11 pystray lo marca 'visible' aunque no haya
    bandeja donde ponerlo (lo deja pendiente); ahí se mira si encontró la bandeja."""
    if not icono.visible:
        return False
    if hasattr(icono, "_systray_manager"):  # solo existe en el modo X11
        return bool(icono._systray_manager)
    return True


def _detener_en_segundo_plano(icono):
    """icon.stop() puede tardar unos segundos en algunos sistemas: que no congele la ventana."""
    def detener():
        try:
            icono.stop()
        except Exception:
            pass
    threading.Thread(target=detener, daemon=True).start()


def notificar(titulo: str, mensaje: str, icono_ico: str = "") -> bool:
    """Notificación del sistema (en Windows, el aviso de la esquina inferior derecha).
    Devuelve False si no se pudo mostrar; nunca lanza excepciones."""
    try:
        from plyer import notification
        kw = {"title": titulo[:63], "message": mensaje[:255], "app_name": "AUDYAX", "timeout": 8}
        if icono_ico and sys.platform.startswith("win"):
            kw["app_icon"] = icono_ico
        notification.notify(**kw)
        return True
    except Exception:
        return False


def resumen_lote(descargadas: int, errores: int, omitidas: int = 0) -> str:
    """Texto de la notificación final, p. ej. '78 canciones descargadas · 2 con error'."""
    partes = []
    if descargadas:
        partes.append(f"{descargadas} {'canción descargada' if descargadas == 1 else 'canciones descargadas'}")
    if omitidas:
        partes.append(f"{omitidas} ya estaba{'n' if omitidas != 1 else ''} descargada{'s' if omitidas != 1 else ''}")
    if errores:
        partes.append(f"{errores} con error")
    return " · ".join(partes) or "No se descargó ninguna canción"
