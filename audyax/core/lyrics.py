"""
Song lyrics: lookup on LRCLIB (https://lrclib.net), saved as a .lrc file next to
the song and/or inside the audio file itself, and reading for the player.

LRCLIB is free, needs no key and returns two versions of the lyrics:
  - synced ("syncedLyrics", LRC format: "[01:23.45] line"), which players
    show in time with the song;
  - plain text ("plainLyrics").

This module doesn't depend on the UI: the download engine uses it and so will the player.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from audyax.core.services import CircuitBreaker, ServiceUnavailable

URL_API = "https://lrclib.net/api"
USER_AGENT = "AUDYAX (https://github.com/luxkun65/AUDYAX)"
TOLERANCE_SECONDS = 3   # accepted duration difference when choosing among results

# What to do with lyrics (Settings → Letras y álbum). "off" = don't look them up.
LYRICS_MODES = ("off", "lrc", "embedded", "both")

try:
    from mutagen.id3 import ID3, USLT, ID3NoHeaderError
    from mutagen.mp4 import MP4
    from mutagen.oggopus import OggOpus
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False


# ==============================================================================
# LRCLIB LOOKUP
# ==============================================================================
breaker = CircuitBreaker()  # if LRCLIB fails several times in a row, it is left alone for a while


def _request(path: str, query_params: dict, timeout: int = 10, proxy: str = ""):
    url = f"{URL_API}/{path}?{urllib.parse.urlencode({k: v for k, v in query_params.items() if v})}"
    request_info = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(
        *([urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy.startswith("http") else []))
    if not breaker.available():
        raise ServiceUnavailable("LRCLIB no respondió hace poco; se reintentará más tarde")
    try:
        with opener.open(request_info, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        breaker.success()
        return data
    except urllib.error.HTTPError as e:
        breaker.success()  # the service answered (even if it was "not found")
        if e.code == 404:
            return None
        raise
    except (urllib.error.URLError, TimeoutError, OSError):
        breaker.failure()
        raise


def _to_result(item: dict) -> dict:
    return {"synced": (item.get("syncedLyrics") or "").strip() or None,
            "text": (item.get("plainLyrics") or "").strip() or None,
            "instrumental": bool(item.get("instrumental")),
            "id": item.get("id")}


def find_lyrics(title: str, artist: str, album: str = "", duration=None, proxy: str = ""):
    """Looks up a song's lyrics. Returns a dict with 'synced', 'text' and
    'instrumental', or None if there are none. Raises the exception if the connection fails.

    1) /api/get with title, artist, album and duration (exact match, ±2 s).
    2) If nothing, /api/search with title and artist, choosing the result with the closest
       duration (preferring the ones with synced lyrics)."""
    title, artist = (title or "").strip(), (artist or "").strip()
    if not title or not artist:
        return None
    dur = int(round(duration)) if duration else None
    if dur:
        item = _request("get", {"track_name": title, "artist_name": artist, "album_name": album,
                              "duration": dur}, proxy=proxy)
        if item and (item.get("syncedLyrics") or item.get("plainLyrics") or item.get("instrumental")):
            return _to_result(item)
    candidate_list = _request("search", {"track_name": title, "artist_name": artist}, proxy=proxy) or []
    candidate_list = [c for c in candidate_list if c.get("syncedLyrics") or c.get("plainLyrics")]
    if dur:
        candidate_list = [c for c in candidate_list
                      if c.get("duration") is None or abs(c["duration"] - dur) <= TOLERANCE_SECONDS]
    if not candidate_list:
        return None
    candidate_list.sort(key=lambda c: (not c.get("syncedLyrics"),
                                   abs((c.get("duration") or dur or 0) - (dur or 0))))
    return _to_result(candidate_list[0])


def text_to_save(lyric: dict) -> str:
    """The version that is saved: the synced one if it exists (it works either way:
    players that don't understand it still show the lines), otherwise the plain text."""
    if not lyric:
        return ""
    return lyric.get("synced") or lyric.get("text") or ""


# ==============================================================================
# SAVING
# ==============================================================================
def lrc_path(audio_path) -> Path:
    return Path(audio_path).with_suffix(".lrc")


def save_lrc(audio_path, lyric: dict) -> Path:
    """Writes 'Song.lrc' next to 'Song.mp3' (that's where players look for it)."""
    dest = lrc_path(audio_path)
    dest.write_text(text_to_save(lyric) + "\n", encoding="utf-8")
    return dest


def embed_lyrics(audio_path, lyric: dict) -> bool:
    """Saves the lyrics inside the file: USLT (MP3), ©lyr (M4A) or LYRICS (Opus).
    Returns False if the format doesn't support it."""
    text = text_to_save(lyric)
    if not HAS_MUTAGEN or not text:
        return False
    path = Path(audio_path)
    ext = path.suffix.lower()
    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("USLT")
        tags.add(USLT(encoding=3, lang="und", desc="", text=text))
        tags.save(path, v2_version=3)
    elif ext in (".m4a", ".mp4"):
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags["\xa9lyr"] = [text]
        audio.save()
    elif ext == ".opus":
        audio = OggOpus(path)
        audio["LYRICS"] = [text]
        audio.save()
    else:
        return False
    return True


def save_lyrics(audio_path, lyric: dict, mode: str) -> list:
    """Saves according to the settings mode. Returns where they ended up ("lrc", "embedded")."""
    done = []
    if not lyric or not text_to_save(lyric) or mode not in LYRICS_MODES or mode == "off":
        return done
    if mode in ("lrc", "both"):
        save_lrc(audio_path, lyric)
        done.append("lrc")
    if mode in ("embedded", "both") and embed_lyrics(audio_path, lyric):
        done.append("embedded")
    return done


# ==============================================================================
# READING (for the player)
# ==============================================================================
def read_lyrics(audio_path) -> str:
    """Lyrics of a downloaded song: first the .lrc next to it, otherwise the embedded ones."""
    lrc = lrc_path(audio_path)
    if lrc.is_file():
        try:
            return lrc.read_text(encoding="utf-8-sig").strip()
        except OSError:
            pass
    if not HAS_MUTAGEN:
        return ""
    path = Path(audio_path)
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            containers = ID3(path).getall("USLT")
            return str(containers[0].text).strip() if containers else ""
        if ext in (".m4a", ".mp4"):
            value = (MP4(path).tags or {}).get("\xa9lyr")
            return str(value[0]).strip() if value else ""
        if ext == ".opus":
            value = OggOpus(path).get("lyrics") or OggOpus(path).get("unsyncedlyrics")
            return str(value[0]).strip() if value else ""
    except Exception:
        return ""
    return ""


_TIME_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def parse_lrc(text: str) -> list:
    """Turns LRC lyrics into a sorted [(seconds, line), ...]. A line can have several
    timestamps ("[00:10.00][01:20.00] chorus"). Tags like [ar:…] are ignored.
    If the lyrics aren't synced, returns []."""
    lines = []
    for raw in (text or "").splitlines():
        marks = list(_TIME_RE.finditer(raw))
        if not marks:
            continue
        rest = raw[marks[-1].end():].strip()
        for m in marks:
            fraction = m.group(3) or "0"
            seconds = int(m.group(1)) * 60 + int(m.group(2)) + int(fraction) / (10 ** len(fraction))
            lines.append((seconds, rest))
    lines.sort(key=lambda x: x[0])
    return lines


def current_line(lines: list, seconds: float) -> int:
    """Index of the line for the given moment (-1 before the first one)."""
    current = -1
    for i, (t, _) in enumerate(lines):
        if t <= seconds + 0.05:
            current = i
        else:
            break
    return current


_TAG_RE = re.compile(r"^\[[a-zA-Z]+:.*\]$")   # [ar:…], [ti:…], [offset:…]


def lyric_lines(text: str):
    """Lyrics ready to show: (lines, synced).
    Synced: [(seconds, line), ...] as parse_lrc gives them.
    Not synced: [(None, line), ...] with LRC tags removed and at most one empty line in a row.
    No lyrics: ([], False)."""
    synced = parse_lrc(text)
    if synced:
        return synced, True
    lines, blank = [], True
    for raw in (text or "").splitlines():
        raw = raw.strip()
        if _TAG_RE.match(raw):
            continue
        if not raw:
            if not blank:
                lines.append((None, ""))
            blank = True
            continue
        lines.append((None, raw))
        blank = False
    while lines and not lines[-1][1]:
        lines.pop()
    return lines, False
