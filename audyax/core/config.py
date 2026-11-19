"""Saved settings (~/.audyax.json) and their defaults."""

import json
import re
from pathlib import Path

CONFIG_PATH = Path.home() / ".audyax.json"
OLD_CONFIG_PATH = Path.home() / ".descargador_musica.json"   # versions before the code was in English


DEFAULT_DIR = str(Path.home() / "Music" / "Descargas")


DEFAULT_CONFIG = {
    "folder": DEFAULT_DIR,
    "audio_format": "mp3",
    "quality": "192",
    "normalize": False,
    "crop": True,
    "replaygain": True,
    "theme": "dark",
    "check_ytdlp": True,           # on startup, tell the user if there is a new yt-dlp version
    "detect_clipboard": True,   # offer to paste a copied YouTube link
    "organize": "flat",            # "flat" | "artist" | "artist_album" (see ORGANIZE_MODES)
    "playlists_in_folder": True,    # playlists stay together in their own folder even when organizing
    "parallel_downloads": 2,      # songs downloaded at the same time (1 = one after another)
    "notifications": True,          # notify when downloads finish
    "keep_in_background": True,           # when closed with the X, keep running in the system tray
    # Advanced options (all off at first)
    "speed_limit_enabled": False,
    "speed_limit_mb": 2.0,      # MB/s in total (split among parallel downloads)
    "proxy_enabled": False,
    "proxy": "",                     # e.g. http://server:8080 or socks5://127.0.0.1:1080
    "cookies_enabled": False,
    "cookies_source": "firefox",     # firefox, chrome, edge… or "file"
    "cookies_file": "",           # path to a cookies.txt (Netscape format)
    # Lyrics and metadata
    "lyrics": "off",                  # "off" | "lrc" | "embedded" | "both" (see lyrics.LYRICS_MODES)
    "musicbrainz": True,             # fill album and year from MusicBrainz when YouTube doesn't provide them
    "caa_cover": True,             # use the album cover from Cover Art Archive when found
    # Mini player
    "volume": 0.8,
    "library_folder": "",            # player library; empty = the download folder
    "shuffle": False,                # player: shuffle the queue
    "repeat": "off",                 # player: "off" | "all" (the queue) | "one" (the song)
    # Watched playlists
    "watched": [],                 # [{url, name, folder, last_check, new_count}] (see watched.py)
    "check_watched": True,       # check them when AUDYAX opens
    "auto_download_new": False,      # download new songs without asking
}


COOKIE_BROWSERS = ["firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium"]


VALID_PROXY = re.compile(r"^(https?|socks4a?|socks5h?)://[^\s/]+(:\d+)?/?$", re.IGNORECASE)


MAX_PARALLEL = 3  # more than this makes YouTube start throttling or blocking


def parallel_downloads(cfg: dict) -> int:
    try:
        n = int(cfg.get("parallel_downloads", 1))
    except (TypeError, ValueError):
        n = 1
    return max(1, min(MAX_PARALLEL, n))


# ==============================================================================
# SAVED SETTINGS
# ==============================================================================
# Old (Spanish) settings keys and values -> current ones. Only used once, to migrate
# ~/.descargador_musica.json to ~/.audyax.json.
_OLD_KEYS = {
    "carpeta": "folder", "formato": "audio_format", "calidad": "quality", "normalizar": "normalize",
    "recorte": "crop", "tema": "theme", "revisar_ytdlp": "check_ytdlp", "detectar_portapapeles": "detect_clipboard",
    "organizar": "organize", "playlists_en_carpeta": "playlists_in_folder", "descargas_simultaneas": "parallel_downloads",
    "notificaciones": "notifications", "segundo_plano": "keep_in_background",
    "limite_velocidad_activo": "speed_limit_enabled", "limite_velocidad_mb": "speed_limit_mb",
    "proxy_activo": "proxy_enabled", "cookies_activo": "cookies_enabled", "cookies_origen": "cookies_source",
    "cookies_archivo": "cookies_file", "letras": "lyrics", "portada_caa": "caa_cover", "volumen": "volume",
    "vigiladas": "watched", "revisar_vigiladas": "check_watched", "bajar_nuevas_auto": "auto_download_new",
}
_OLD_VALUES = {
    "organize": {"plano": "flat", "artista": "artist", "artista_album": "artist_album"},
    "lyrics": {"no": "off", "incrustada": "embedded", "ambas": "both"},
    "cookies_source": {"archivo": "file"},
}
_OLD_WATCHED_KEYS = {"nombre": "name", "carpeta": "folder", "agregada": "added", "ultima_revision": "last_check",
                     "nuevas": "new_count"}


def migrate_old_config(old: dict) -> dict:
    """Translates settings saved by older versions (Spanish keys) to the current keys."""
    cfg = {}
    for key, value in old.items():
        key = _OLD_KEYS.get(key, key)
        value = _OLD_VALUES.get(key, {}).get(value, value) if isinstance(value, str) else value
        if key == "watched" and isinstance(value, list):
            value = [{_OLD_WATCHED_KEYS.get(k, k): v for k, v in w.items()} for w in value if isinstance(w, dict)]
        cfg[key] = value
    return cfg


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except FileNotFoundError:
        try:  # first start after the update: bring the old settings over
            cfg.update(migrate_old_config(json.loads(OLD_CONFIG_PATH.read_text(encoding="utf-8"))))
            save_config(cfg)
        except (OSError, ValueError, AttributeError):
            pass
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict):
    # Only known preferences are saved (not the data of a particular job)
    data = {k: cfg[k] for k in DEFAULT_CONFIG if k in cfg}
    try:
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
