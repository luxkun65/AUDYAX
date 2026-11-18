"""
MOTOR de descarga de YouTube / YouTube Music (sin interfaz gráfica).

Aquí vive todo lo que descarga, convierte, etiqueta y mide el volumen.
No importa tkinter: se puede usar desde cualquier interfaz (o desde la terminal)
llamando a ejecutar_trabajo(url, cfg, emitir, cancelar).

Requisitos:
    pip install -U yt-dlp mutagen
    ffmpeg instalado y en el PATH
"""
import base64
import csv
import json
import logging
import logging.handlers
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
import urllib.request
from datetime import datetime
from pathlib import Path

import lyrics
import metadata
from yt_dlp import YoutubeDL
from yt_dlp.postprocessor import (
    EmbedThumbnailPP,
    FFmpegExtractAudioPP,
    FFmpegMetadataPP,
    FFmpegThumbnailsConvertorPP,
)
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import sanitize_filename

try:
    from mutagen import File as MutagenFile
    from mutagen.flac import Picture
    from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TXXX, ID3NoHeaderError
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4, AtomDataType, MP4Cover, MP4FreeForm
    from mutagen.oggopus import OggOpus
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

# Lee las playlists como las muestra YouTube Music (ver pistas_ytmusic). Opcional.
try:
    from ytmusicapi import YTMusic
    HAS_YTMUSIC = True
except ImportError:
    HAS_YTMUSIC = False

try:
    from yt_dlp.utils import DownloadCancelled
except ImportError:  # yt-dlp muy antiguo: actualiza con pip install -U yt-dlp
    class DownloadCancelled(Exception):
        pass


VERSION = "1.0.0"
REPO = "luxkun65/AUDYAX"
CONFIG_PATH = Path.home() / ".descargador_musica.json"
DEFAULT_DIR = str(Path.home() / "Music" / "Descargas")
DEFAULT_CONFIG = {
    "folder": DEFAULT_DIR,
    "audio_format": "mp3",
    "quality": "192",
    "normalize": False,
    "crop": True,
    "replaygain": True,
    "theme": "dark",
    "check_ytdlp": True,           # avisar al abrir si hay una versión nueva de yt-dlp
    "detect_clipboard": True,   # ofrecer pegar un enlace de YouTube copiado
    "organize": "flat",            # "plano" | "artista" | "artista_album" (ver MODOS_ORGANIZAR)
    "playlists_in_folder": True,    # las playlists van juntas en su carpeta aunque se organice
    "parallel_downloads": 2,      # canciones que se bajan a la vez (1 = una tras otra)
    "notifications": True,          # avisar cuando terminan las descargas
    "keep_in_background": True,           # al cerrar con la X, seguir abierto en la bandeja del sistema
    # Opciones avanzadas (todas apagadas al principio)
    "speed_limit_enabled": False,
    "speed_limit_mb": 2.0,      # MB/s en total (se reparte entre las descargas simultáneas)
    "proxy_enabled": False,
    "proxy": "",                     # p. ej. http://servidor:8080 o socks5://127.0.0.1:1080
    "cookies_enabled": False,
    "cookies_source": "firefox",     # firefox, chrome, edge… o "archivo"
    "cookies_file": "",           # ruta a un cookies.txt (formato Netscape)
    # Letras y metadatos
    "lyrics": "off",                  # "no" | "lrc" | "incrustada" | "ambas" (ver letras.MODOS_LETRAS)
    "musicbrainz": True,             # completar álbum y año con MusicBrainz si YouTube no los trae
    "caa_cover": True,             # usar la portada del álbum de Cover Art Archive si se encuentra
    # Mini reproductor
    "volume": 0.8,
    # Playlists vigiladas
    "watched": [],                 # [{url, nombre, carpeta, ultima_revision, nuevas}] (ver vigiladas.py)
    "check_watched": True,       # revisar al abrir AUDYAX
    "auto_download_new": False,      # bajar las nuevas sin preguntar
}
COOKIE_BROWSERS = ["firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium"]
VALID_PROXY = re.compile(r"^(https?|socks4a?|socks5h?)://[^\s/]+(:\d+)?/?$", re.IGNORECASE)
MAX_PARALLEL = 3  # más que esto hace que YouTube empiece a frenar o bloquear

# Escrituras compartidas entre hilos (faltantes.txt, log)
_FILES_LOCK = threading.Lock()

# Cómo repartir las canciones en carpetas. Cada nivel es un campo aparte de la plantilla
# de yt-dlp: así un "/" dentro de un nombre (p. ej. "AC/DC") no crea carpetas de más.
ORGANIZE_MODES = {
    "flat": [],
    "artist": ["org_artist"],
    "artist_album": ["org_artist", "org_album"],
}

# ==============================================================================
# MOTOR DE DESCARGA (sin nada de interfaz; se comunica enviando eventos)
# ==============================================================================
# Mensajes con los que YouTube pide confirmar la edad (solo en estos casos se usan las cookies)
AGE_RESTRICTION_RE = re.compile(
    r"confirm your age|age[- ]?restricted|inappropriate for some users|age verification|"
    r"confirmar tu edad|restricci[oó]n de edad", re.IGNORECASE)


def network_options(cfg: dict) -> dict:
    """Límite de velocidad y proxy según la configuración (vacío si están apagados).
    Se usan en TODAS las consultas: descargas, búsqueda y lectura de playlists.
    Las cookies NO van aquí: solo se usan para videos con restricción de edad
    (ver opciones_cookies y ejecutar_trabajo)."""
    op = {}
    if cfg.get("speed_limit_enabled"):
        try:
            mb = float(str(cfg.get("speed_limit_mb", 0)).replace(",", "."))
        except ValueError:
            mb = 0
        if mb > 0:
            # El límite es para el total: se reparte entre las descargas simultáneas
            op["ratelimit"] = max(32 * 1024, int(mb * 1024 * 1024 / parallel_downloads(cfg)))
    proxy = (cfg.get("proxy") or "").strip()
    if cfg.get("proxy_enabled") and VALID_PROXY.match(proxy):
        op["proxy"] = proxy
    return op


def cookie_options(cfg: dict) -> dict:
    """Cookies de la sesión de YouTube (vacío si la opción está apagada o mal configurada)."""
    if not cfg.get("cookies_enabled"):
        return {}
    source = cfg.get("cookies_source") or "firefox"
    if source == "file":
        if cfg.get("cookies_file") and Path(cfg["cookies_file"]).is_file():
            return {"cookiefile": cfg["cookies_file"]}
        return {}
    if source in COOKIE_BROWSERS:
        return {"cookiesfrombrowser": (source,)}
    return {}


class _ReadOnlyCookies:
    """yt-dlp vuelve a ESCRIBIR el archivo de cookies al terminar. Para no tocar el
    cookies.txt del usuario (ni que varias descargas a la vez lo escriban juntas), cada
    descarga trabaja con una copia temporal que se borra al final."""

    def __init__(self, options: dict):
        self.options, self.snapshot = options, None

    def __enter__(self):
        original = self.options.get("cookiefile")
        if original:
            fd, self.snapshot = tempfile.mkstemp(prefix="audyax_cookies_", suffix=".txt")
            os.close(fd)
            shutil.copyfile(original, self.snapshot)
            self.options["cookiefile"] = self.snapshot
        return self.options

    def __exit__(self, *exc):
        if self.snapshot:
            try:
                os.remove(self.snapshot)
            except OSError:
                pass
        return False


def parallel_downloads(cfg: dict) -> int:
    try:
        n = int(cfg.get("parallel_downloads", 1))
    except (TypeError, ValueError):
        n = 1
    return max(1, min(MAX_PARALLEL, n))


def wait_if_paused(pause, cancel, video_id=None, skip=None):
    """Si está en pausa, se queda esperando aquí (sin consumir CPU) hasta reanudar.
    Cancelar también despierta la espera. yt-dlp llama a los hooks de progreso muy a menudo,
    así que bloquearlos detiene la descarga en curso; al reanudar, sigue donde iba.

    omitir: conjunto de ids cancelados uno a uno desde la interfaz. Si id_video está ahí,
    se corta solo esa canción (cada canción usa su propio YoutubeDL)."""
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
    """Logger para yt-dlp: guarda todo en el log y avisa del motivo real de cada fallo."""

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
        # Formato típico: "ERROR: [youtube] abc123def45: Video unavailable"
        m = re.search(r"\[[^\]]+\]\s+([\w-]{11}):\s*(.*)", msg)
        if m:
            video_id, reason = m.group(1), m.group(2)
        else:
            video_id, reason = "?", re.sub(r"^ERROR:\s*", "", msg)
        self.on_failure(video_id, reason.strip())


class _Silence:
    """Logger mudo para la consulta previa de la URL."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def tags(info: dict) -> dict:
    """Los datos (tags) de una canción, tal como se muestran en la lista."""
    return {
        "title": info.get("title") or "",
        "artist": info.get("artist") or "",
        "album": info.get("album") or "",
        "year": str(info.get("release_year") or (info.get("upload_date") or "")[:4] or ""),
        "genre": info.get("genre") or (info.get("genres") or [""])[0] or "",
        "url": info.get("webpage_url") or info.get("original_url") or "",
    }


def unique_name(dest: Path, title: str, artist: str, taken=None) -> str:
    """Nombre de archivo (sin extensión) que no choque con otro de la carpeta.
    Primero solo el título; si ya existe, 'Título (Artista)'; si también, 'Título (Artista) 2', 3…
    Se compara sin distinguir mayúsculas porque Windows tampoco las distingue.
    'ocupados': nombres apartados por descargas en curso que aún no crearon su archivo."""
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
    """Con varias descargas a la vez, dos canciones con el mismo título podrían elegir el
    mismo nombre antes de que exista el archivo. Esto aparta cada nombre al elegirlo."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sections = {}  # carpeta -> {nombres}

    def reserve(self, folder: Path, title: str, artist: str) -> str:
        with self._lock:
            sections = self._sections.setdefault(str(folder), set())
            name = unique_name(folder, title, artist, sections)
            sections.add(name)
            return name


class CleanMetadata(PostProcessor):
    """Elige mejores campos que 'uploader', decide el nombre final del archivo
    (solo el título; si ya existe, añade '(Artista)') y avisa a la interfaz."""

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
        # id -> {"titulo", "artista", "album"} tal como los muestra YouTube Music. Mandan sobre
        # lo que deduce yt-dlp (que con un videoclip pondría "Canción (Official Video)" o "ArtistaVEVO").
        self.extras = extras or {}
        self.default_album = default_album

    def _complete_with_musicbrainz(self, info: dict):
        """Pide a MusicBrainz el álbum y el año reales y, si está activado, pone la portada
        del álbum como primera opción de miniatura."""
        self.emit(("status", info["id"], "Buscando álbum en MusicBrainz…"))
        try:
            found = metadata.search(info.get("title"), info.get("artist"), info.get("duration"),
                                       proxy=self.cfg.get("proxy", "") if self.cfg.get("proxy_enabled") else "")
        except Exception as e:  # sin conexión, MusicBrainz caído…: se sigue con lo de YouTube
            self.report_warning(f"MusicBrainz: {e}")
            return
        if not found:
            return
        if not info.get("album") and found["album"]:
            info["album"] = found["album"]
        if found["year"] and not (info.get("release_year") or info.get("release_date")):
            info["release_year"] = int(found["year"])
        if self.cfg.get("caa_cover"):
            # yt-dlp prueba las miniaturas empezando por la ÚLTIMA y, si una no existe (404),
            # sigue con la anterior: la portada del álbum va al final y la de YouTube queda de respaldo
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
        # No escribir número de pista: yt-dlp omite las etiquetas que valen None
        info["track_number"] = None
        if self.cfg.get("musicbrainz") and metadata.poor_data(info):
            self._complete_with_musicbrainz(info)
        if not info.get("album"):
            info["album"] = info.get("playlist_title") or self.default_album or info["title"]

        # Carpetas de organización (si están activadas) y nombre que no choque dentro de ellas
        info["org_artist"] = info["artist"] or "Artista desconocido"
        info["org_album"] = info["album"] or "Sin álbum"
        folder = self.dest
        for field in ORGANIZE_MODES.get(self.organize, []):
            folder = folder / sanitize_filename(info[field])
        info["safe_filename"] = self.reservations.reserve(folder, info["title"], info["artist"])

        self.emit(("info", info["id"], tags(info)))
        return [], info


# ------------------------------------------------------------------------------
# ReplayGain: mide el volumen y lo guarda en los tags SIN tocar el audio
# ------------------------------------------------------------------------------
REFERENCE_LUFS = -18.0   # nivel de referencia de ReplayGain 2.0
REFERENCE_R128 = -23.0   # nivel de referencia que usa Opus (EBU R128)


def analyze_loudness(path, duration=None):
    """Mide el volumen con el filtro ebur128 de ffmpeg. No modifica el archivo."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
    r = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-vn",
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **extra)
    summary = r.stderr.split("Summary:")[-1]
    m_i = re.search(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", summary)
    if not m_i:
        return None
    lufs = float(m_i.group(1))
    m_p = re.search(r"Peak:\s+(-?\d+(?:\.\d+)?)\s+dBFS", summary)
    peak = 10 ** (float(m_p.group(1)) / 20) if m_p else 1.0
    return {"lufs": lufs, "peak": peak, "gain": REFERENCE_LUFS - lufs, "dur": duration or 1.0}


def album_gain(tracks):
    """Volumen del conjunto: promedio de energía ponderado por duración (aproximado)."""
    total = sum(p["dur"] for p in tracks)
    energy = sum(p["dur"] * 10 ** (p["lufs"] / 10) for p in tracks) / total
    lufs = 10 * math.log10(energy)
    return {"lufs": lufs, "peak": max(p["peak"] for p in tracks), "gain": REFERENCE_LUFS - lufs}


def rg_text(track, album=None):
    t = f"pista {track['gain']:.2f} dB"
    if album:
        t += f" · álbum {album['gain']:.2f} dB"
    return t


def file_info(path) -> dict:
    """Datos técnicos del archivo ya descargado (formato, duración, bitrate, tamaño, muestreo).
    Solo lee; no modifica nada. Los campos que no se puedan leer quedan vacíos."""
    p = Path(path)
    data = {"audio_format": p.suffix.lstrip(".").upper(), "duration": "", "bitrate": "",
             "sample_rate": "", "size": ""}
    try:
        data["size"] = f"{p.stat().st_size / 1048576:.1f} MB"
    except OSError:
        pass
    if HAS_MUTAGEN:
        try:
            audio = MutagenFile(p)
            if audio is not None and audio.info is not None:
                seg = int(getattr(audio.info, "length", 0) or 0)
                if seg:
                    data["duration"] = f"{seg // 60}:{seg % 60:02d}"
                br = getattr(audio.info, "bitrate", 0) or 0
                if br:
                    data["bitrate"] = f"{round(br / 1000)} kbps"
                sr = getattr(audio.info, "sample_rate", 0) or 0
                if sr:
                    data["sample_rate"] = f"{sr / 1000:g} kHz"
        except Exception:
            pass
    return data


def write_replaygain(path: Path, track: dict, album=None):
    """Escribe los tags ReplayGain en el formato que corresponda a cada archivo."""
    if not HAS_MUTAGEN:
        raise RuntimeError("falta mutagen (pip install mutagen)")
    ext = path.suffix.lower()

    pairs = {"REPLAYGAIN_TRACK_GAIN": f"{track['gain']:.2f} dB",
             "REPLAYGAIN_TRACK_PEAK": f"{track['peak']:.6f}"}
    if album:
        pairs["REPLAYGAIN_ALBUM_GAIN"] = f"{album['gain']:.2f} dB"
        pairs["REPLAYGAIN_ALBUM_PEAK"] = f"{album['peak']:.6f}"

    if ext == ".mp3":
        audio = MP3(path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        for key, value in pairs.items():
            audio.tags.delall(f"TXXX:{key}")
            audio.tags.add(TXXX(encoding=3, desc=key, text=[value]))
        audio.save(v2_version=3)
    elif ext in (".m4a", ".mp4"):
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        for key, value in pairs.items():
            audio.tags[f"----:com.apple.iTunes:{key.lower()}"] = [
                MP4FreeForm(value.encode("utf-8"), dataformat=AtomDataType.UTF8)]
        audio.save()
    elif ext == ".opus":
        # Opus usa otro estándar: ganancia en formato Q7.8 relativa a -23 LUFS
        def q78(lufs):
            return str(max(-32768, min(32767, round((REFERENCE_R128 - lufs) * 256))))
        audio = OggOpus(path)
        audio["R128_TRACK_GAIN"] = [q78(track["lufs"])]
        if album:
            audio["R128_ALBUM_GAIN"] = [q78(album["lufs"])]
        audio.save()
    else:
        raise ValueError(f"el formato {ext} no admite ReplayGain en este programa")


def strip_track_number(path: Path):
    """Borra la etiqueta de número de pista del archivo ya terminado (p. ej. el '63').
    Se hace al final para que funcione venga de donde venga ese dato."""
    if not HAS_MUTAGEN:
        return
    ext = path.suffix.lower()
    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return
        if tags.getall("TRCK"):
            tags.delall("TRCK")
            tags.save(v2_version=3)
    elif ext in (".m4a", ".mp4"):
        audio = MP4(path)
        if audio.tags is not None and "trkn" in audio.tags:
            del audio.tags["trkn"]
            audio.save()
    elif ext == ".opus":
        audio = OggOpus(path)
        changed = False
        for key in ("tracknumber", "tracktotal", "totaltracks"):
            if key in audio:
                del audio[key]
                changed = True
        if changed:
            audio.save()


class ExtractAudio(FFmpegExtractAudioPP):
    """FFmpegExtractAudio que puede obligar a recodificar.

    yt-dlp copia el audio sin tocarlo cuando el códec ya coincide (p. ej. AAC -> m4a,
    o Opus -> opus). Eso es ideal, salvo cuando se quiere normalizar: copiar y aplicar
    un filtro a la vez no es posible (con m4a la normalización se ignoraba en silencio
    y con opus FFmpeg daba error). Con forzar=True se recodifica siempre."""

    def __init__(self, downloader=None, force=False, **kw):
        super().__init__(downloader, **kw)
        self.force = force

    @classmethod
    def pp_key(cls):
        # Mismo nombre que el original: así siguen valiendo los argumentos
        # "extractaudio+ffmpeg_o" y los mensajes de progreso.
        return "ExtractAudio"

    def get_audio_codec(self, path):
        codec = super().get_audio_codec(path)
        # Un códec ficticio distinto del de destino hace que yt-dlp convierta
        return "forzar-recodificacion" if (self.force and codec) else codec


def should_normalize(cfg: dict) -> bool:
    return bool(cfg.get("normalize")) and cfg.get("audio_format") != "original"


class MarkDone(PostProcessor):
    """Se ejecuta al final (after_move): el archivo ya está tageado y en su sitio."""

    def __init__(self, downloader, emit, replaygain, record, cfg=None):
        super().__init__(downloader)
        self.emit = emit
        self.replaygain = replaygain
        self.record = record  # canciones analizadas (para el ReplayGain de álbum)
        self.cfg = cfg or {}

    def run(self, info):
        data = tags(info)
        path = info.get("filepath") or info.get("_filename") or ""
        data["path"] = path
        if path:
            try:
                strip_track_number(Path(path))
            except Exception as e:  # un fallo aquí no invalida la descarga
                self.report_warning(f"No se pudo quitar el número de pista: {e}")
            # yt-dlp escribe como año la fecha de SUBIDA del video; si se conoce el año real del
            # lanzamiento (YouTube Music o MusicBrainz), se escribe ese, que es el que se muestra
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
            except Exception as e:  # un fallo aquí no invalida la descarga
                self.report_warning(f"ReplayGain: {e}")
        mode = self.cfg.get("lyrics", "off")
        if path and mode in lyrics.LYRICS_MODES and mode != "off":
            data["lyrics"] = self._lyrics(info, path, mode)
        if info.get("_musicbrainz"):
            data["musicbrainz"] = True
        self.emit(("done", info["id"], data))
        return [], info

    def _lyrics(self, info, path, mode) -> str:
        """Busca la letra en LRCLIB y la guarda. Devuelve "sincronizada", "texto" o ""."""
        self.emit(("status", info["id"], "Buscando letra…"))
        try:
            lyric = lyrics.find_lyrics(info.get("title"), info.get("artist"), info.get("album"),
                                        info.get("duration"),
                                        proxy=self.cfg.get("proxy", "") if self.cfg.get("proxy_enabled") else "")
            if not lyric or not lyrics.save_lyrics(path, lyric, mode):
                return ""
            return "synced" if lyric.get("synced") else "text"
        except Exception as e:  # sin letra no es un error de la descarga
            self.report_warning(f"Letra: {e}")
            return ""


def relative_subfolder(dest: Path, folder: Path) -> str:
    try:
        rel = dest.relative_to(folder)
    except ValueError:
        return ""
    return "" if str(rel) == "." else str(rel)


def _to_entry(e: dict) -> dict:
    url = str(e.get("webpage_url") or e.get("url") or "")
    if not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={e['id']}"
    thumbnail = e.get("thumbnail") or ""
    if not thumbnail and e.get("thumbnails"):
        thumbnail = (e["thumbnails"][-1] or {}).get("url") or ""
    return {"id": e["id"], "title": e.get("track") or e.get("title") or e["id"],
            "url": url, "thumbnail": thumbnail}


def clean_url(url: str) -> str:
    """Arregla dos trampas de los enlaces de YouTube Music:
    - 'watch?v=X&list=RD…' es la "radio" automática de esa canción: yt-dlp bajaría la radio
      entera (decenas de canciones que no son tuyas). Se deja solo la canción.
    - 'music.youtube.com/watch?v=X' se pasa a www.youtube.com (es el mismo video).
    Las playlists de music.youtube.com se dejan igual: las resuelve pistas_ytmusic."""
    url = (url or "").strip()
    parts = urllib.parse.urlsplit(url)
    if "youtube.com" not in parts.netloc or parts.path != "/watch":
        return url
    qs = urllib.parse.parse_qs(parts.query)
    v = (qs.get("v") or [""])[0]
    song_list = (qs.get("list") or [""])[0]
    if not v:
        return url
    if song_list and not song_list.startswith("RD"):
        if parts.netloc.startswith("music."):
            return url  # canción dentro de una playlist de YouTube Music: se baja la playlist
        return f"https://www.youtube.com/watch?v={v}&list={song_list}"
    return f"https://www.youtube.com/watch?v={v}"


def is_ytmusic_playlist(url: str) -> bool:
    parts = urllib.parse.urlsplit(url or "")
    if not parts.netloc.startswith("music.youtube.com"):
        return False
    if parts.path.startswith("/browse/MPRE"):
        return True  # página de un álbum
    song_list = (urllib.parse.parse_qs(parts.query).get("list") or [""])[0]
    return bool(song_list) and not song_list.startswith("RD")


def _largest_thumbnail(thumbnails) -> str:
    thumbnails = [t for t in (thumbnails or []) if t and t.get("url")]
    if not thumbnails:
        return ""
    return max(thumbnails, key=lambda t: (t.get("width") or 0))["url"]


def ytmusic_tracks(url: str, client=None, proxy: str = ""):
    """Lee una playlist o álbum de music.youtube.com como lo hace YouTube Music.

    Por qué: yt-dlp no soporta YouTube Music y redirige a la misma playlist en youtube.com,
    que guarda el ID ORIGINAL de cada canción. Si esa versión fue retirada o está bloqueada,
    yt-dlp falla con "Video unavailable", mientras YouTube Music reproduce en silencio otra
    versión disponible (otro ID). Aquí se usa el ID que YouTube Music realmente reproduce.

    Devuelve (titulo, pistas) o None si no se puede (sin ytmusicapi, sin conexión, etc.).
    Cada pista: id, titulo, artista, album, url, miniatura, disponible."""
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
        song_list = (urllib.parse.parse_qs(parts.query).get("list") or [""])[0]
        data = client.get_playlist(song_list, limit=None)
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


def analyze_url(url: str, folder: Path, subfolder: str = "", net=None):
    """Devuelve (carpeta_destino, lista_de_canciones, es_playlist, titulo_playlist).
    Si es playlist, usa subcarpeta. 'subcarpeta' fuerza el destino (se usa al reintentar
    una canción que venía de una playlist)."""
    dest, entries, is_playlist = (folder / subfolder if subfolder else folder), [], False
    title = ""
    try:
        options = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                    "ignoreerrors": True, "logger": _Silence(), **(net or {})}
        with _ReadOnlyCookies(options) as options, YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if info and info.get("_type") == "playlist":
            is_playlist = True
            title = info.get("title") or ""
            if info.get("title") and not subfolder:
                dest = folder / sanitize_filename(info["title"])
            for e in info.get("entries") or []:
                if e and e.get("id") and e.get("_type") != "playlist" and e.get("ie_key") != "YoutubeTab":
                    entries.append(_to_entry(e))
        elif info and info.get("id"):
            entries.append(_to_entry(info))
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
    """Log en carpeta/descarga.log. Si ya apunta ahí no se toca (puede haber otras descargas
    escribiendo en él a la vez)."""
    log = logging.getLogger("audyax")
    log.setLevel(logging.DEBUG)
    path = os.path.abspath(folder / "descarga.log")
    with _FILES_LOCK:
        if any(getattr(h, "baseFilename", None) == path for h in log.handlers):
            return log
        for h in list(log.handlers):
            h.close()
            log.removeHandler(h)
        # Rota a los ~2 MB para que el log no crezca sin límite dentro de la carpeta de música
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
    """Ruta de salida para yt-dlp, p. ej. destino/%(org_artista)s/%(org_album)s/%(nombre_archivo)s.%(ext)s
    No se pone "/" dentro de un campo porque yt-dlp lo convierte en "⧹"."""
    levels = [f"%({field})s" for field in ORGANIZE_MODES.get(organize, [])]
    return str(dest.joinpath(*levels, "%(safe_filename)s.%(ext)s"))


def organize_mode(cfg: dict, is_playlist: bool) -> str:
    """Organización que se aplica a este trabajo concreto."""
    mode = cfg.get("organize", "flat")
    if mode not in ORGANIZE_MODES:
        return "flat"
    if cfg.get("subfolder"):
        return "flat"  # reintento de una canción de playlist: vuelve junto a su playlist
    if is_playlist and cfg.get("playlists_in_folder", True):
        return "flat"
    return mode


def build_options(cfg, folder, dest, logger, progress_hook, hook_pp,
                       organize="flat") -> dict:
    """Opciones de yt-dlp. Los postprocesadores se añaden aparte (agregar_postprocesadores)
    porque uno de ellos es una clase propia que no se puede declarar por nombre."""
    template = output_template(dest, organize)

    ppa = {}
    if cfg["crop"]:
        ppa["thumbnailsconvertor+ffmpeg_o"] = [
            "-c:v", "mjpeg",
            "-vf", "crop='if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'",
        ]
    if should_normalize(cfg):
        ppa["extractaudio+ffmpeg_o"] = ["-af", "loudnorm=I=-14:TP=-1:LRA=11"]

    return {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        # "pl_thumbnail" vacío = no guardar la imagen suelta de la playlist
        "outtmpl": {"default": template, "pl_thumbnail": ""},
        "postprocessor_args": ppa,
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
    """Cadena de postprocesado, en orden: portada -> nombre/tags -> audio -> metadatos -> portada
    incrustada -> ReplayGain y aviso de 'listo'."""
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
    """Decide qué se va a bajar. Devuelve destino, entradas, es_playlist, las URLs que se
    le pasan a yt-dlp y, si vino de YouTube Music, sus títulos/artistas/álbumes por id."""
    url = clean_url(url)
    if is_ytmusic_playlist(url):
        try:
            res = ytmusic_tracks(url, proxy=(net or {}).get("proxy", ""))
        except Exception as e:  # sin conexión, playlist privada, cambio de la API…
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
        # Una URL por canción: así se pueden bajar varias a la vez
        return {"dest": dest, "entries": entries, "is_playlist": True,
                "urls": [e["url"] for e in entries], "extras": {},
                "default_album": title, "per_song": True}
    return {"dest": dest, "entries": entries, "is_playlist": is_playlist,
            "urls": [url], "extras": {}, "default_album": title, "per_song": False}


def run_job(url: str, cfg: dict, emit, cancel, pause=None, limit=None, skip=None):
    """Descarga una URL (video o playlist) y va enviando eventos a la interfaz.

    pausa: threading.Event; mientras esté activado, las descargas se detienen.
    limite: semáforo compartido por toda la app con el máximo de canciones a la vez
            (si no se pasa, se crea uno con cfg["descargas_simultaneas"]).
    omitir: conjunto de ids que la interfaz cancela uno a uno; se puede ir llenando mientras
            el trabajo corre (las que aún no empezaron se saltan, la que está en curso se corta)."""
    if skip is None:
        skip = set()
    n_parallel = parallel_downloads(cfg)
    if limit is None:
        limit = threading.BoundedSemaphore(n_parallel)
    folder = Path(cfg["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    log = setup_log(folder)
    known = {}  # id -> entrada (para que faltantes.txt diga QUÉ canción falló)

    cookies = cookie_options(cfg)
    age_restricted = []          # canciones con restricción de edad que se reintentarán con cookies
    retried = set()   # (las que ya se reintentaron: si vuelven a fallar, es un error normal)

    def on_failure(video_id, reason):
        if AGE_RESTRICTION_RE.search(reason) and video_id != "?":
            if cookies and video_id not in retried:
                # No es un error todavía: se vuelve a intentar con la sesión del usuario
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

    # 1) Mostrar de inmediato todas las canciones (amarillas, "en cola")
    net = network_options(cfg)
    plan = resolve(url, folder, cfg.get("subfolder") or "", log, net)
    if cfg.get("extras"):  # p. ej. al reintentar una canción que venía de YouTube Music
        plan["extras"] = {**cfg["extras"], **plan["extras"]}
    dest, entries, is_playlist = plan["dest"], plan["entries"], plan["is_playlist"]
    organize = organize_mode(cfg, is_playlist)
    if is_playlist and organize != "flat":
        dest = folder  # la playlist se reparte por artista/álbum en vez de ir a su carpeta
    dest.mkdir(parents=True, exist_ok=True)
    already_have = ids_in_archive(folder)
    if cfg.get("only_new") and plan.get("per_song"):
        # Playlist vigilada: las que ya están ni siquiera se muestran
        pairs = [(u, e) for u, e in zip(plan["urls"], entries) if e["id"] not in already_have]
        plan["urls"], entries = [u for u, _ in pairs], [e for _, e in pairs]
    if is_playlist:
        emit(("playlist", url, plan.get("default_album") or ""))  # p. ej. para ofrecer vigilarla
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
    if plan.get("per_song"):  # lista de canciones sueltas: no pedir las que ya están
        batches = [([u], e["id"]) for u, e in zip(plan["urls"], entries) if e["id"] not in already_have]
    else:
        batches = [(plan["urls"], None)]

    # 2) Hooks de progreso (con límite para no saturar la interfaz)
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
        """Baja una canción (o la URL entera si no se pudo separar). Cada hilo usa su propio
        YoutubeDL, y el semáforo 'limite' asegura que no haya más de N a la vez en toda la app.
        con_cookies: solo en el reintento de un video con restricción de edad."""
        if video_id in skip:
            return  # cancelada antes de empezar: ni siquiera ocupa un cupo
        with limit:
            wait_if_paused(pause, cancel, video_id, skip)
            options = build_options(cfg, folder, dest, logger, progress_hook, hook_pp,
                                          organize)
            if with_cookies:
                options.update(cookies)
            with _ReadOnlyCookies(options) as options, YoutubeDL(options) as ydl:
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
        except Exception as e:  # un fallo inesperado no debe tumbar las demás canciones
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

    # 2b) Videos con restricción de edad: segundo intento, ahora sí con las cookies
    url_of = {e["id"]: u for u, e in zip(plan["urls"], entries)} if plan.get("per_song") else {}
    for video_id in list(age_restricted):
        if cancel.is_set():
            break
        retried.add(video_id)
        safe_download(([url_of.get(video_id) or f"https://www.youtube.com/watch?v={video_id}"], video_id),
                     with_cookies=True)
    if cancel.is_set():
        raise DownloadCancelled("Cancelado por el usuario")

    # 3) Playlist/álbum: ahora que se conocen todas las pistas, se añade el ReplayGain de álbum
    if cfg["replaygain"] and is_playlist and len(record) >= 2:
        album = album_gain([r["data"] for r in record])
        for r in record:
            try:
                write_replaygain(r["path"], r["data"], album)
                emit(("rg", r["id"], rg_text(r["data"], album)))
            except Exception as e:
                log.warning(f"ReplayGain de álbum falló en {r['path']}: {e}")


# ==============================================================================
# DETECCIÓN DE ENLACES (portapapeles y arrastrar/soltar)
# ==============================================================================
_RE_YOUTUBE = re.compile(
    r"^https?://(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?|playlist\?|shorts/|live/|browse/)|youtu\.be/[\w-])",
    re.IGNORECASE)


def is_youtube_link(text: str) -> bool:
    """True si el texto es (solo) un enlace de YouTube o YouTube Music."""
    t = (text or "").strip()
    return bool(t) and " " not in t and "\n" not in t and bool(_RE_YOUTUBE.match(t))


def extract_links(text: str) -> list:
    """Enlaces de YouTube dentro de un texto cualquiera (p. ej. lo que llega al soltar
    algo en la ventana, que puede venir entre llaves o como lista de URLs). Sin repetidos."""
    seen, links = set(), []
    for raw_data in re.findall(r"https?://[^\s{}<>\"']+", text or ""):
        url = raw_data.rstrip(".,;)")
        if is_youtube_link(url) and url not in seen:
            seen.add(url)
            links.append(url)
    return links


# ==============================================================================
# BÚSQUEDA POR NOMBRE ("artista canción")
# ==============================================================================
def _duration_text(seg) -> str:
    try:
        seg = int(seg)
    except (TypeError, ValueError):
        return ""
    h, rest = divmod(seg, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"


def _to_result(e: dict) -> dict:
    data = _to_entry(e)
    data["canal"] = re.sub(r"\s*-\s*Topic$", "", e.get("channel") or e.get("uploader") or "")
    data["duration"] = _duration_text(e.get("duration"))
    if not data["thumbnail"]:
        data["thumbnail"] = f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg"
    return data


def search(text: str, max_results: int = 10, net=None) -> list:
    """Busca en YouTube y devuelve una lista de resultados
    (id, titulo, url, miniatura, canal, duracion). Lanza la excepción si falla."""
    text = (text or "").strip()
    if not text:
        return []
    options = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                "logger": _Silence(), **(net or {})}
    with _ReadOnlyCookies(options) as options, YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"ytsearch{int(max_results)}:{text}", download=False)
    return [_to_result(e) for e in (info or {}).get("entries") or []
            if e and e.get("id") and e.get("ie_key") in (None, "Youtube")]


# ==============================================================================
# ACTUALIZAR yt-dlp
# ==============================================================================
def ytdlp_version() -> str:
    from yt_dlp.version import __version__
    return __version__


def _as_tuple(version: str) -> tuple:
    return tuple(int(p) for p in re.findall(r"\d+", version or ""))


def is_newer(candidate: str, current: str) -> bool:
    return _as_tuple(candidate) > _as_tuple(current)


def latest_ytdlp_version(timeout: int = 8) -> str:
    """Última versión publicada en PyPI. Lanza la excepción si no hay conexión."""
    req = urllib.request.Request("https://pypi.org/pypi/yt-dlp/json",
                                 headers={"User-Agent": "AUDYAX"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))["info"]["version"]


def latest_app_version(timeout: int = 8) -> str:
    """Última versión publicada de AUDYAX en GitHub Releases (p. ej. "1.0.1").
    Lanza la excepción si no hay conexión o aún no hay versiones publicadas."""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest",
                                 headers={"User-Agent": "AUDYAX", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))["tag_name"].lstrip("vV")


def program_folder() -> Path:
    """Carpeta donde está AUDYAX: la del .exe si está empaquetado, o la de este archivo."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def setup_bundled_ffmpeg() -> str:
    """Si junto al programa hay una carpeta 'ffmpeg' (la versión .exe la trae), se pone
    primera en el PATH para que la usen yt-dlp y el cálculo de ReplayGain.
    Devuelve la carpeta usada o "" si no hay ninguna."""
    candidates = [program_folder() / "ffmpeg"]
    if getattr(sys, "_MEIPASS", None):
        candidates.append(Path(sys._MEIPASS) / "ffmpeg")
    for folder in candidates:
        exe = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
        if (folder / exe).is_file():
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
            return str(folder)
    return ""


def can_self_update() -> bool:
    """En el ejecutable de PyInstaller yt-dlp va empaquetado y pip no puede cambiarlo."""
    return not getattr(sys, "frozen", False)


def update_ytdlp() -> tuple:
    """Actualiza yt-dlp (y ytmusicapi, que también se rompe cuando YouTube Music cambia)
    con pip. Devuelve (ok, mensaje). Hay que reiniciar la app
    para que se use la versión nueva (la vieja ya está cargada en memoria)."""
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


# ==============================================================================
# EDITOR DE ETIQUETAS (archivos ya descargados)
# ==============================================================================
TAG_FIELDS = ("title", "artist", "album", "year", "genre")
EDITABLE_FORMATS = (".mp3", ".m4a", ".mp4", ".opus")

_ID3 = {"title": TIT2, "artist": TPE1, "album": TALB, "year": TDRC, "genre": TCON} if HAS_MUTAGEN else {}
_MP4 = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "year": "\xa9day", "genre": "\xa9gen"}
_VORBIS = {"title": "title", "artist": "artist", "album": "album", "year": "date", "genre": "genre"}


def is_editable(path) -> bool:
    return HAS_MUTAGEN and Path(path).suffix.lower() in EDITABLE_FORMATS


def _first(value) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "")


def valid_filename(text: str, extension: str = "") -> str:
    """Convierte lo que escribe el usuario en un nombre de archivo válido (sin extensión):
    quita caracteres prohibidos en Windows (\\ / : * ? " < > |), espacios y puntos sobrantes,
    y la extensión si la escribió (p. ej. "Canción.mp3" -> "Canción")."""
    text = (text or "").strip()
    if extension and text.lower().endswith(extension.lower()):
        text = text[: -len(extension)]
    return sanitize_filename(text).strip().rstrip(". ").strip()


def rename_file(path, new_name: str) -> Path:
    """Cambia el nombre del archivo (la extensión se conserva) y devuelve la ruta nueva.
    Nunca sobrescribe otro archivo: si ya existe uno con ese nombre, lanza FileExistsError.
    Sí permite cambiar solo mayúsculas/minúsculas ("cancion" -> "Canción")."""
    path = Path(path)
    name = valid_filename(new_name, path.suffix)
    if not name:
        raise ValueError("El nombre del archivo no puede quedar vacío.")
    dest = path.with_name(name + path.suffix)
    # Se compara el texto exacto: en Windows "cancion.mp3" == "Cancion.mp3" como rutas, y
    # entonces un cambio solo de mayúsculas no se haría.
    if dest.name == path.name:
        return path
    if dest.exists() and not os.path.samefile(dest, path):
        raise FileExistsError(f"Ya existe «{dest.name}» en esa carpeta.")
    path.rename(dest)
    return dest


def read_tags(path) -> dict:
    """Título, artista, álbum, año y género que tiene guardados el archivo."""
    path = Path(path)
    ext = path.suffix.lower()
    data = dict.fromkeys(TAG_FIELDS, "")
    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return data
        for field, klass in _ID3.items():
            container = tags.get(klass.__name__)
            if container is not None and container.text:
                data[field] = str(container.text[0])
    elif ext in (".m4a", ".mp4"):
        tags = MP4(path).tags or {}
        for field, key in _MP4.items():
            data[field] = _first(tags.get(key))
    elif ext == ".opus":
        audio = OggOpus(path)
        for field, key in _VORBIS.items():
            data[field] = _first(audio.get(key))
    else:
        raise ValueError(f"el formato {ext} no se puede editar")
    data["year"] = data["year"][:4] if re.match(r"\d{4}", data["year"]) else data["year"]
    return data


def read_cover(path):
    """Bytes de la portada incrustada, o None si no tiene."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            photos = ID3(path).getall("APIC")
            return bytes(photos[0].data) if photos else None
        if ext in (".m4a", ".mp4"):
            covers = (MP4(path).tags or {}).get("covr")
            return bytes(covers[0]) if covers else None
        if ext == ".opus":
            blocks = OggOpus(path).get("metadata_block_picture")
            return Picture(base64.b64decode(blocks[0])).data if blocks else None
    except Exception:
        return None
    return None


def write_tags(path, data: dict, cover: bytes = None):
    """Guarda los campos indicados (un texto vacío borra la etiqueta) y, si se pasa,
    reemplaza la portada (bytes JPEG). No toca el audio ni el ReplayGain."""
    if not HAS_MUTAGEN:
        raise RuntimeError("falta mutagen (pip install mutagen)")
    path = Path(path)
    ext = path.suffix.lower()
    fields = {k: (str(v).strip() if v is not None else "") for k, v in data.items()
              if k in TAG_FIELDS}

    if ext == ".mp3":
        audio = MP3(path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        for field, value in fields.items():
            klass = _ID3[field]
            audio.tags.delall(klass.__name__)
            if value:
                audio.tags.add(klass(encoding=3, text=[value]))
        if cover:
            audio.tags.delall("APIC")
            audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
        audio.save(v2_version=3)
    elif ext in (".m4a", ".mp4"):
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        for field, value in fields.items():
            key = _MP4[field]
            if value:
                audio.tags[key] = [value]
            elif key in audio.tags:
                del audio.tags[key]
        if cover:
            audio.tags["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
    elif ext == ".opus":
        audio = OggOpus(path)
        for field, value in fields.items():
            key = _VORBIS[field]
            if value:
                audio[key] = [value]
            elif key in audio:
                del audio[key]
        if cover:
            photo = Picture()
            photo.type, photo.mime, photo.desc, photo.data = 3, "image/jpeg", "Cover", cover
            audio["metadata_block_picture"] = [base64.b64encode(photo.write()).decode("ascii")]
        audio.save()
    else:
        raise ValueError(f"el formato {ext} no se puede editar")


# ==============================================================================
# EXPORTAR LA LISTA (.m3u y .csv)
# ==============================================================================
CSV_COLUMNS = [("num", "#"), ("status", "Estado"), ("title", "Título"), ("artist", "Artista"),
                ("album", "Álbum"), ("year", "Año"), ("genre", "Género"), ("url", "Enlace"),
                ("path", "Archivo"), ("reason", "Motivo")]


def duration_seconds(path) -> int:
    """Duración en segundos (o -1 si no se puede leer, como pide el formato M3U)."""
    if HAS_MUTAGEN:
        try:
            audio = MutagenFile(path)
            if audio is not None and audio.info is not None:
                return int(round(audio.info.length))
        except Exception:
            pass
    return -1


def export_m3u(dest, songs) -> int:
    """Escribe una lista de reproducción M3U extendida (UTF-8) con las canciones que existen.
    Las rutas quedan relativas a la carpeta de la lista cuando se puede, así la lista sigue
    funcionando si se mueve toda la carpeta de música. Devuelve cuántas canciones escribió."""
    dest = Path(dest)
    base = dest.parent.resolve()
    lines = ["#EXTM3U"]
    for c in songs:
        path = Path(c.get("path") or "")
        if not c.get("path") or not path.is_file():
            continue
        try:
            path_text = os.path.relpath(path.resolve(), base)
        except ValueError:  # otra unidad en Windows (C: vs D:)
            path_text = str(path.resolve())
        name = " - ".join(x for x in (c.get("artist"), c.get("title")) if x) or path.stem
        lines.append(f"#EXTINF:{duration_seconds(path)},{name}")
        lines.append(path_text)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return (len(lines) - 1) // 2


def _csv_cell(value) -> str:
    text = "" if value is None else str(value)
    # Evita que Excel interprete un título como "=…" como fórmula
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_csv(dest, rows) -> int:
    """Escribe la lista como CSV. Usa ';' y UTF-8 con BOM para que Excel en español lo abra
    directamente en columnas y con tildes. Devuelve cuántas filas escribió."""
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        csv_writer = csv.writer(f, delimiter=";")
        csv_writer.writerow([title for _, title in CSV_COLUMNS])
        for row in rows:
            csv_writer.writerow([_csv_cell(row.get(key, "")) for key, _ in CSV_COLUMNS])
    return len(rows)


# ==============================================================================
# CONFIGURACIÓN GUARDADA
# ==============================================================================
def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict):
    # Solo se guardan las preferencias conocidas (no datos de un trabajo concreto)
    data = {k: cfg[k] for k in DEFAULT_CONFIG if k in cfg}
    try:
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass