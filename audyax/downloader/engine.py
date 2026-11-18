"""
Download ENGINE for YouTube / YouTube Music (no graphical interface).

Everything that downloads, converts, tags and measures loudness lives here.
It doesn't import tkinter: it can be used from any interface (or from the terminal)
by calling run_job(url, cfg, emit, cancel).

Requirements:
    pip install -U yt-dlp mutagen
    ffmpeg installed and in the PATH
"""

import logging
import logging.handlers
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor import EmbedThumbnailPP, FFmpegExtractAudioPP, FFmpegMetadataPP, FFmpegThumbnailsConvertorPP
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import sanitize_filename

from audyax.core import lyrics, metadata
from audyax.core.config import parallel_downloads
from audyax.core.replaygain import album_gain, analyze_loudness, rg_text, write_replaygain
from audyax.core.tags import is_editable, strip_track_number, write_tags
from audyax.downloader.network import AGE_RESTRICTION_RE, ReadOnlyCookies, cookie_options, network_options
from audyax.downloader.youtube import SilentLogger, clean_url, is_ytmusic_playlist, to_entry, ytmusic_tracks

try:
    from yt_dlp.utils import DownloadCancelled
except ImportError:  # very old yt-dlp: update with pip install -U yt-dlp
    class DownloadCancelled(Exception):
        pass


# Writes shared between threads (faltantes.txt, log)
_FILES_LOCK = threading.Lock()


# How songs are split into folders. Each level is a separate field of the yt-dlp
# template: that way a "/" inside a name (e.g. "AC/DC") doesn't create extra folders.
ORGANIZE_MODES = {
    "flat": [],
    "artist": ["org_artist"],
    "artist_album": ["org_artist", "org_album"],
}


def wait_if_paused(pause, cancel, video_id=None, skip=None):
    """While paused, waits here (without using CPU) until resumed.
    Cancelling also wakes up the wait. yt-dlp calls the progress hooks very often,
    so blocking them stops the running download; when resumed, it continues where it was.

    skip: set of ids cancelled one by one from the UI. If video_id is in it,
    only that song is stopped (each song uses its own YoutubeDL)."""
    def cancelled():
        return ((cancel is not None and cancel.is_set())
                or (skip is not None and video_id is not None and video_id in skip))
    while pause is not None and pause.is_set():
        if cancelled():
            break
        time.sleep(0.2)
    if cancelled():
        raise DownloadCancelled("Cancelado por el usuario")


class YtdlpLogger:
    """Logger for yt-dlp: writes everything to the log and reports the real reason of each failure."""

    def __init__(self, log, on_failure):
        self.log = log
        self.on_failure = on_failure

    def debug(self, msg):
        (self.log.debug if msg.startswith("[debug]") else self.log.info)(msg)

    def info(self, msg):
        self.log.info(msg)

    def warning(self, msg):
        self.log.warning(msg)

    def error(self, msg):
        self.log.error(msg)
        # Typical format: "ERROR: [youtube] abc123def45: Video unavailable"
        m = re.search(r"\[[^\]]+\]\s+([\w-]{11}):\s*(.*)", msg)
        if m:
            video_id, reason = m.group(1), m.group(2)
        else:
            video_id, reason = "?", re.sub(r"^ERROR:\s*", "", msg)
        self.on_failure(video_id, reason.strip())




def tags(info: dict) -> dict:
    """A song's data (tags), as shown in the list."""
    return {
        "title": info.get("title") or "",
        "artist": info.get("artist") or "",
        "album": info.get("album") or "",
        "year": str(info.get("release_year") or (info.get("upload_date") or "")[:4] or ""),
        "genre": info.get("genre") or (info.get("genres") or [""])[0] or "",
        "url": info.get("webpage_url") or info.get("original_url") or "",
    }


def unique_name(dest: Path, title: str, artist: str, taken=None) -> str:
    """File name (without extension) that doesn't clash with another one in the folder.
    First just the title; if it exists, 'Title (Artist)'; if that exists too, 'Title (Artist) 2', 3…
    Compared case-insensitively because Windows doesn't distinguish case either.
    'taken': names reserved by running downloads that haven't created their file yet."""
    base = sanitize_filename(title)
    try:
        used = {f.stem.casefold() for f in dest.iterdir()} if dest.exists() else set()
    except OSError:
        used = set()
    used |= {n.casefold() for n in taken or ()}
    if base.casefold() not in used:
        return base
    with_artist = f"{base} ({sanitize_filename(artist)})"
    name, n = with_artist, 2
    while name.casefold() in used:
        name = f"{with_artist} {n}"
        n += 1
    return name


class NameReservations:
    """With several downloads at once, two songs with the same title could choose the
    same name before the file exists. This reserves each name when it is chosen."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sections = {}  # folder -> {names}

    def reserve(self, folder: Path, title: str, artist: str) -> str:
        with self._lock:
            sections = self._sections.setdefault(str(folder), set())
            name = unique_name(folder, title, artist, sections)
            sections.add(name)
            return name


class CleanMetadata(PostProcessor):
    """Chooses better fields than 'uploader', decides the final file name
    (just the title; if it already exists, adds '(Artist)') and notifies the UI."""

    def __init__(self, downloader, dest: Path, emit, cancel, extras=None, default_album="",
                 organize="flat", reservations=None, pause=None, skip=None, cfg=None):
        super().__init__(downloader)
        self.cfg = cfg or {}
        self.reservations = reservations or NameReservations()
        self.pause = pause
        self.skip = skip
        self.dest = dest
        self.organize = organize
        self.emit = emit
        self.cancel = cancel
        # id -> {"title", "artist", "album"} as YouTube Music shows them. They take priority over
        # what yt-dlp guesses (for a music video it would use "Song (Official Video)" or "ArtistVEVO").
        self.extras = extras or {}
        self.default_album = default_album

    def _complete_with_musicbrainz(self, info: dict):
        """Asks MusicBrainz for the real album and year and, if enabled, puts the album
        cover as the first thumbnail option."""
        self.emit(("status", info["id"], "Buscando álbum en MusicBrainz…"))
        try:
            found = metadata.search(info.get("title"), info.get("artist"), info.get("duration"),
                                       proxy=self.cfg.get("proxy", "") if self.cfg.get("proxy_enabled") else "")
        except Exception as e:  # offline, MusicBrainz down…: keep going with YouTube's data
            self.report_warning(f"MusicBrainz: {e}")
            return
        if not found:
            return
        if not info.get("album") and found["album"]:
            info["album"] = found["album"]
        if found["year"] and not (info.get("release_year") or info.get("release_date")):
            info["release_year"] = int(found["year"])
        if self.cfg.get("caa_cover"):
            # yt-dlp tries thumbnails starting from the LAST one and, if one doesn't exist (404),
            # moves to the previous one: the album cover goes last and YouTube's stays as a fallback
            for i, url in enumerate(reversed(metadata.cover_urls(found))):
                info.setdefault("thumbnails", []).append({"url": url, "id": f"caa{i}", "ext": "jpg"})
        info["_musicbrainz"] = found

    def run(self, info):
        wait_if_paused(self.pause, self.cancel, info.get("id"), self.skip)

        extra = self.extras.get(info.get("id")) or {}
        if extra.get("title"):
            info["track"] = extra["title"]
        if extra.get("artist"):
            info["artist"] = extra["artist"]
        if extra.get("album"):
            info["album"] = extra["album"]

        artist = (
            info.get("artist")
            or ", ".join(info.get("artists") or [])
            or info.get("creator")
            or info.get("uploader")
            or "Unknown Artist"
        )
        info["artist"] = re.sub(r"\s*-\s*Topic$", "", artist).strip()
        if info.get("track"):
            info["title"] = info["track"]
        # Don't write a track number: yt-dlp skips tags whose value is None
        info["track_number"] = None
        if self.cfg.get("musicbrainz") and metadata.poor_data(info):
            self._complete_with_musicbrainz(info)
        if not info.get("album"):
            info["album"] = info.get("playlist_title") or self.default_album or info["title"]

        # Organization folders (if enabled) and a name that doesn't clash inside them
        info["org_artist"] = info["artist"] or "Artista desconocido"
        info["org_album"] = info["album"] or "Sin álbum"
        folder = self.dest
        for field in ORGANIZE_MODES.get(self.organize, []):
            folder = folder / sanitize_filename(info[field])
        info["safe_filename"] = self.reservations.reserve(folder, info["title"], info["artist"])

        self.emit(("info", info["id"], tags(info)))
        return [], info


class ExtractAudio(FFmpegExtractAudioPP):
    """FFmpegExtractAudio that can force re-encoding.

    yt-dlp copies the audio untouched when the codec already matches (e.g. AAC -> m4a,
    or Opus -> opus). That's ideal, except when normalizing: copying and applying
    a filter at the same time isn't possible (with m4a normalization was silently ignored
    and with opus FFmpeg failed). With force=True it always re-encodes."""

    def __init__(self, downloader=None, force=False, **kw):
        super().__init__(downloader, **kw)
        self.force = force

    @classmethod
    def pp_key(cls):
        # Same name as the original: this keeps the "extractaudio+ffmpeg_o" arguments
        # and the progress messages working.
        return "ExtractAudio"

    def get_audio_codec(self, path):
        codec = super().get_audio_codec(path)
        # A dummy codec different from the target one makes yt-dlp convert
        return "forzar-recodificacion" if (self.force and codec) else codec


def should_normalize(cfg: dict) -> bool:
    return bool(cfg.get("normalize")) and cfg.get("audio_format") != "original"


class MarkDone(PostProcessor):
    """Runs at the end (after_move): the file is already tagged and in place."""

    def __init__(self, downloader, emit, replaygain, record, cfg=None):
        super().__init__(downloader)
        self.emit = emit
        self.replaygain = replaygain
        self.record = record  # analyzed songs (for the album ReplayGain)
        self.cfg = cfg or {}

    def run(self, info):
        data = tags(info)
        path = info.get("filepath") or info.get("_filename") or ""
        data["path"] = path
        if path:
            try:
                strip_track_number(Path(path))
            except Exception as e:  # a failure here doesn't invalidate the download
                self.report_warning(f"No se pudo quitar el número de pista: {e}")
            # yt-dlp writes the video's UPLOAD date as the year; if the real release year is known
            # (YouTube Music or MusicBrainz), that one is written, since it's the one shown
            if info.get("release_year") and is_editable(path):
                try:
                    write_tags(path, {"year": str(info["release_year"])})
                except Exception as e:
                    self.report_warning(f"No se pudo escribir el año: {e}")
        if self.replaygain and path:
            self.emit(("status", info["id"], "Calculando ReplayGain…"))
            try:
                vol = analyze_loudness(path, info.get("duration"))
                if vol is None:
                    raise RuntimeError("no se pudo medir el volumen")
                write_replaygain(Path(path), vol)
                self.record.append({"id": info["id"], "path": Path(path), "data": vol})
                data["replaygain"] = rg_text(vol)
            except Exception as e:  # a failure here doesn't invalidate the download
                self.report_warning(f"ReplayGain: {e}")
        mode = self.cfg.get("lyrics", "off")
        if path and mode in lyrics.LYRICS_MODES and mode != "off":
            data["lyrics"] = self._lyrics(info, path, mode)
        if info.get("_musicbrainz"):
            data["musicbrainz"] = True
        self.emit(("done", info["id"], data))
        return [], info

    def _lyrics(self, info, path, mode) -> str:
        """Looks up the lyrics on LRCLIB and saves them. Returns "synced", "text" or ""."""
        self.emit(("status", info["id"], "Buscando letra…"))
        try:
            lyric = lyrics.find_lyrics(info.get("title"), info.get("artist"), info.get("album"),
                                        info.get("duration"),
                                        proxy=self.cfg.get("proxy", "") if self.cfg.get("proxy_enabled") else "")
            if not lyric or not lyrics.save_lyrics(path, lyric, mode):
                return ""
            return "synced" if lyric.get("synced") else "text"
        except Exception as e:  # no lyrics is not a download error
            self.report_warning(f"Letra: {e}")
            return ""


def relative_subfolder(dest: Path, folder: Path) -> str:
    try:
        rel = dest.relative_to(folder)
    except ValueError:
        return ""
    return "" if str(rel) == "." else str(rel)


def analyze_url(url: str, folder: Path, subfolder: str = "", net=None):
    """Returns (dest_folder, song_list, is_playlist, playlist_title).
    For a playlist, uses a subfolder. 'subfolder' forces the destination (used when retrying
    a song that came from a playlist)."""
    dest, entries, is_playlist = (folder / subfolder if subfolder else folder), [], False
    title = ""
    try:
        options = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                    "ignoreerrors": True, "logger": SilentLogger(), **(net or {})}
        with ReadOnlyCookies(options) as options, YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if info and info.get("_type") == "playlist":
            is_playlist = True
            title = info.get("title") or ""
            if info.get("title") and not subfolder:
                dest = folder / sanitize_filename(info["title"])
            for e in info.get("entries") or []:
                if e and e.get("id") and e.get("_type") != "playlist" and e.get("ie_key") != "YoutubeTab":
                    entries.append(to_entry(e))
        elif info and info.get("id"):
            entries.append(to_entry(info))
    except Exception:
        pass
    return dest, entries, is_playlist, title


def ids_in_archive(folder: Path) -> set:
    try:
        lines = (folder / "descargados.txt").read_text(encoding="utf-8").splitlines()
        return {l.split()[-1] for l in lines if l.strip()}
    except OSError:
        return set()


def setup_log(folder: Path) -> logging.Logger:
    """Log in folder/descarga.log. If it already points there it's left alone (other downloads
    may be writing to it at the same time)."""
    log = logging.getLogger("audyax")
    log.setLevel(logging.DEBUG)
    path = os.path.abspath(folder / "descarga.log")
    with _FILES_LOCK:
        if any(getattr(h, "baseFilename", None) == path for h in log.handlers):
            return log
        for h in list(log.handlers):
            h.close()
            log.removeHandler(h)
        # Rotates at ~2 MB so the log doesn't grow without limit inside the music folder
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(handler)
    return log


STAGES = {
    "ThumbnailsConvertor": "Preparando portada…",
    "ExtractAudio": "Convirtiendo audio…",
    "Metadata": "Escribiendo tags…",
    "EmbedThumbnail": "Incrustando portada…",
}


def output_template(dest: Path, organize: str = "flat") -> str:
    """Output path for yt-dlp, e.g. dest/%(org_artist)s/%(org_album)s/%(safe_filename)s.%(ext)s
    No "/" is put inside a field because yt-dlp turns it into "⧹"."""
    levels = [f"%({field})s" for field in ORGANIZE_MODES.get(organize, [])]
    return str(dest.joinpath(*levels, "%(safe_filename)s.%(ext)s"))


def organize_mode(cfg: dict, is_playlist: bool) -> str:
    """Organization applied to this particular job."""
    mode = cfg.get("organize", "flat")
    if mode not in ORGANIZE_MODES:
        return "flat"
    if cfg.get("subfolder"):
        return "flat"  # retrying a song from a playlist: it goes back next to its playlist
    if is_playlist and cfg.get("playlists_in_folder", True):
        return "flat"
    return mode


def build_options(cfg, folder, dest, logger, progress_hook, hook_pp,
                       organize="flat") -> dict:
    """yt-dlp options. Postprocessors are added separately (add_postprocessors)
    because one of them is our own class, which can't be declared by name."""
    template = output_template(dest, organize)

    pp_args = {}
    if cfg["crop"]:
        pp_args["thumbnailsconvertor+ffmpeg_o"] = [
            "-c:v", "mjpeg",
            "-vf", "crop='if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'",
        ]
    if should_normalize(cfg):
        pp_args["extractaudio+ffmpeg_o"] = ["-af", "loudnorm=I=-14:TP=-1:LRA=11"]

    return {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        # empty "pl_thumbnail" = don't save the playlist's standalone image
        "outtmpl": {"default": template, "pl_thumbnail": ""},
        "postprocessor_args": pp_args,
        "writethumbnail": True,
        "noplaylist": False,
        "ignoreerrors": True,
        "download_archive": str(folder / "descargados.txt"),
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragments": 5,
        "geo_bypass": True,
        "noprogress": True,
        "logger": logger,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [hook_pp],
        **network_options(cfg),
    }


def add_postprocessors(ydl, cfg, dest, emit, cancel, record,
                             extras=None, default_album="", organize="flat",
                             reservations=None, pause=None, skip=None):
    """Postprocessing chain, in order: cover -> name/tags -> audio -> metadata -> embedded
    cover -> ReplayGain and the 'done' event."""
    codec = "best" if cfg["audio_format"] == "original" else cfg["audio_format"]
    ydl.add_post_processor(FFmpegThumbnailsConvertorPP(ydl, format="jpg"), when="before_dl")
    ydl.add_post_processor(CleanMetadata(ydl, dest, emit, cancel, extras, default_album,
                                           organize, reservations, pause, skip, cfg), when="pre_process")
    ydl.add_post_processor(ExtractAudio(ydl, force=should_normalize(cfg), preferredcodec=codec,
                                        preferredquality=cfg["quality"]))
    ydl.add_post_processor(FFmpegMetadataPP(ydl, add_metadata=True))
    ydl.add_post_processor(EmbedThumbnailPP(ydl))
    ydl.add_post_processor(MarkDone(ydl, emit, cfg["replaygain"], record, cfg), when="after_move")


def resolve(url: str, folder: Path, subfolder: str = "", log=None, net=None) -> dict:
    """Decides what will be downloaded. Returns dest, entries, is_playlist, the URLs passed
    to yt-dlp and, if it came from YouTube Music, its titles/artists/albums by id."""
    url = clean_url(url)
    if is_ytmusic_playlist(url):
        try:
            res = ytmusic_tracks(url, proxy=(net or {}).get("proxy", ""))
        except Exception as e:  # offline, private playlist, API change…
            res = None
            if log:
                log.warning(f"No se pudo leer la playlist con YouTube Music ({e}); se usa yt-dlp")
        if res and res[1]:
            title, tracks = res
            if subfolder:
                dest = folder / subfolder
            else:
                dest = folder / sanitize_filename(title) if title else folder
            return {
                "dest": dest, "entries": tracks, "is_playlist": True,
                "urls": [f"https://www.youtube.com/watch?v={p['id']}" for p in tracks],
                "extras": {p["id"]: {k: p[k] for k in ("title", "artist", "album")} for p in tracks},
                "default_album": title, "per_song": True,
            }
    dest, entries, is_playlist, title = analyze_url(url, folder, subfolder, net)
    if is_playlist and entries:
        # One URL per song: this way several can download at once
        return {"dest": dest, "entries": entries, "is_playlist": True,
                "urls": [e["url"] for e in entries], "extras": {},
                "default_album": title, "per_song": True}
    return {"dest": dest, "entries": entries, "is_playlist": is_playlist,
            "urls": [url], "extras": {}, "default_album": title, "per_song": False}


def run_job(url: str, cfg: dict, emit, cancel, pause=None, limit=None, skip=None):
    """Downloads a URL (video or playlist) and keeps sending events to the UI.

    pause: threading.Event; while it is set, downloads stop.
    limit: semaphore shared by the whole app with the maximum number of songs at once
           (if not given, one is created with cfg["parallel_downloads"]).
    skip: set of ids the UI cancels one by one; it can keep growing while the job runs
          (songs that haven't started are skipped, the one in progress is stopped)."""
    if skip is None:
        skip = set()
    n_parallel = parallel_downloads(cfg)
    if limit is None:
        limit = threading.BoundedSemaphore(n_parallel)
    folder = Path(cfg["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    log = setup_log(folder)
    known = {}  # id -> entry (so that faltantes.txt says WHICH song failed)

    cookies = cookie_options(cfg)
    age_restricted = []          # age-restricted songs that will be retried with cookies
    retried = set()   # (the ones already retried: if they fail again, it's a normal error)

    def on_failure(video_id, reason):
        if AGE_RESTRICTION_RE.search(reason) and video_id != "?":
            if cookies and video_id not in retried:
                # Not an error yet: it will be retried with the user's session
                if video_id not in age_restricted:
                    age_restricted.append(video_id)
                emit(("status", video_id, "Restricción de edad: usando tu sesión…"))
                return
            if not cookies:
                reason += (" (Video con restricción de edad: actívalo en Configuración → "
                           "Avanzado → Cookies)")
        e = known.get(video_id) or {}
        if e.get("available") is False:
            reason += (" (YouTube Music la muestra en gris: no está disponible en tu país "
                       "o fue retirada)")
        emit(("error", video_id, reason))
        try:
            link = e.get("url") or (f"https://www.youtube.com/watch?v={video_id}"
                                      if video_id != "?" else "(desconocido)")
            song = " - ".join(x for x in (e.get("artist"), e.get("title")) if x)
            with _FILES_LOCK, open(folder / "faltantes.txt", "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M}\n"
                        + (f"Canción: {song}\n" if song else "")
                        + f"URL: {link}\nMotivo: {reason}\n---\n")
        except OSError:
            pass

    logger = YtdlpLogger(log, on_failure)

    # 1) Show all songs right away (yellow, "queued")
    net = network_options(cfg)
    plan = resolve(url, folder, cfg.get("subfolder") or "", log, net)
    if cfg.get("extras"):  # e.g. when retrying a song that came from YouTube Music
        plan["extras"] = {**cfg["extras"], **plan["extras"]}
    dest, entries, is_playlist = plan["dest"], plan["entries"], plan["is_playlist"]
    organize = organize_mode(cfg, is_playlist)
    if is_playlist and organize != "flat":
        dest = folder  # the playlist is split by artist/album instead of going to its own folder
    dest.mkdir(parents=True, exist_ok=True)
    already_have = ids_in_archive(folder)
    if cfg.get("only_new") and plan.get("per_song"):
        # Watched playlist: songs already downloaded aren't even shown
        pairs = [(u, e) for u, e in zip(plan["urls"], entries) if e["id"] not in already_have]
        plan["urls"], entries = [u for u, _ in pairs], [e for _, e in pairs]
    if is_playlist:
        emit(("playlist", url, plan.get("default_album") or ""))  # e.g. to offer watching it
    sub = relative_subfolder(dest, folder)
    for e in entries:
        known[e["id"]] = e
        data = {"title": e["title"], "url": e["url"], "thumbnail": e["thumbnail"], "subfolder": sub}
        for key in ("artist", "album"):
            if e.get(key):
                data[key] = e[key]
        emit(("new", e["id"], data))
        if e["id"] in already_have:
            emit(("skipped", e["id"]))
    if plan.get("per_song"):  # list of individual songs: don't request the ones already downloaded
        batches = [([u], e["id"]) for u, e in zip(plan["urls"], entries) if e["id"] not in already_have]
    else:
        batches = [(plan["urls"], None)]

    # 2) Progress hooks (rate-limited so the UI isn't flooded)
    last = {}

    def progress_hook(d):
        video_id = (d.get("info_dict") or {}).get("id")
        wait_if_paused(pause, cancel, video_id, skip)
        if not video_id:
            return
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            text = f"Descargando {d.get('downloaded_bytes', 0) * 100 / total:.0f}%" if total else "Descargando…"
            if last.get(video_id) != text:
                last[video_id] = text
                emit(("status", video_id, text))
        elif d["status"] == "finished":
            emit(("status", video_id, "Procesando…"))

    def hook_pp(d):
        stage = STAGES.get(d.get("postprocessor"))
        video_id = (d.get("info_dict") or {}).get("id")
        if d.get("status") == "started":
            wait_if_paused(pause, cancel, video_id, skip)
        if d.get("status") == "started" and stage and video_id:
            emit(("status", video_id, stage))

    record = []
    reservations = NameReservations()

    def download(batch, video_id, with_cookies=False):
        """Downloads one song (or the whole URL if it couldn't be split). Each thread uses its own
        YoutubeDL, and the 'limit' semaphore ensures there are never more than N at once in the whole app.
        with_cookies: only when retrying an age-restricted video."""
        if video_id in skip:
            return  # cancelled before starting: it doesn't even take a slot
        with limit:
            wait_if_paused(pause, cancel, video_id, skip)
            options = build_options(cfg, folder, dest, logger, progress_hook, hook_pp,
                                          organize)
            if with_cookies:
                options.update(cookies)
            with ReadOnlyCookies(options) as options, YoutubeDL(options) as ydl:
                add_postprocessors(ydl, cfg, dest, emit, cancel, record,
                                         plan["extras"], plan["default_album"], organize,
                                         reservations, pause, skip)
                ydl.download(batch)

    def safe_download(pair, with_cookies=False):
        batch, video_id = pair
        try:
            download(batch, video_id, with_cookies)
        except DownloadCancelled:
            pass
        except Exception as e:  # an unexpected failure must not bring down the other songs
            log.exception("Fallo inesperado")
            on_failure("?", str(e))

    if batches:
        if plan.get("per_song") and n_parallel > 1 and len(batches) > 1:
            with ThreadPoolExecutor(max_workers=n_parallel) as pool:
                list(pool.map(safe_download, batches))
        else:
            for pair in batches:
                if cancel.is_set():
                    break
                safe_download(pair)

    # 2b) Age-restricted videos: second attempt, now with cookies
    url_of = {e["id"]: u for u, e in zip(plan["urls"], entries)} if plan.get("per_song") else {}
    for video_id in list(age_restricted):
        if cancel.is_set():
            break
        retried.add(video_id)
        safe_download(([url_of.get(video_id) or f"https://www.youtube.com/watch?v={video_id}"], video_id),
                     with_cookies=True)
    if cancel.is_set():
        raise DownloadCancelled("Cancelado por el usuario")

    # 3) Playlist/album: now that all tracks are known, the album ReplayGain is added
    if cfg["replaygain"] and is_playlist and len(record) >= 2:
        album = album_gain([r["data"] for r in record])
        for r in record:
            try:
                write_replaygain(r["path"], r["data"], album)
                emit(("rg", r["id"], rg_text(r["data"], album)))
            except Exception as e:
                log.warning(f"ReplayGain de álbum falló en {r['path']}: {e}")
