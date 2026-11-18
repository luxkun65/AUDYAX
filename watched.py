"""
Playlists vigiladas: se guardan playlists (de YouTube o YouTube Music) y se revisa si tienen
canciones nuevas, para bajar solo esas.

La base es descargados.txt: cada carpeta de descargas guarda ahí los ids de lo que ya se bajó,
así que "nuevas" son las de la playlist que no están en ese archivo.

Se guardan dentro de la configuración de AUDYAX (clave "vigiladas"), como una lista de:
    {"url", "nombre", "carpeta", "agregada", "ultima_revision", "total", "nuevas", "error"}
Este módulo no depende de la interfaz.
"""
import urllib.parse
from datetime import datetime
from pathlib import Path

import engine as engine


def is_playlist(url: str) -> bool:
    """¿El enlace es de una playlist (o álbum)? Las "radios" automáticas (list=RD…) no cuentan:
    cambian cada vez y no tiene sentido vigilarlas."""
    parts = urllib.parse.urlsplit((url or "").strip())
    if "youtube.com" not in parts.netloc and "youtu.be" not in parts.netloc:
        return False
    if parts.path.startswith("/browse/MPRE"):
        return True
    song_list = (urllib.parse.parse_qs(parts.query).get("list") or [""])[0]
    return bool(song_list) and not song_list.startswith("RD")


def playlist_url(url: str) -> str:
    """Forma canónica para guardarla y comparar: solo la playlist (sin la canción en la que
    estaba el usuario al copiarla)."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.path.startswith("/browse/"):
        return f"https://{parts.netloc}{parts.path}"
    song_list = urllib.parse.parse_qs(parts.query)["list"][0]
    host = "music.youtube.com" if parts.netloc.startswith("music.") else "www.youtube.com"
    return f"https://{host}/playlist?list={song_list}"


def new_item(url: str, folder: str, name: str = "") -> dict:
    if not is_playlist(url):
        raise ValueError("Ese enlace no es de una playlist de YouTube o YouTube Music.")
    return {"url": playlist_url(url), "name": name or "", "folder": str(folder),
            "added": datetime.now().isoformat(timespec="seconds"), "last_check": "",
            "total": 0, "new_count": 0, "error": ""}


def add(song_list: list, url: str, folder: str, name: str = "") -> dict:
    """Agrega una playlist (si ya estaba, devuelve la existente). Modifica `lista`."""
    v = new_item(url, folder, name)
    for existing in song_list:
        if existing["url"] == v["url"]:
            return existing
    song_list.append(v)
    return v


def remove(song_list: list, url: str):
    song_list[:] = [v for v in song_list if v["url"] != url]


def check(watched_item: dict, net=None) -> list:
    """Consulta la playlist y devuelve las canciones que aún no se bajaron a su carpeta.
    Actualiza en `vigilada` el nombre, el total, cuántas nuevas hay y la fecha de revisión.
    Si falla, guarda el motivo en vigilada["error"] y devuelve []."""
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
    """Resumen para la lista: "25 canciones · 3 nuevas · revisada hace 2 h"."""
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
