"""Music library of the player (part 2): scans a folder, reads each song's tags and keeps
them in a cache so the next scans only read new or changed files. No UI code here.

    lib = Library(folder)
    lib.load()                      # instant: what the cache already knows
    lib.scan(progress=..., cancel=...)   # reads only what changed (in a thread)
    lib.tracks, lib.artists(), lib.albums(), search(lib.tracks, "text")
"""

import json
import os
import threading
import unicodedata
from collections import Counter
from pathlib import Path

try:
    from mutagen import File as MutagenFile
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

CACHE_PATH = Path.home() / ".audyax_library.json"
CACHE_VERSION = 1

# Formats FFmpeg can play; the player decodes everything through FFmpeg
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".mp4", ".aac", ".opus", ".ogg", ".oga", ".flac", ".wav",
                    ".wma", ".webm", ".aiff", ".aif", ".alac", ".mka"}

NO_ARTIST = "Artista desconocido"   # shown to the user
NO_ALBUM = "Álbum desconocido"
VARIOUS_ARTISTS = "Varios artistas"

TRACK_FIELDS = ("path", "title", "artist", "album", "year", "genre", "duration", "mtime", "size")


def normalize(text) -> str:
    """For comparing and searching: lower case and without accents ("Canción" == "cancion")."""
    text = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(c for c in text if not unicodedata.combining(c)).strip()


def _first(value) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def read_track(path) -> dict:
    """Tags and duration of a file. Never fails: what can't be read stays empty and the
    title falls back to the file name."""
    p = Path(path)
    st = p.stat()
    track = {"path": str(p), "title": "", "artist": "", "album": "", "year": "", "genre": "",
             "duration": 0.0, "mtime": st.st_mtime, "size": st.st_size}
    if HAS_MUTAGEN:
        try:
            audio = MutagenFile(p, easy=True)   # same keys for MP3, M4A, Opus, FLAC…
            if audio is not None:
                tags = audio.tags or {}
                for field, key in (("title", "title"), ("artist", "artist"), ("album", "album"),
                                   ("year", "date"), ("genre", "genre")):
                    try:
                        track[field] = _first(tags.get(key))
                    except Exception:
                        pass
                if audio.info is not None:
                    track["duration"] = float(getattr(audio.info, "length", 0) or 0)
        except Exception:
            pass
    year = track["year"]
    track["year"] = year[:4] if year[:4].isdigit() else year
    if not track["title"]:
        track["title"] = p.stem
    return track


def find_audio_files(folder):
    """All the audio files under `folder` (hidden folders are skipped)."""
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if Path(name).suffix.lower() in AUDIO_EXTENSIONS and not name.startswith("."):
                yield Path(root) / name


def artist_of(track) -> str:
    return track.get("artist") or NO_ARTIST


def album_of(track) -> str:
    return track.get("album") or NO_ALBUM


def album_key(track) -> str:
    """Songs of the same album may be in different folders but have the same album name;
    unknown albums are grouped by folder so they don't all end up together."""
    if track.get("album"):
        return normalize(track["album"])
    return "\0" + str(Path(track["path"]).parent)


def search(tracks, query: str):
    """Songs whose title, artist, album or file name contain every word of `query`."""
    words = normalize(query).split()
    if not words:
        return list(tracks)
    out = []
    for t in tracks:
        text = normalize(" ".join((t.get("title", ""), t.get("artist", ""), t.get("album", ""),
                                   Path(t["path"]).stem)))
        if all(w in text for w in words):
            out.append(t)
    return out


SORT_FIELDS = ("title", "artist", "album", "year", "duration")


def sort_tracks(tracks, field: str = "artist", reverse: bool = False):
    """Sorts by a column; ties are broken by artist, album and title (the natural order)."""
    def key(t):
        main = t.get(field) or ""
        main = (float(main or 0),) if field == "duration" else (main == "", normalize(main))
        return main + (normalize(artist_of(t)), normalize(album_of(t)), normalize(t.get("title")),
                       t["path"])
    return sorted(tracks, key=key, reverse=reverse)


def format_duration(seconds) -> str:
    seconds = int(round(float(seconds or 0)))
    if seconds <= 0:
        return "—"
    h, rest = divmod(seconds, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"


class Library:
    """The songs of one folder. `tracks` is a list of dicts (see TRACK_FIELDS)."""

    def __init__(self, folder, cache_path=None):
        self.folder = str(Path(folder).expanduser())
        self.cache_path = Path(cache_path) if cache_path else CACHE_PATH
        self.tracks = []
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- cache
    def load(self) -> bool:
        """Loads the cache if it belongs to this folder. Returns True if there was one."""
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if data.get("version") != CACHE_VERSION or data.get("folder") != self.folder:
            return False
        tracks = [t for t in data.get("tracks", []) if isinstance(t, dict) and t.get("path")]
        with self._lock:
            self.tracks = [{f: t.get(f, "") for f in TRACK_FIELDS} for t in tracks]
        return True

    def save(self):
        data = {"version": CACHE_VERSION, "folder": self.folder, "tracks": self.tracks}
        tmp = self.cache_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.cache_path)
        except OSError:
            pass  # without a cache the next start just scans everything again

    # ---------------------------------------------------------------- scan
    def scan(self, progress=None, cancel=None) -> dict:
        """Brings the library up to date with the folder. Only new or modified files are
        read (by date and size). progress(done, total) is called now and then;
        if cancel() returns True the scan stops and nothing changes.
        Returns {"added": n, "updated": n, "removed": n, "total": n} or None if cancelled."""
        known = {t["path"]: t for t in self.tracks}
        files = list(find_audio_files(self.folder)) if Path(self.folder).is_dir() else []
        total = len(files)
        new_tracks, added, updated = [], 0, 0
        for i, f in enumerate(files):
            if cancel and cancel():
                return None
            path = str(f)
            try:
                st = f.stat()
            except OSError:
                continue
            old = known.get(path)
            if old and old.get("mtime") == st.st_mtime and old.get("size") == st.st_size:
                new_tracks.append(old)
            else:
                try:
                    new_tracks.append(read_track(f))
                except OSError:
                    continue
                if old:
                    updated += 1
                else:
                    added += 1
            if progress and (i % 25 == 0 or i == total - 1):
                progress(i + 1, total)
        present = {t["path"] for t in new_tracks}
        removed = sum(1 for p in known if p not in present)
        with self._lock:
            self.tracks = new_tracks
        if added or updated or removed or not self.cache_path.exists():
            self.save()
        return {"added": added, "updated": updated, "removed": removed, "total": len(new_tracks)}

    # ---------------------------------------------------------------- groups
    def artists(self):
        """[(artist, number of songs, number of albums)] sorted by name."""
        songs, albums, names = Counter(), {}, {}
        for t in self.tracks:
            k = normalize(artist_of(t))
            names.setdefault(k, artist_of(t))
            songs[k] += 1
            albums.setdefault(k, set()).add(album_key(t))
        return sorted(((names[k], songs[k], len(albums[k])) for k in songs),
                      key=lambda a: (a[0] == NO_ARTIST, normalize(a[0])))

    def albums(self):
        """[{"key", "album", "artist", "year", "count", "first"}] sorted by album name.
        The artist is the one most of its songs have, or "Varios artistas"."""
        groups = {}
        for t in self.tracks:
            groups.setdefault(album_key(t), []).append(t)
        out = []
        for key, songs in groups.items():
            artists = Counter(normalize(artist_of(t)) for t in songs)   # "Soda Stereo" == "soda stereo"
            top, n = artists.most_common(1)[0]
            artist = next(artist_of(t) for t in songs if normalize(artist_of(t)) == top)
            if len(artists) > 1 and n <= len(songs) / 2:
                artist = VARIOUS_ARTISTS
            years = sorted(t["year"] for t in songs if t.get("year"))
            out.append({"key": key, "album": album_of(songs[0]), "artist": artist,
                        "year": years[0] if years else "", "count": len(songs),
                        "first": sorted(songs, key=lambda t: t["path"])[0]})
        return sorted(out, key=lambda a: (a["album"] == NO_ALBUM, normalize(a["album"]),
                                          normalize(a["artist"])))

    def tracks_of_artist(self, artist: str):
        k = normalize(artist)
        return [t for t in self.tracks if normalize(artist_of(t)) == k]

    def tracks_of_album(self, key: str):
        return [t for t in self.tracks if album_key(t) == key]

    def total_duration(self, tracks=None) -> float:
        return sum(float(t.get("duration") or 0) for t in (self.tracks if tracks is None else tracks))
