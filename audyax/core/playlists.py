"""The player's own playlists: one .m3u8 file each (UTF-8 extended M3U), in a "Listas"
folder inside the music library, so other players (VLC, foobar, phones…) can open them
too. No UI code here.

Paths are written relative to the playlist's folder when possible, so the lists keep
working if the whole music folder is moved. Songs whose file is missing are kept (they may
come back); the player just skips them."""

import os
import re
from pathlib import Path

FOLDER_NAME = "Listas"
EXTENSIONS = (".m3u8", ".m3u")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def playlists_folder(library_folder) -> Path:
    return Path(library_folder) / FOLDER_NAME


def safe_name(name: str) -> str:
    """A playlist name that works as a file name on Windows, macOS and Linux."""
    name = _BAD_CHARS.sub(" ", str(name or "")).strip().strip(".")
    name = re.sub(r"\s+", " ", name)[:120]
    if name.upper() in {"CON", "PRN", "AUX", "NUL"} | {f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10)}:
        name = f"{name}_"
    return name


def list_playlists(library_folder):
    """[(name, path)] of the playlists, sorted by name (case and accents ignored)."""
    from audyax.core.library import normalize
    folder = playlists_folder(library_folder)
    if not folder.is_dir():
        return []
    found = [(p.stem, p) for p in folder.iterdir()
             if p.is_file() and p.suffix.lower() in EXTENSIONS and not p.name.startswith(".")]
    return sorted(found, key=lambda x: normalize(x[0]))


def read_playlist(path) -> list:
    """Absolute paths (as text) of the songs of an .m3u/.m3u8, in order. Comment lines
    (#EXTM3U, #EXTINF…) and URLs are skipped; relative paths are relative to the playlist."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")   # old .m3u files written by other programs
    base = path.parent
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "://" in line:
            continue
        p = Path(line.replace("\\", os.sep) if os.sep == "/" else line)
        out.append(str(p if p.is_absolute() else (base / p).resolve()))
    return out


def write_playlist(path, tracks):
    """Writes the playlist with `tracks` (dicts with "path" and, if known, "title",
    "artist", "duration"). Atomic: a crash never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    base = path.parent.resolve()
    lines = ["#EXTM3U"]
    for t in tracks:
        song = Path(t["path"])
        try:
            text = os.path.relpath(song.resolve(), base)
        except ValueError:   # another drive on Windows (C: vs D:)
            text = str(song.resolve())
        name = " - ".join(x for x in (t.get("artist"), t.get("title")) if x) or song.stem
        seconds = int(round(float(t.get("duration") or 0))) or -1
        lines += [f"#EXTINF:{seconds},{name}", text.replace("\\", "/") if os.sep == "/" else text]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def create_playlist(library_folder, name: str, tracks=()) -> Path:
    """Creates a new playlist. Raises ValueError if the name is empty or already used."""
    clean = safe_name(name)
    if not clean:
        raise ValueError("Escribe un nombre para la lista.")
    if _find(library_folder, clean):
        raise ValueError(f"Ya existe una lista llamada «{clean}».")
    path = playlists_folder(library_folder) / f"{clean}.m3u8"
    write_playlist(path, list(tracks))
    return path


def rename_playlist(library_folder, path, new_name: str) -> Path:
    path = Path(path)
    clean = safe_name(new_name)
    if not clean:
        raise ValueError("Escribe un nombre para la lista.")
    other = _find(library_folder, clean)
    if other and other.resolve() != path.resolve():
        raise ValueError(f"Ya existe una lista llamada «{clean}».")
    new = path.with_name(clean + path.suffix)
    if new.name != path.name:
        if new.name.lower() == path.name.lower():   # only the case changes (Windows/macOS)
            tmp = path.with_name(path.name + ".renaming")
            os.replace(path, tmp)
            path = tmp
        os.replace(path, new)
    return new


def delete_playlist(path):
    """Deletes the playlist file (never the songs)."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def _find(library_folder, name: str):
    from audyax.core.library import normalize
    return next((p for n, p in list_playlists(library_folder) if normalize(n) == normalize(name)), None)
