"""
Letras de canciones: búsqueda en LRCLIB (https://lrclib.net), guardado como archivo .lrc
junto a la canción y/o dentro del propio archivo de audio, y lectura para el reproductor.

LRCLIB es gratuito, no necesita clave y devuelve dos versiones de la letra:
  - sincronizada ("syncedLyrics", formato LRC: "[01:23.45] línea"), que los reproductores
    muestran al ritmo de la canción;
  - texto plano ("plainLyrics").

Este módulo no depende de la interfaz: lo usa el motor de descarga y lo usará el reproductor.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from servicios import Interruptor, ServicioNoDisponible
from pathlib import Path

URL_API = "https://lrclib.net/api"
AGENTE = "AUDYAX (https://github.com/luxkun65/AUDYAX)"
TOLERANCIA_SEGUNDOS = 3   # diferencia de duración aceptada al elegir entre resultados

# Qué hacer con las letras (Configuración → Descargas). "no" = no buscarlas.
MODOS_LETRAS = ("no", "lrc", "incrustada", "ambas")

try:
    from mutagen.id3 import ID3, USLT, ID3NoHeaderError
    from mutagen.mp4 import MP4
    from mutagen.oggopus import OggOpus
    HAY_MUTAGEN = True
except ImportError:
    HAY_MUTAGEN = False


# ==============================================================================
# BÚSQUEDA EN LRCLIB
# ==============================================================================
interruptor = Interruptor()  # si LRCLIB falla varias veces seguidas, se deja un rato


def _pedir(ruta: str, parametros: dict, timeout: int = 10, proxy: str = ""):
    url = f"{URL_API}/{ruta}?{urllib.parse.urlencode({k: v for k, v in parametros.items() if v})}"
    pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    abridor = urllib.request.build_opener(
        *([urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy.startswith("http") else []))
    if not interruptor.disponible():
        raise ServicioNoDisponible("LRCLIB no respondió hace poco; se reintentará más tarde")
    try:
        with abridor.open(pedido, timeout=timeout) as r:
            datos = json.loads(r.read().decode("utf-8"))
        interruptor.exito()
        return datos
    except urllib.error.HTTPError as e:
        interruptor.exito()  # el servicio respondió (aunque sea "no encontrado")
        if e.code == 404:
            return None
        raise
    except (urllib.error.URLError, TimeoutError, OSError):
        interruptor.fallo()
        raise


def _resultado(dato: dict) -> dict:
    return {"sincronizada": (dato.get("syncedLyrics") or "").strip() or None,
            "texto": (dato.get("plainLyrics") or "").strip() or None,
            "instrumental": bool(dato.get("instrumental")),
            "id": dato.get("id")}


def buscar_letra(titulo: str, artista: str, album: str = "", duracion=None, proxy: str = ""):
    """Busca la letra de una canción. Devuelve un dict con 'sincronizada', 'texto' e
    'instrumental', o None si no hay. Lanza la excepción si falla la conexión.

    1) /api/get con título, artista, álbum y duración (coincidencia exacta, ±2 s).
    2) Si no hay, /api/search con título y artista, y se elige el resultado de duración
       más parecida (preferiendo los que traen letra sincronizada)."""
    titulo, artista = (titulo or "").strip(), (artista or "").strip()
    if not titulo or not artista:
        return None
    dur = int(round(duracion)) if duracion else None
    if dur:
        dato = _pedir("get", {"track_name": titulo, "artist_name": artista, "album_name": album,
                              "duration": dur}, proxy=proxy)
        if dato and (dato.get("syncedLyrics") or dato.get("plainLyrics") or dato.get("instrumental")):
            return _resultado(dato)
    candidatos = _pedir("search", {"track_name": titulo, "artist_name": artista}, proxy=proxy) or []
    candidatos = [c for c in candidatos if c.get("syncedLyrics") or c.get("plainLyrics")]
    if dur:
        candidatos = [c for c in candidatos
                      if c.get("duration") is None or abs(c["duration"] - dur) <= TOLERANCIA_SEGUNDOS]
    if not candidatos:
        return None
    candidatos.sort(key=lambda c: (not c.get("syncedLyrics"),
                                   abs((c.get("duration") or dur or 0) - (dur or 0))))
    return _resultado(candidatos[0])


def texto_para_guardar(letra: dict) -> str:
    """La versión que se guarda: la sincronizada si existe (sirve en ambos casos: los
    reproductores que no la entienden muestran las líneas igual), si no, la de texto."""
    if not letra:
        return ""
    return letra.get("sincronizada") or letra.get("texto") or ""


# ==============================================================================
# GUARDAR
# ==============================================================================
def ruta_lrc(ruta_audio) -> Path:
    return Path(ruta_audio).with_suffix(".lrc")


def guardar_lrc(ruta_audio, letra: dict) -> Path:
    """Escribe 'Canción.lrc' junto a 'Canción.mp3' (así lo buscan los reproductores)."""
    destino = ruta_lrc(ruta_audio)
    destino.write_text(texto_para_guardar(letra) + "\n", encoding="utf-8")
    return destino


def incrustar_letra(ruta_audio, letra: dict) -> bool:
    """Guarda la letra dentro del archivo: USLT (MP3), ©lyr (M4A) o LYRICS (Opus).
    Devuelve False si el formato no lo admite."""
    texto = texto_para_guardar(letra)
    if not HAY_MUTAGEN or not texto:
        return False
    ruta = Path(ruta_audio)
    ext = ruta.suffix.lower()
    if ext == ".mp3":
        try:
            tags = ID3(ruta)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("USLT")
        tags.add(USLT(encoding=3, lang="und", desc="", text=texto))
        tags.save(ruta, v2_version=3)
    elif ext in (".m4a", ".mp4"):
        audio = MP4(ruta)
        if audio.tags is None:
            audio.add_tags()
        audio.tags["\xa9lyr"] = [texto]
        audio.save()
    elif ext == ".opus":
        audio = OggOpus(ruta)
        audio["LYRICS"] = [texto]
        audio.save()
    else:
        return False
    return True


def guardar_letra(ruta_audio, letra: dict, modo: str) -> list:
    """Guarda según el modo de la configuración. Devuelve dónde quedó ("lrc", "incrustada")."""
    hecho = []
    if not letra or not texto_para_guardar(letra) or modo not in MODOS_LETRAS or modo == "no":
        return hecho
    if modo in ("lrc", "ambas"):
        guardar_lrc(ruta_audio, letra)
        hecho.append("lrc")
    if modo in ("incrustada", "ambas") and incrustar_letra(ruta_audio, letra):
        hecho.append("incrustada")
    return hecho


# ==============================================================================
# LEER (para el reproductor)
# ==============================================================================
def leer_letra(ruta_audio) -> str:
    """Letra de una canción ya descargada: primero el .lrc de al lado, si no, la incrustada."""
    lrc = ruta_lrc(ruta_audio)
    if lrc.is_file():
        try:
            return lrc.read_text(encoding="utf-8-sig").strip()
        except OSError:
            pass
    if not HAY_MUTAGEN:
        return ""
    ruta = Path(ruta_audio)
    ext = ruta.suffix.lower()
    try:
        if ext == ".mp3":
            marcos = ID3(ruta).getall("USLT")
            return str(marcos[0].text).strip() if marcos else ""
        if ext in (".m4a", ".mp4"):
            valor = (MP4(ruta).tags or {}).get("\xa9lyr")
            return str(valor[0]).strip() if valor else ""
        if ext == ".opus":
            valor = OggOpus(ruta).get("lyrics") or OggOpus(ruta).get("unsyncedlyrics")
            return str(valor[0]).strip() if valor else ""
    except Exception:
        return ""
    return ""


_RE_TIEMPO = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def parsear_lrc(texto: str) -> list:
    """Convierte una letra LRC en [(segundos, línea), ...] ordenada. Una línea puede tener
    varias marcas de tiempo ("[00:10.00][01:20.00] estribillo"). Las etiquetas como [ar:…]
    se ignoran. Si la letra no está sincronizada, devuelve []."""
    lineas = []
    for cruda in (texto or "").splitlines():
        marcas = list(_RE_TIEMPO.finditer(cruda))
        if not marcas:
            continue
        resto = cruda[marcas[-1].end():].strip()
        for m in marcas:
            fraccion = m.group(3) or "0"
            segundos = int(m.group(1)) * 60 + int(m.group(2)) + int(fraccion) / (10 ** len(fraccion))
            lineas.append((segundos, resto))
    lineas.sort(key=lambda x: x[0])
    return lineas


def linea_actual(lineas: list, segundos: float) -> int:
    """Índice de la línea que corresponde al momento dado (-1 antes de la primera)."""
    actual = -1
    for i, (t, _) in enumerate(lineas):
        if t <= segundos + 0.05:
            actual = i
        else:
            break
    return actual
