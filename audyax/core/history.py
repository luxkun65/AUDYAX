"""Play history of the player: how many times each song was heard and when, for the smart
lists ("Más escuchadas", "Escuchadas recientemente"). No UI code here.

A song counts as heard when it played long enough (see counts_as_played); the player decides
that and calls record(). Stored in ~/.audyax_history.json (atomic writes)."""

import json
import os
import threading
import time
from pathlib import Path

HISTORY_PATH = Path.home() / ".audyax_history.json"
MIN_SECONDS = 30          # a song counts as heard after 30 s…
MIN_FRACTION = 0.5        # …or half of it, if it's shorter than a minute


def counts_as_played(listened: float, duration: float) -> bool:
    """Was enough of the song heard to count it?"""
    if duration and duration < 2 * MIN_SECONDS:
        return listened >= duration * MIN_FRACTION
    return listened >= MIN_SECONDS


class History:
    def __init__(self, path=None):
        self.path = Path(path) if path else HISTORY_PATH
        self._lock = threading.Lock()
        self.songs = {}          # path -> {"count": n, "last": epoch seconds, "first": epoch seconds}
        self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            songs = data.get("songs", {}) if isinstance(data, dict) else {}
            self.songs = {p: v for p, v in songs.items()
                          if isinstance(v, dict) and isinstance(v.get("count"), int)}
        except (OSError, ValueError):
            self.songs = {}

    def save(self):
        with self._lock:
            data = json.dumps({"version": 1, "songs": self.songs}, ensure_ascii=False)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def record(self, path, when=None):
        """One more play of `path`."""
        when = time.time() if when is None else when
        with self._lock:
            entry = self.songs.setdefault(str(path), {"count": 0, "first": when})
            entry["count"] += 1
            entry["last"] = when
        self.save()

    def count(self, path) -> int:
        return self.songs.get(str(path), {}).get("count", 0)

    def most_played(self, n: int = 50, existing=None):
        """Paths of the most played songs (ties: the most recent first). `existing` (a set of
        paths) leaves out songs that are no longer in the library."""
        items = [(p, v) for p, v in self.songs.items() if existing is None or p in existing]
        items.sort(key=lambda pv: (-pv[1]["count"], -pv[1].get("last", 0)))
        return [p for p, _ in items[:n]]

    def recent(self, n: int = 50, existing=None):
        """Paths of the songs heard most recently, newest first."""
        items = [(p, v) for p, v in self.songs.items()
                 if v.get("last") and (existing is None or p in existing)]
        items.sort(key=lambda pv: -pv[1]["last"])
        return [p for p, _ in items[:n]]

    def forget(self, path):
        with self._lock:
            self.songs.pop(str(path), None)
        self.save()


def recently_added(tracks, n: int = 50):
    """The `n` library songs added last (by the file's date), newest first."""
    return sorted(tracks, key=lambda t: -float(t.get("mtime") or 0))[:n]
