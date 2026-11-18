"""
Watched playlists: playlists (from YouTube or YouTube Music) are saved and checked for
new songs, to download only those.

It is based on descargados.txt: each download folder keeps the ids of what was already
downloaded there, so "new" songs are the playlist's songs that aren't in that file.

They are stored inside AUDYAX's settings (key "watched"), as a list of:
    {"url", "name", "folder", "added", "last_check", "total", "new_count", "error"}
This module doesn't depend on the UI.
"""
import urllib.parse
from datetime import datetime
from pathlib import Path

from audyax.downloader import engine


def is_playlist(url: str) -> bool:
    """Is the link a playlist (or album)? Automatic "radios" (list=RD…) don't count:
    they change every time and watching them makes no sense."""
    parts = urllib.parse.urlsplit((url or "").strip())
    if "youtube.com" not in parts.netloc and "youtu.be" not in parts.netloc:
        return False
    if parts.path.startswith("/browse/MPRE"):
        return True
    list_id = (urllib.parse.parse_qs(parts.query).get("list") or [""])[0]
    return bool(list_id) and not list_id.startswith("RD")


def playlist_url(url: str) -> str:
    """Canonical form for saving and comparing: only the playlist (without the song the
    user was on when copying it)."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.path.startswith("/browse/"):
        return f"https://{parts.netloc}{parts.path}"
    list_id = urllib.parse.parse_qs(parts.query)["list"][0]
    host = "music.youtube.com" if parts.netloc.startswith("music.") else "www.youtube.com"
    return f"https://{host}/playlist?list={list_id}"


def create(url: str, folder: str, name: str = "") -> dict:
    if not is_playlist(url):
        raise ValueError("Ese enlace no es de una playlist de YouTube o YouTube Music.")
    return {"url": playlist_url(url), "name": name or "", "folder": str(folder),
            "added": datetime.now().isoformat(timespec="seconds"), "last_check": "",
            "total": 0, "new_count": 0, "error": ""}


def add(watch_list: list, url: str, folder: str, name: str = "") -> dict:
    """Adds a playlist (if it was already there, returns the existing one). Modifies `watch_list`."""
    v = create(url, folder, name)
    for existing in watch_list:
        if existing["url"] == v["url"]:
            return existing
    watch_list.append(v)
    return v


def remove(watch_list: list, url: str):
    watch_list[:] = [v for v in watch_list if v["url"] != url]


def check(watched_item: dict, net=None) -> list:
    """Fetches the playlist and returns the songs not yet downloaded to its folder.
    Updates the name, the total, how many are new and the check date in `item`.
    If it fails, stores the reason in item["error"] and returns []."""
    folder = Path(watched_item["folder"])
    watched_item["last_check"] = datetime.now().isoformat(timespec="seconds")
    try:
        plan = engine.resolve(watched_item["url"], folder, "", None, net)
    except Exception as e:
        watched_item["error"] = str(e)
        return []
    entries = plan.get("entries") or []
    if not plan.get("is_playlist") or not entries:
        watched_item["error"] = "No se pudo leer la playlist (¿es privada o ya no existe?)."
        return []
    already_have = engine.ids_in_archive(folder)
    new_songs = [e for e in entries if e["id"] not in already_have]
    watched_item.update(name=plan.get("default_album") or watched_item.get("name") or watched_item["url"],
                    total=len(entries), new_count=len(new_songs), error="")
    return new_songs


def check_summary(watched_item: dict) -> str:
    """Summary for the list: "25 canciones · 3 nuevas · revisada hace 2 h"."""
    if watched_item.get("error"):
        return "No se pudo revisar: " + watched_item["error"]
    if not watched_item.get("last_check"):
        return "Sin revisar todavía"
    parts = [f"{watched_item.get('total', 0)} canciones"]
    n = watched_item.get("new_count", 0)
    parts.append(f"{n} nueva{'s' if n != 1 else ''}" if n else "al día")
    parts.append("revisada " + time_ago(watched_item["last_check"]))
    return " · ".join(parts)


def time_ago(iso_date: str, now: datetime = None) -> str:
    try:
        seconds = ((now or datetime.now()) - datetime.fromisoformat(iso_date)).total_seconds()
    except (TypeError, ValueError):
        return ""
    if seconds < 60:
        return "hace un momento"
    for unit, size in (("día", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            n = int(seconds // size)
            return f"hace {n} {unit}{'s' if unit == 'día' and n != 1 else ''}"
    return ""
