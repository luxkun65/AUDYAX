"""
Better metadata and covers from MusicBrainz (https://musicbrainz.org) and Cover Art Archive
(https://coverartarchive.org), for when YouTube provides poor data: a music video with no
album, or a year that is really the date the video was uploaded.

MusicBrainz rules that are respected:
  - a User-Agent that identifies the application and how to contact it;
  - at most 1 request per second (even with several downloads at once).

It is conservative on purpose: it only accepts a match if the title and the artist
match and the duration is similar. Better to leave a field empty than to fill it wrong.
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
MIN_SCORE = 85          # MusicBrainz gives each result a "score" from 0 to 100
TOLERANCE_SECONDS = 10     # accepted duration difference between the video and the recording

_lock = threading.Lock()
_last_request = [0.0]
_cache = {}
breaker = CircuitBreaker()  # if MusicBrainz fails several times in a row, it is left alone for a while


# ==============================================================================
# REQUESTS (with the limit of 1 per second)
# ==============================================================================
def _request_mb(path: str, query_params: dict, timeout: int = 15, proxy: str = ""):
    query_params = dict(query_params, fmt="json")
    url = f"{MB_URL}/{path}?{urllib.parse.urlencode(query_params)}"
    request_info = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    opener = urllib.request.build_opener(
        *([urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy.startswith("http") else []))
    if not breaker.available():
        raise ServiceUnavailable("MusicBrainz no respondió hace poco; se reintentará más tarde")
    with _lock:  # one request at a time, at least 1.1 s apart
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
# TEXT COMPARISON
# ==============================================================================
_NOISE = re.compile(
    r"[\(\[][^\)\]]*(official|oficial|video|vídeo|audio|lyric|letra|visualizer|hd|4k|remaster|"
    r"live|en vivo)[^\)\]]*[\)\]]", re.IGNORECASE)


def normalize(text: str) -> str:
    """For comparing: no accents, lowercase, no "(Official Video)", "feat. …" or punctuation."""
    text = _NOISE.sub(" ", text or "")
    text = re.split(r"\s+(?:feat\.?|ft\.?|featuring|con)\s+", text, flags=re.IGNORECASE)[0]
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = text.replace("&", " and ")
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _same_name(a: str, b: str) -> bool:
    """Equal, or every word of the shorter one appears (whole) in the other:
    "One More Time" ~ "One More Time - Radio Edit", but "Queen" !~ "Queens of the Stone Age"."""
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
# CHOOSING THE BEST MATCH
# ==============================================================================
def _release_rank(rel: dict) -> tuple:
    """For choosing the album: official, studio album, no "compilation" or "live",
    and the oldest one (the original edition)."""
    group = rel.get("release-group") or {}
    secondary = group.get("secondary-types") or []
    kind = group.get("primary-type") or ""
    return (rel.get("status") != "Official",
            {"Album": 0, "EP": 1, "Single": 2}.get(kind, 3),
            bool(secondary),
            rel.get("date") or "9999")


def choose(recordings: list, title: str, artist: str, duration=None):
    """Chooses the matching recording and release (album), or None."""
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
    """Escapes the special characters of a MusicBrainz query (Lucene syntax)."""
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", text)


def search(title: str, artist: str, duration=None, proxy: str = ""):
    """Looks the song up on MusicBrainz. Returns {'album', 'year', 'release_id',
    'release_group_id', 'recording_id'} or None if there is no reliable match.
    Raises the exception if the connection fails."""
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
# COVERS (Cover Art Archive)
# ==============================================================================
def cover_urls(match: dict, file_size: int = 1200) -> list:
    """Cover URLs, from the most specific to the most general: the chosen release's
    and the "group's" (which Cover Art Archive resolves to the best edition with a cover).
    If they don't exist, Cover Art Archive answers 404 and the next option is used."""
    urls = []
    if match and match.get("release_group_id"):
        urls.append(f"{CAA_URL}/release-group/{match['release_group_id']}/front-{file_size}")
    if match and match.get("release_id"):
        urls.append(f"{CAA_URL}/release/{match['release_id']}/front-{file_size}")
    return urls


def poor_data(info: dict) -> bool:
    """Is it worth asking MusicBrainz? Yes, if YouTube didn't provide an album or release year
    (for a music video, the "year" is usually just the date the video was uploaded)."""
    return not info.get("album") or not (info.get("release_year") or info.get("release_date"))
