"""
Pequeña protección compartida por las consultas a servicios externos (MusicBrainz, LRCLIB).

Si un servicio está caído o muy lento, cada canción tendría que esperar su propio tiempo
límite. Tras varios fallos de conexión seguidos, se deja de consultarlo un rato y las
descargas siguen sin esos datos extra (que son un "plus", no algo imprescindible).
"""
import threading
import time


class Interruptor:
    """Corta las consultas a un servicio tras `fallos_max` fallos seguidos, durante `pausa` s."""

    def __init__(self, fallos_max: int = 3, pausa: float = 300):
        self.fallos_max, self.pausa = fallos_max, pausa
        self._fallos, self._hasta = 0, 0.0
        self._candado = threading.Lock()

    def disponible(self) -> bool:
        with self._candado:
            return time.monotonic() >= self._hasta

    def exito(self):
        with self._candado:
            self._fallos = 0

    def fallo(self):
        with self._candado:
            self._fallos += 1
            if self._fallos >= self.fallos_max:
                self._hasta = time.monotonic() + self.pausa
                self._fallos = 0


class ServicioNoDisponible(RuntimeError):
    """Se lanza en lugar de consultar mientras el interruptor está cortado."""
