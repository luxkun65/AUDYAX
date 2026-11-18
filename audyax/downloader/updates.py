"""Checking for and installing updates (yt-dlp and AUDYAX itself)."""

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request

from audyax import REPO
from audyax.downloader.youtube import HAS_YTMUSIC


# ==============================================================================
# UPDATE yt-dlp
# ==============================================================================
def ytdlp_version() -> str:
    from yt_dlp.version import __version__
    return __version__


def _as_tuple(version: str) -> tuple:
    return tuple(int(p) for p in re.findall(r"\d+", version or ""))


def is_newer(candidate: str, current: str) -> bool:
    return _as_tuple(candidate) > _as_tuple(current)


def latest_ytdlp_version(timeout: int = 8) -> str:
    """Latest version published on PyPI. Raises the exception if offline."""
    req = urllib.request.Request("https://pypi.org/pypi/yt-dlp/json",
                                 headers={"User-Agent": "AUDYAX"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))["info"]["version"]


def latest_app_version(timeout: int = 8) -> str:
    """Latest AUDYAX version published on GitHub Releases (e.g. "1.0.1").
    Raises the exception if offline or if no versions have been published yet."""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"User-Agent": "AUDYAX", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))["tag_name"].lstrip("vV")


def can_self_update() -> bool:
    """In the PyInstaller executable yt-dlp is bundled and pip can't change it."""
    return not getattr(sys, "frozen", False)


def update_ytdlp() -> tuple:
    """Updates yt-dlp (and ytmusicapi, which also breaks when YouTube Music changes)
    with pip. Returns (ok, message). The app must be restarted
    for the new version to be used (the old one is already loaded in memory)."""
    if not can_self_update():
        return False, ("Esta es la versión empaquetada (.exe): yt-dlp va dentro del programa. "
                       "Descarga una versión nueva de AUDYAX para actualizarlo.")
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
    try:
        packages = ["yt-dlp"] + (["ytmusicapi"] if HAS_YTMUSIC else [])
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", *packages],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600, **extra)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if r.returncode != 0:
        lines = (r.stderr or r.stdout or "").strip().splitlines()
        return False, "\n".join(lines[-6:]) or f"pip terminó con código {r.returncode}"
    return True, "yt-dlp se actualizó correctamente."
