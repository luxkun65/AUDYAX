"""
Pequeña protección compartida por las consultas a servicios externos (MusicBrainz, LRCLIB).

Si un servicio está caído o muy lento, cada canción tendría que esperar su propio tiempo
límite. Tras varios fallos de conexión seguidos, se deja de consultarlo un rato y las
descargas siguen sin esos datos extra (que son un "plus", no algo imprescindible).
"""
import threading
import time


class CircuitBreaker:
    """Corta las consultas a un servicio tras `fallos_max` fallos seguidos, durante `pausa` s."""

    def __init__(self, max_failures: int = 3, pause: float = 300):
        self.max_failures, self.pause = max_failures, pause
        self._failures, self._until = 0, 0.0
        self._lock = threading.Lock()

    def available(self) -> bool:
        with self._lock:
            return time.monotonic() >= self._until

    def success(self):
        with self._lock:
            self._failures = 0

    def failure(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self.max_failures:
                self._until = time.monotonic() + self.pause
                self._failures = 0


class ServiceUnavailable(RuntimeError):
    """Se lanza en lugar de consultar mientras el interruptor está cortado."""
