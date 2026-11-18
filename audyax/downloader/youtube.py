"""YouTube and YouTube Music: cleaning links, reading YouTube Music playlists,
detecting links in text and searching by name."""

import re
import urllib.parse
import urllib.request

from yt_dlp import YoutubeDL

from audyax.downloader.network import ReadOnlyCookies

# Reads playlists the way YouTube Music shows them (see ytmusic_tracks). Optional.
try:
    from ytmusicapi import YTMusic
    HAS_YTMUSIC = True
except ImportError:
    HAS_YTMUSIC = False


class SilentLogger:
    """Silent logger for the initial URL lookup."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def to_entry(e: dict) -> dict:
    url = str(e.get("webpage_url") or e.get("url") or "")
    if not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={e['id']}"
    thumbnail = e.get("thumbnail") or ""
    if not thumbnail and e.get("thumbnails"):
        thumbnail = (e["thumbnails"][-1] or {}).get("url") or ""
    return {"id": e["id"], "title": e.get("track") or e.get("title") or e["id"],
            "url": url, "thumbnail": thumbnail}


def clean_url(url: str) -> str:
    """Fixes two traps of YouTube Music links:
    - 'watch?v=X&list=RD…' is that song's automatic "radio": yt-dlp would download the whole
      radio (dozens of songs that aren't yours). Only the song is kept.
    - 'music.youtube.com/watch?v=X' becomes www.youtube.com (it's the same video).
    music.youtube.com playlists are left as they are: ytmusic_tracks resolves them."""
    url = (url or "").strip()
    parts = urllib.parse.urlsplit(url)
    if "youtube.com" not in parts.netloc or parts.path != "/watch":
        return url
    qs = urllib.parse.parse_qs(parts.query)
    v = (qs.get("v") or [""])[0]
    list_id = (qs.get("list") or [""])[0]
    if not v:
        return url
    if list_id and not list_id.startswith("RD"):
        if parts.netloc.startswith("music."):
            return url  # song inside a YouTube Music playlist: the playlist is downloaded
        return f"https://www.youtube.com/watch?v={v}&list={list_id}"
    return f"https://www.youtube.com/watch?v={v}"


def is_ytmusic_playlist(url: str) -> bool:
    parts = urllib.parse.urlsplit(url or "")
    if not parts.netloc.startswith("music.youtube.com"):
        return False
    if parts.path.startswith("/browse/MPRE"):
        return True  # album page
    list_id = (urllib.parse.parse_qs(parts.query).get("list") or [""])[0]
    return bool(list_id) and not list_id.startswith("RD")


def _largest_thumbnail(thumbnails) -> str:
    thumbnails = [t for t in (thumbnails or []) if t and t.get("url")]
    if not thumbnails:
        return ""
    return max(thumbnails, key=lambda t: (t.get("width") or 0))["url"]


def ytmusic_tracks(url: str, client=None, proxy: str = ""):
    """Reads a music.youtube.com playlist or album the way YouTube Music does.

    Why: yt-dlp doesn't support YouTube Music and redirects to the same playlist on youtube.com,
    which keeps the ORIGINAL ID of each song. If that version was removed or is blocked,
    yt-dlp fails with "Video unavailable", while YouTube Music silently plays another
    available version (another ID). Here the ID that YouTube Music actually plays is used.

    Returns (title, tracks) or None if it can't (no ytmusicapi, offline, etc.).
    Each track: id, title, artist, album, url, thumbnail, available."""
    if client is None:
        if not HAS_YTMUSIC:
            return None
        client = YTMusic(proxies={"http": proxy, "https": proxy} if proxy else None)
    parts = urllib.parse.urlsplit(url)
    fixed_album = ""
    if parts.path.startswith("/browse/MPRE"):
        data = client.get_album(parts.path.rsplit("/", 1)[-1])
        fixed_album = data.get("title") or ""
    else:
        list_id = (urllib.parse.parse_qs(parts.query).get("list") or [""])[0]
        data = client.get_playlist(list_id, limit=None)
    title = data.get("title") or ""
    tracks = []
    for t in data.get("tracks") or []:
        vid = t.get("videoId")
        if not vid:
            continue
        album = t.get("album")
        album = (album.get("name") if isinstance(album, dict) else album) or fixed_album
        tracks.append({
            "id": vid,
            "title": t.get("title") or vid,
            "artist": ", ".join(a.get("name", "") for a in t.get("artists") or [] if a.get("name")),
            "album": album or "",
            "url": f"https://music.youtube.com/watch?v={vid}",
            "thumbnail": _largest_thumbnail(t.get("thumbnails")) or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            "available": t.get("isAvailable", True) is not False,
        })
    return title, tracks


# ==============================================================================
# LINK DETECTION (clipboard and drag and drop)
# ==============================================================================
_RE_YOUTUBE = re.compile(
    r"^https?://(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?|playlist\?|shorts/|live/|browse/)|youtu\.be/[\w-])",
    re.IGNORECASE)


def is_youtube_link(text: str) -> bool:
    """True if the text is (only) a YouTube or YouTube Music link."""
    t = (text or "").strip()
    return bool(t) and " " not in t and "\n" not in t and bool(_RE_YOUTUBE.match(t))


def extract_links(text: str) -> list:
    """YouTube links inside any text (e.g. what arrives when something is dropped on
    the window, which may come between braces or as a list of URLs). No duplicates."""
    seen, links = set(), []
    for raw_data in re.findall(r"https?://[^\s{}<>\"']+", text or ""):
        url = raw_data.rstrip(".,;)")
        if is_youtube_link(url) and url not in seen:
            seen.add(url)
            links.append(url)
    return links


# ==============================================================================
# SEARCH BY NAME ("artist song")
# ==============================================================================
def _duration_text(seg) -> str:
    try:
        seg = int(seg)
    except (TypeError, ValueError):
        return ""
    h, rest = divmod(seg, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"


def _to_result(e: dict) -> dict:
    data = to_entry(e)
    data["canal"] = re.sub(r"\s*-\s*Topic$", "", e.get("channel") or e.get("uploader") or "")
    data["duration"] = _duration_text(e.get("duration"))
    if not data["thumbnail"]:
        data["thumbnail"] = f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg"
    return data


def search(text: str, max_results: int = 10, net=None) -> list:
    """Searches YouTube and returns a list of results
    (id, title, url, thumbnail, channel, duration). Raises the exception if it fails."""
    text = (text or "").strip()
    if not text:
        return []
    options = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                "logger": SilentLogger(), **(net or {})}
    with ReadOnlyCookies(options) as options, YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"ytsearch{int(max_results)}:{text}", download=False)
    return [_to_result(e) for e in (info or {}).get("entries") or []
            if e and e.get("id") and e.get("ie_key") in (None, "Youtube")]
