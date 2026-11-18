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

import motor_descarga as motor


def es_playlist(url: str) -> bool:
    """¿El enlace es de una playlist (o álbum)? Las "radios" automáticas (list=RD…) no cuentan:
    cambian cada vez y no tiene sentido vigilarlas."""
    partes = urllib.parse.urlsplit((url or "").strip())
    if "youtube.com" not in partes.netloc and "youtu.be" not in partes.netloc:
        return False
    if partes.path.startswith("/browse/MPRE"):
        return True
    lista = (urllib.parse.parse_qs(partes.query).get("list") or [""])[0]
    return bool(lista) and not lista.startswith("RD")


def url_playlist(url: str) -> str:
    """Forma canónica para guardarla y comparar: solo la playlist (sin la canción en la que
    estaba el usuario al copiarla)."""
    partes = urllib.parse.urlsplit(url.strip())
    if partes.path.startswith("/browse/"):
        return f"https://{partes.netloc}{partes.path}"
    lista = urllib.parse.parse_qs(partes.query)["list"][0]
    host = "music.youtube.com" if partes.netloc.startswith("music.") else "www.youtube.com"
    return f"https://{host}/playlist?list={lista}"


def nueva(url: str, carpeta: str, nombre: str = "") -> dict:
    if not es_playlist(url):
        raise ValueError("Ese enlace no es de una playlist de YouTube o YouTube Music.")
    return {"url": url_playlist(url), "nombre": nombre or "", "carpeta": str(carpeta),
            "agregada": datetime.now().isoformat(timespec="seconds"), "ultima_revision": "",
            "total": 0, "nuevas": 0, "error": ""}


def agregar(lista: list, url: str, carpeta: str, nombre: str = "") -> dict:
    """Agrega una playlist (si ya estaba, devuelve la existente). Modifica `lista`."""
    v = nueva(url, carpeta, nombre)
    for existente in lista:
        if existente["url"] == v["url"]:
            return existente
    lista.append(v)
    return v


def quitar(lista: list, url: str):
    lista[:] = [v for v in lista if v["url"] != url]


def revisar(vigilada: dict, red=None) -> list:
    """Consulta la playlist y devuelve las canciones que aún no se bajaron a su carpeta.
    Actualiza en `vigilada` el nombre, el total, cuántas nuevas hay y la fecha de revisión.
    Si falla, guarda el motivo en vigilada["error"] y devuelve []."""
    carpeta = Path(vigilada["carpeta"])
    vigilada["ultima_revision"] = datetime.now().isoformat(timespec="seconds")
    try:
        plan = motor.resolver(vigilada["url"], carpeta, "", None, red)
    except Exception as e:
        vigilada["error"] = str(e)
        return []
    entradas = plan.get("entradas") or []
    if not plan.get("es_playlist") or not entradas:
        vigilada["error"] = "No se pudo leer la playlist (¿es privada o ya no existe?)."
        return []
    ya_estan = motor.ids_en_archivo(carpeta)
    nuevas = [e for e in entradas if e["id"] not in ya_estan]
    vigilada.update(nombre=plan.get("album_defecto") or vigilada.get("nombre") or vigilada["url"],
                    total=len(entradas), nuevas=len(nuevas), error="")
    return nuevas


def texto_revision(vigilada: dict) -> str:
    """Resumen para la lista: "25 canciones · 3 nuevas · revisada hace 2 h"."""
    if vigilada.get("error"):
        return "No se pudo revisar: " + vigilada["error"]
    if not vigilada.get("ultima_revision"):
        return "Sin revisar todavía"
    partes = [f"{vigilada.get('total', 0)} canciones"]
    n = vigilada.get("nuevas", 0)
    partes.append(f"{n} nueva{'s' if n != 1 else ''}" if n else "al día")
    partes.append("revisada " + hace(vigilada["ultima_revision"]))
    return " · ".join(partes)


def hace(fecha_iso: str, ahora: datetime = None) -> str:
    try:
        segundos = ((ahora or datetime.now()) - datetime.fromisoformat(fecha_iso)).total_seconds()
    except (TypeError, ValueError):
        return ""
    if segundos < 60:
        return "hace un momento"
    for unidad, tam in (("día", 86400), ("h", 3600), ("min", 60)):
        if segundos >= tam:
            n = int(segundos // tam)
            return f"hace {n} {unidad}{'s' if unidad == 'día' and n != 1 else ''}"
    return ""
