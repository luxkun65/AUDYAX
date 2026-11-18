"""
Mejores metadatos y portadas con MusicBrainz (https://musicbrainz.org) y Cover Art Archive
(https://coverartarchive.org), para cuando YouTube trae datos pobres: un videoclip sin
álbum, o un año que en realidad es la fecha en que se subió el video.

Reglas de MusicBrainz que se respetan:
  - User-Agent que identifica la aplicación y cómo contactarla;
  - como mucho 1 petición por segundo (aunque haya varias descargas a la vez).

Es conservador a propósito: solo acepta una coincidencia si el título y el artista
coinciden y la duración se parece. Es mejor no completar un dato que completarlo mal.
"""
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from servicios import Interruptor, ServicioNoDisponible

URL_MB = "https://musicbrainz.org/ws/2"
URL_CAA = "https://coverartarchive.org"
AGENTE = "AUDYAX/1.0 ( https://github.com/luxkun65/AUDYAX )"
PUNTAJE_MINIMO = 85          # MusicBrainz da un "score" de 0 a 100 a cada resultado
TOLERANCIA_SEGUNDOS = 10     # diferencia de duración aceptada entre el video y la grabación

_candado = threading.Lock()
_ultima_peticion = [0.0]
_cache = {}
interruptor = Interruptor()  # si MusicBrainz falla varias veces seguidas, se deja un rato


# ==============================================================================
# PETICIONES (con el límite de 1 por segundo)
# ==============================================================================
def _pedir_mb(ruta: str, parametros: dict, timeout: int = 15, proxy: str = ""):
    parametros = dict(parametros, fmt="json")
    url = f"{URL_MB}/{ruta}?{urllib.parse.urlencode(parametros)}"
    pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Accept": "application/json"})
    abridor = urllib.request.build_opener(
        *([urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy.startswith("http") else []))
    if not interruptor.disponible():
        raise ServicioNoDisponible("MusicBrainz no respondió hace poco; se reintentará más tarde")
    with _candado:  # una petición a la vez, separadas al menos 1,1 s
        espera = _ultima_peticion[0] + 1.1 - time.monotonic()
        if espera > 0:
            time.sleep(espera)
        try:
            with abridor.open(pedido, timeout=timeout) as r:
                datos = json.loads(r.read().decode("utf-8"))
            interruptor.exito()
            return datos
        except (urllib.error.URLError, TimeoutError, OSError):
            interruptor.fallo()
            raise
        finally:
            _ultima_peticion[0] = time.monotonic()


# ==============================================================================
# COMPARAR TEXTOS
# ==============================================================================
_RUIDO = re.compile(
    r"[\(\[][^\)\]]*(official|oficial|video|vídeo|audio|lyric|letra|visualizer|hd|4k|remaster|"
    r"live|en vivo)[^\)\]]*[\)\]]", re.IGNORECASE)


def normalizar(texto: str) -> str:
    """Para comparar: sin tildes, en minúsculas, sin "(Official Video)", "feat. …" ni signos."""
    texto = _RUIDO.sub(" ", texto or "")
    texto = re.split(r"\s+(?:feat\.?|ft\.?|featuring|con)\s+", texto, flags=re.IGNORECASE)[0]
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c)).casefold()
    texto = texto.replace("&", " and ")
    return " ".join(re.sub(r"[^\w\s]", " ", texto).split())


def _coinciden(a: str, b: str) -> bool:
    """Iguales, o todas las palabras del más corto están (completas) en el otro:
    "One More Time" ~ "One More Time - Radio Edit", pero "Queen" no ~ "Queens of the Stone Age"."""
    a, b = normalizar(a), normalizar(b)
    if not a or not b:
        return False
    if a == b:
        return True
    corto, largo = sorted((a, b), key=len)
    return len(corto) >= 4 and set(corto.split()) <= set(largo.split())


def _artista_credito(grabacion: dict) -> str:
    return "".join((c.get("name") or "") + (c.get("joinphrase") or "")
                   for c in grabacion.get("artist-credit") or [])


# ==============================================================================
# ELEGIR LA MEJOR COINCIDENCIA
# ==============================================================================
def _puntos_lanzamiento(lanz: dict) -> tuple:
    """Para elegir el álbum: oficial, álbum de estudio, sin "recopilatorio" ni "en vivo",
    y el más antiguo (la edición original)."""
    grupo = lanz.get("release-group") or {}
    secundarios = grupo.get("secondary-types") or []
    tipo = grupo.get("primary-type") or ""
    return (lanz.get("status") != "Official",
            {"Album": 0, "EP": 1, "Single": 2}.get(tipo, 3),
            bool(secundarios),
            lanz.get("date") or "9999")


def elegir(grabaciones: list, titulo: str, artista: str, duracion=None):
    """Elige la grabación y el lanzamiento (álbum) que corresponden, o None."""
    for g in sorted(grabaciones, key=lambda g: -int(g.get("score") or 0)):
        if int(g.get("score") or 0) < PUNTAJE_MINIMO:
            continue
        if not _coinciden(g.get("title"), titulo) or not _coinciden(_artista_credito(g), artista):
            continue
        if duracion and g.get("length") and abs(g["length"] / 1000 - duracion) > TOLERANCIA_SEGUNDOS:
            continue
        lanzamientos = sorted(g.get("releases") or [], key=_puntos_lanzamiento)
        if not lanzamientos:
            continue
        lanz = lanzamientos[0]
        grupo = lanz.get("release-group") or {}
        anio = (g.get("first-release-date") or lanz.get("date") or "")[:4]
        return {"album": lanz.get("title") or "", "anio": anio if anio.isdigit() else "",
                "release_id": lanz.get("id") or "", "release_group_id": grupo.get("id") or "",
                "grabacion_id": g.get("id") or ""}
    return None


def _escapar(texto: str) -> str:
    """Escapa los caracteres especiales de la búsqueda de MusicBrainz (sintaxis Lucene)."""
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", texto)


def buscar(titulo: str, artista: str, duracion=None, proxy: str = ""):
    """Busca la canción en MusicBrainz. Devuelve {'album', 'anio', 'release_id',
    'release_group_id', 'grabacion_id'} o None si no hay una coincidencia confiable.
    Lanza la excepción si falla la conexión."""
    titulo_limpio = _RUIDO.sub(" ", titulo or "").strip()
    if not titulo_limpio or not (artista or "").strip():
        return None
    clave = (normalizar(titulo_limpio), normalizar(artista), int(duracion or 0) // 5)
    if clave in _cache:
        return _cache[clave]
    consulta = f'recording:"{_escapar(titulo_limpio)}" AND artist:"{_escapar(artista.strip())}"'
    datos = _pedir_mb("recording", {"query": consulta, "limit": 10}, proxy=proxy) or {}
    resultado = elegir(datos.get("recordings") or [], titulo_limpio, artista, duracion)
    _cache[clave] = resultado
    return resultado


# ==============================================================================
# PORTADAS (Cover Art Archive)
# ==============================================================================
def urls_portada(coincidencia: dict, tamano: int = 1200) -> list:
    """Direcciones de la portada, de la más específica a la más general: la del lanzamiento
    elegido y la del "grupo" (que Cover Art Archive resuelve a la mejor edición con portada).
    Si no existen, Cover Art Archive responde 404 y se usa la siguiente opción."""
    urls = []
    if coincidencia and coincidencia.get("release_group_id"):
        urls.append(f"{URL_CAA}/release-group/{coincidencia['release_group_id']}/front-{tamano}")
    if coincidencia and coincidencia.get("release_id"):
        urls.append(f"{URL_CAA}/release/{coincidencia['release_id']}/front-{tamano}")
    return urls


def datos_pobres(info: dict) -> bool:
    """¿Vale la pena consultar MusicBrainz? Sí, si YouTube no trajo álbum o año de lanzamiento
    (en un videoclip, el "año" suele ser solo la fecha en que se subió el video)."""
    return not info.get("album") or not (info.get("release_year") or info.get("release_date"))
