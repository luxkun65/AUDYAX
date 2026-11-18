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

from services import CircuitBreaker, ServiceUnavailable

MB_URL = "https://musicbrainz.org/ws/2"
CAA_URL = "https://coverartarchive.org"
USER_AGENT = "AUDYAX/1.0 ( https://github.com/luxkun65/AUDYAX )"
MIN_SCORE = 85          # MusicBrainz da un "score" de 0 a 100 a cada resultado
TOLERANCE_SECONDS = 10     # diferencia de duración aceptada entre el video y la grabación

_lock = threading.Lock()
_last_request = [0.0]
_cache = {}
breaker = CircuitBreaker()  # si MusicBrainz falla varias veces seguidas, se deja un rato


# ==============================================================================
# PETICIONES (con el límite de 1 por segundo)
# ==============================================================================
def _request_mb(path: str, query_params: dict, timeout: int = 15, proxy: str = ""):
    query_params = dict(query_params, fmt="json")
    url = f"{MB_URL}/{path}?{urllib.parse.urlencode(query_params)}"
    request_info = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    opener = urllib.request.build_opener(
        *([urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy.startswith("http") else []))
    if not breaker.available():
        raise ServiceUnavailable("MusicBrainz no respondió hace poco; se reintentará más tarde")
    with _lock:  # una petición a la vez, separadas al menos 1,1 s
        wait_time = _last_request[0] + 1.1 - time.monotonic()
        if wait_time > 0:
            time.sleep(wait_time)
        try:
            with opener.open(request_info, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            breaker.success()
            return data
        except (urllib.error.URLError, TimeoutError, OSError):
            breaker.failure()
            raise
        finally:
            _last_request[0] = time.monotonic()


# ==============================================================================
# COMPARAR TEXTOS
# ==============================================================================
_NOISE = re.compile(
    r"[\(\[][^\)\]]*(official|oficial|video|vídeo|audio|lyric|letra|visualizer|hd|4k|remaster|"
    r"live|en vivo)[^\)\]]*[\)\]]", re.IGNORECASE)


def normalize(text: str) -> str:
    """Para comparar: sin tildes, en minúsculas, sin "(Official Video)", "feat. …" ni signos."""
    text = _NOISE.sub(" ", text or "")
    text = re.split(r"\s+(?:feat\.?|ft\.?|featuring|con)\s+", text, flags=re.IGNORECASE)[0]
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = text.replace("&", " and ")
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _same_name(a: str, b: str) -> bool:
    """Iguales, o todas las palabras del más corto están (completas) en el otro:
    "One More Time" ~ "One More Time - Radio Edit", pero "Queen" no ~ "Queens of the Stone Age"."""
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return False
    if a == b:
        return True
    shorten, longer = sorted((a, b), key=len)
    return len(shorten) >= 4 and set(shorten.split()) <= set(longer.split())


def _credited_artist(recording: dict) -> str:
    return "".join((c.get("name") or "") + (c.get("joinphrase") or "")
                   for c in recording.get("artist-credit") or [])


# ==============================================================================
# ELEGIR LA MEJOR COINCIDENCIA
# ==============================================================================
def _release_rank(rel: dict) -> tuple:
    """Para elegir el álbum: oficial, álbum de estudio, sin "recopilatorio" ni "en vivo",
    y el más antiguo (la edición original)."""
    group = rel.get("release-group") or {}
    secondary = group.get("secondary-types") or []
    kind = group.get("primary-type") or ""
    return (rel.get("status") != "Official",
            {"Album": 0, "EP": 1, "Single": 2}.get(kind, 3),
            bool(secondary),
            rel.get("date") or "9999")


def choose(recordings: list, title: str, artist: str, duration=None):
    """Elige la grabación y el lanzamiento (álbum) que corresponden, o None."""
    for g in sorted(recordings, key=lambda g: -int(g.get("score") or 0)):
        if int(g.get("score") or 0) < MIN_SCORE:
            continue
        if not _same_name(g.get("title"), title) or not _same_name(_credited_artist(g), artist):
            continue
        if duration and g.get("length") and abs(g["length"] / 1000 - duration) > TOLERANCE_SECONDS:
            continue
        releases = sorted(g.get("releases") or [], key=_release_rank)
        if not releases:
            continue
        rel = releases[0]
        group = rel.get("release-group") or {}
        year = (g.get("first-release-date") or rel.get("date") or "")[:4]
        return {"album": rel.get("title") or "", "year": year if year.isdigit() else "",
                "release_id": rel.get("id") or "", "release_group_id": group.get("id") or "",
                "recording_id": g.get("id") or ""}
    return None


def _escape(text: str) -> str:
    """Escapa los caracteres especiales de la búsqueda de MusicBrainz (sintaxis Lucene)."""
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", text)


def search(title: str, artist: str, duration=None, proxy: str = ""):
    """Busca la canción en MusicBrainz. Devuelve {'album', 'anio', 'release_id',
    'release_group_id', 'grabacion_id'} o None si no hay una coincidencia confiable.
    Lanza la excepción si falla la conexión."""
    clean_title = _NOISE.sub(" ", title or "").strip()
    if not clean_title or not (artist or "").strip():
        return None
    key = (normalize(clean_title), normalize(artist), int(duration or 0) // 5)
    if key in _cache:
        return _cache[key]
    query = f'recording:"{_escape(clean_title)}" AND artist:"{_escape(artist.strip())}"'
    data = _request_mb("recording", {"query": query, "limit": 10}, proxy=proxy) or {}
    result = choose(data.get("recordings") or [], clean_title, artist, duration)
    _cache[key] = result
    return result


# ==============================================================================
# PORTADAS (Cover Art Archive)
# ==============================================================================
def cover_urls(match: dict, file_size: int = 1200) -> list:
    """Direcciones de la portada, de la más específica a la más general: la del lanzamiento
    elegido y la del "grupo" (que Cover Art Archive resuelve a la mejor edición con portada).
    Si no existen, Cover Art Archive responde 404 y se usa la siguiente opción."""
    urls = []
    if match and match.get("release_group_id"):
        urls.append(f"{CAA_URL}/release-group/{match['release_group_id']}/front-{file_size}")
    if match and match.get("release_id"):
        urls.append(f"{CAA_URL}/release/{match['release_id']}/front-{file_size}")
    return urls


def poor_data(info: dict) -> bool:
    """¿Vale la pena consultar MusicBrainz? Sí, si YouTube no trajo álbum o año de lanzamiento
    (en un videoclip, el "año" suele ser solo la fecha en que se subió el video)."""
    return not info.get("album") or not (info.get("release_year") or info.get("release_date"))
