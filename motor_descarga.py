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
import json
import logging
import logging.handlers
import math
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

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
    HAY_MUTAGEN = True
except ImportError:
    HAY_MUTAGEN = False

try:
    from yt_dlp.utils import DownloadCancelled
except ImportError:  # yt-dlp muy antiguo: actualiza con pip install -U yt-dlp
    class DownloadCancelled(Exception):
        pass


CONFIG_PATH = Path.home() / ".descargador_musica.json"
DIR_POR_DEFECTO = str(Path.home() / "Music" / "Descargas")
CONFIG_POR_DEFECTO = {
    "carpeta": DIR_POR_DEFECTO,
    "formato": "mp3",
    "calidad": "192",
    "normalizar": False,
    "recorte": True,
    "replaygain": True,
    "tema": "dark",
    "revisar_ytdlp": True,           # avisar al abrir si hay una versión nueva de yt-dlp
    "detectar_portapapeles": True,   # ofrecer pegar un enlace de YouTube copiado
}

# ==============================================================================
# MOTOR DE DESCARGA (sin nada de interfaz; se comunica enviando eventos)
# ==============================================================================
class RegistroYtdlp:
    """Logger para yt-dlp: guarda todo en el log y avisa del motivo real de cada fallo."""

    def __init__(self, log, al_fallar):
        self.log = log
        self.al_fallar = al_fallar

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
            id_video, motivo = m.group(1), m.group(2)
        else:
            id_video, motivo = "?", re.sub(r"^ERROR:\s*", "", msg)
        self.al_fallar(id_video, motivo.strip())


class _Silencio:
    """Logger mudo para la consulta previa de la URL."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def etiquetas(info: dict) -> dict:
    """Los datos (tags) de una canción, tal como se muestran en la lista."""
    return {
        "titulo": info.get("title") or "",
        "artista": info.get("artist") or "",
        "album": info.get("album") or "",
        "anio": str(info.get("release_year") or (info.get("upload_date") or "")[:4] or ""),
        "genero": info.get("genre") or (info.get("genres") or [""])[0] or "",
        "url": info.get("webpage_url") or info.get("original_url") or "",
    }


def nombre_unico(destino: Path, titulo: str, artista: str) -> str:
    """Nombre de archivo (sin extensión) que no choque con otro de la carpeta.
    Primero solo el título; si ya existe, 'Título (Artista)'; si también, 'Título (Artista) 2', 3…
    Se compara sin distinguir mayúsculas porque Windows tampoco las distingue."""
    base = sanitize_filename(titulo)
    try:
        usados = {f.stem.casefold() for f in destino.iterdir()} if destino.exists() else set()
    except OSError:
        usados = set()
    if base.casefold() not in usados:
        return base
    con_artista = f"{base} ({sanitize_filename(artista)})"
    nombre, n = con_artista, 2
    while nombre.casefold() in usados:
        nombre = f"{con_artista} {n}"
        n += 1
    return nombre


class LimpiarMetadata(PostProcessor):
    """Elige mejores campos que 'uploader', decide el nombre final del archivo
    (solo el título; si ya existe, añade '(Artista)') y avisa a la interfaz."""

    def __init__(self, downloader, destino: Path, emitir, cancelar):
        super().__init__(downloader)
        self.destino = destino
        self.emitir = emitir
        self.cancelar = cancelar

    def run(self, info):
        if self.cancelar.is_set():
            raise DownloadCancelled("Cancelado por el usuario")

        artista = (
            info.get("artist")
            or ", ".join(info.get("artists") or [])
            or info.get("creator")
            or info.get("uploader")
            or "Unknown Artist"
        )
        info["artist"] = re.sub(r"\s*-\s*Topic$", "", artista).strip()
        if info.get("track"):
            info["title"] = info["track"]
        # No escribir número de pista: yt-dlp omite las etiquetas que valen None
        info["track_number"] = None
        if not info.get("album"):
            info["album"] = info.get("playlist_title") or info["title"]

        info["nombre_archivo"] = nombre_unico(self.destino, info["title"], info["artist"])

        self.emitir(("info", info["id"], etiquetas(info)))
        return [], info


# ------------------------------------------------------------------------------
# ReplayGain: mide el volumen y lo guarda en los tags SIN tocar el audio
# ------------------------------------------------------------------------------
REFERENCIA_LUFS = -18.0   # nivel de referencia de ReplayGain 2.0
REFERENCIA_R128 = -23.0   # nivel de referencia que usa Opus (EBU R128)


def analizar_volumen(ruta, duracion=None):
    """Mide el volumen con el filtro ebur128 de ffmpeg. No modifica el archivo."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
    r = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(ruta), "-vn",
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **extra)
    resumen = r.stderr.split("Summary:")[-1]
    m_i = re.search(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", resumen)
    if not m_i:
        return None
    lufs = float(m_i.group(1))
    m_p = re.search(r"Peak:\s+(-?\d+(?:\.\d+)?)\s+dBFS", resumen)
    pico = 10 ** (float(m_p.group(1)) / 20) if m_p else 1.0
    return {"lufs": lufs, "pico": pico, "gain": REFERENCIA_LUFS - lufs, "dur": duracion or 1.0}


def calcular_album(pistas):
    """Volumen del conjunto: promedio de energía ponderado por duración (aproximado)."""
    total = sum(p["dur"] for p in pistas)
    energia = sum(p["dur"] * 10 ** (p["lufs"] / 10) for p in pistas) / total
    lufs = 10 * math.log10(energia)
    return {"lufs": lufs, "pico": max(p["pico"] for p in pistas), "gain": REFERENCIA_LUFS - lufs}


def texto_rg(pista, album=None):
    t = f"pista {pista['gain']:.2f} dB"
    if album:
        t += f" · álbum {album['gain']:.2f} dB"
    return t


def info_archivo(ruta) -> dict:
    """Datos técnicos del archivo ya descargado (formato, duración, bitrate, tamaño, muestreo).
    Solo lee; no modifica nada. Los campos que no se puedan leer quedan vacíos."""
    p = Path(ruta)
    datos = {"formato": p.suffix.lstrip(".").upper(), "duracion": "", "bitrate": "",
             "muestreo": "", "tamano": ""}
    try:
        datos["tamano"] = f"{p.stat().st_size / 1048576:.1f} MB"
    except OSError:
        pass
    if HAY_MUTAGEN:
        try:
            audio = MutagenFile(p)
            if audio is not None and audio.info is not None:
                seg = int(getattr(audio.info, "length", 0) or 0)
                if seg:
                    datos["duracion"] = f"{seg // 60}:{seg % 60:02d}"
                br = getattr(audio.info, "bitrate", 0) or 0
                if br:
                    datos["bitrate"] = f"{round(br / 1000)} kbps"
                sr = getattr(audio.info, "sample_rate", 0) or 0
                if sr:
                    datos["muestreo"] = f"{sr / 1000:g} kHz"
        except Exception:
            pass
    return datos


def escribir_replaygain(ruta: Path, pista: dict, album=None):
    """Escribe los tags ReplayGain en el formato que corresponda a cada archivo."""
    if not HAY_MUTAGEN:
        raise RuntimeError("falta mutagen (pip install mutagen)")
    ext = ruta.suffix.lower()

    pares = {"REPLAYGAIN_TRACK_GAIN": f"{pista['gain']:.2f} dB",
             "REPLAYGAIN_TRACK_PEAK": f"{pista['pico']:.6f}"}
    if album:
        pares["REPLAYGAIN_ALBUM_GAIN"] = f"{album['gain']:.2f} dB"
        pares["REPLAYGAIN_ALBUM_PEAK"] = f"{album['pico']:.6f}"

    if ext == ".mp3":
        audio = MP3(ruta, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        for clave, valor in pares.items():
            audio.tags.delall(f"TXXX:{clave}")
            audio.tags.add(TXXX(encoding=3, desc=clave, text=[valor]))
        audio.save(v2_version=3)
    elif ext in (".m4a", ".mp4"):
        audio = MP4(ruta)
        if audio.tags is None:
            audio.add_tags()
        for clave, valor in pares.items():
            audio.tags[f"----:com.apple.iTunes:{clave.lower()}"] = [
                MP4FreeForm(valor.encode("utf-8"), dataformat=AtomDataType.UTF8)]
        audio.save()
    elif ext == ".opus":
        # Opus usa otro estándar: ganancia en formato Q7.8 relativa a -23 LUFS
        def q78(lufs):
            return str(max(-32768, min(32767, round((REFERENCIA_R128 - lufs) * 256))))
        audio = OggOpus(ruta)
        audio["R128_TRACK_GAIN"] = [q78(pista["lufs"])]
        if album:
            audio["R128_ALBUM_GAIN"] = [q78(album["lufs"])]
        audio.save()
    else:
        raise ValueError(f"el formato {ext} no admite ReplayGain en este programa")


def quitar_numero_pista(ruta: Path):
    """Borra la etiqueta de número de pista del archivo ya terminado (p. ej. el '63').
    Se hace al final para que funcione venga de donde venga ese dato."""
    if not HAY_MUTAGEN:
        return
    ext = ruta.suffix.lower()
    if ext == ".mp3":
        try:
            tags = ID3(ruta)
        except ID3NoHeaderError:
            return
        if tags.getall("TRCK"):
            tags.delall("TRCK")
            tags.save(v2_version=3)
    elif ext in (".m4a", ".mp4"):
        audio = MP4(ruta)
        if audio.tags is not None and "trkn" in audio.tags:
            del audio.tags["trkn"]
            audio.save()
    elif ext == ".opus":
        audio = OggOpus(ruta)
        cambio = False
        for clave in ("tracknumber", "tracktotal", "totaltracks"):
            if clave in audio:
                del audio[clave]
                cambio = True
        if cambio:
            audio.save()


class ExtraerAudio(FFmpegExtractAudioPP):
    """FFmpegExtractAudio que puede obligar a recodificar.

    yt-dlp copia el audio sin tocarlo cuando el códec ya coincide (p. ej. AAC -> m4a,
    o Opus -> opus). Eso es ideal, salvo cuando se quiere normalizar: copiar y aplicar
    un filtro a la vez no es posible (con m4a la normalización se ignoraba en silencio
    y con opus FFmpeg daba error). Con forzar=True se recodifica siempre."""

    def __init__(self, downloader=None, forzar=False, **kw):
        super().__init__(downloader, **kw)
        self.forzar = forzar

    @classmethod
    def pp_key(cls):
        # Mismo nombre que el original: así siguen valiendo los argumentos
        # "extractaudio+ffmpeg_o" y los mensajes de progreso.
        return "ExtractAudio"

    def get_audio_codec(self, path):
        codec = super().get_audio_codec(path)
        # Un códec ficticio distinto del de destino hace que yt-dlp convierta
        return "forzar-recodificacion" if (self.forzar and codec) else codec


def debe_normalizar(cfg: dict) -> bool:
    return bool(cfg.get("normalizar")) and cfg.get("formato") != "original"


class MarcarListo(PostProcessor):
    """Se ejecuta al final (after_move): el archivo ya está tageado y en su sitio."""

    def __init__(self, downloader, emitir, replaygain, registro):
        super().__init__(downloader)
        self.emitir = emitir
        self.replaygain = replaygain
        self.registro = registro  # canciones analizadas (para el ReplayGain de álbum)

    def run(self, info):
        datos = etiquetas(info)
        ruta = info.get("filepath") or info.get("_filename") or ""
        datos["ruta"] = ruta
        if ruta:
            try:
                quitar_numero_pista(Path(ruta))
            except Exception as e:  # un fallo aquí no invalida la descarga
                self.report_warning(f"No se pudo quitar el número de pista: {e}")
        if self.replaygain and ruta:
            self.emitir(("estado", info["id"], "Calculando ReplayGain…"))
            try:
                vol = analizar_volumen(ruta, info.get("duration"))
                if vol is None:
                    raise RuntimeError("no se pudo medir el volumen")
                escribir_replaygain(Path(ruta), vol)
                self.registro.append({"id": info["id"], "ruta": Path(ruta), "datos": vol})
                datos["replaygain"] = texto_rg(vol)
            except Exception as e:  # un fallo aquí no invalida la descarga
                self.report_warning(f"ReplayGain: {e}")
        self.emitir(("listo", info["id"], datos))
        return [], info


def subcarpeta_relativa(destino: Path, carpeta: Path) -> str:
    try:
        rel = destino.relative_to(carpeta)
    except ValueError:
        return ""
    return "" if str(rel) == "." else str(rel)


def _entrada(e: dict) -> dict:
    url = str(e.get("webpage_url") or e.get("url") or "")
    if not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={e['id']}"
    miniatura = e.get("thumbnail") or ""
    if not miniatura and e.get("thumbnails"):
        miniatura = (e["thumbnails"][-1] or {}).get("url") or ""
    return {"id": e["id"], "titulo": e.get("track") or e.get("title") or e["id"],
            "url": url, "miniatura": miniatura}


def analizar_url(url: str, carpeta: Path, subcarpeta: str = ""):
    """Devuelve (carpeta_destino, lista_de_canciones, es_playlist). Si es playlist, usa subcarpeta.
    'subcarpeta' fuerza el destino (se usa al reintentar una canción que venía de una playlist)."""
    destino, entradas, es_playlist = (carpeta / subcarpeta if subcarpeta else carpeta), [], False
    try:
        opciones = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                    "ignoreerrors": True, "logger": _Silencio()}
        with YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(url, download=False)
        if info and info.get("_type") == "playlist":
            es_playlist = True
            if info.get("title") and not subcarpeta:
                destino = carpeta / sanitize_filename(info["title"])
            for e in info.get("entries") or []:
                if e and e.get("id") and e.get("_type") != "playlist" and e.get("ie_key") != "YoutubeTab":
                    entradas.append(_entrada(e))
        elif info and info.get("id"):
            entradas.append(_entrada(info))
    except Exception:
        pass
    return destino, entradas, es_playlist


def ids_en_archivo(carpeta: Path) -> set:
    try:
        lineas = (carpeta / "descargados.txt").read_text(encoding="utf-8").splitlines()
        return {l.split()[-1] for l in lineas if l.strip()}
    except OSError:
        return set()


def configurar_log(carpeta: Path) -> logging.Logger:
    log = logging.getLogger("descargador")
    log.setLevel(logging.DEBUG)
    for h in list(log.handlers):
        h.close()
        log.removeHandler(h)
    # Rota a los ~2 MB para que el log no crezca sin límite dentro de la carpeta de música
    handler = logging.handlers.RotatingFileHandler(
        carpeta / "descarga.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(handler)
    return log


ETAPAS = {
    "ThumbnailsConvertor": "Preparando portada…",
    "ExtractAudio": "Convirtiendo audio…",
    "Metadata": "Escribiendo tags…",
    "EmbedThumbnail": "Incrustando portada…",
}


def construir_opciones(cfg, carpeta, destino, logger, hook_progreso, hook_pp) -> dict:
    """Opciones de yt-dlp. Los postprocesadores se añaden aparte (agregar_postprocesadores)
    porque uno de ellos es una clase propia que no se puede declarar por nombre."""
    # La subcarpeta ya viene resuelta en 'destino'. No se pone "/" dentro de un
    # campo de la plantilla porque yt-dlp lo convierte en "⧹".
    plantilla = str(destino / "%(nombre_archivo)s.%(ext)s")

    ppa = {}
    if cfg["recorte"]:
        ppa["thumbnailsconvertor+ffmpeg_o"] = [
            "-c:v", "mjpeg",
            "-vf", "crop='if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'",
        ]
    if debe_normalizar(cfg):
        ppa["extractaudio+ffmpeg_o"] = ["-af", "loudnorm=I=-14:TP=-1:LRA=11"]

    return {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        # "pl_thumbnail" vacío = no guardar la imagen suelta de la playlist
        "outtmpl": {"default": plantilla, "pl_thumbnail": ""},
        "postprocessor_args": ppa,
        "writethumbnail": True,
        "noplaylist": False,
        "ignoreerrors": True,
        "download_archive": str(carpeta / "descargados.txt"),
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragments": 5,
        "geo_bypass": True,
        "noprogress": True,
        "logger": logger,
        "progress_hooks": [hook_progreso],
        "postprocessor_hooks": [hook_pp],
    }


def agregar_postprocesadores(ydl, cfg, destino, emitir, cancelar, registro):
    """Cadena de postprocesado, en orden: portada -> nombre/tags -> audio -> metadatos -> portada
    incrustada -> ReplayGain y aviso de 'listo'."""
    codec = "best" if cfg["formato"] == "original" else cfg["formato"]
    ydl.add_post_processor(FFmpegThumbnailsConvertorPP(ydl, format="jpg"), when="before_dl")
    ydl.add_post_processor(LimpiarMetadata(ydl, destino, emitir, cancelar), when="pre_process")
    ydl.add_post_processor(ExtraerAudio(ydl, forzar=debe_normalizar(cfg), preferredcodec=codec,
                                        preferredquality=cfg["calidad"]))
    ydl.add_post_processor(FFmpegMetadataPP(ydl, add_metadata=True))
    ydl.add_post_processor(EmbedThumbnailPP(ydl))
    ydl.add_post_processor(MarcarListo(ydl, emitir, cfg["replaygain"], registro), when="after_move")


def ejecutar_trabajo(url: str, cfg: dict, emitir, cancelar):
    """Descarga una URL (video o playlist) y va enviando eventos a la interfaz."""
    carpeta = Path(cfg["carpeta"])
    carpeta.mkdir(parents=True, exist_ok=True)
    log = configurar_log(carpeta)

    def al_fallar(id_video, motivo):
        emitir(("error", id_video, motivo))
        try:
            enlace = (f"https://www.youtube.com/watch?v={id_video}"
                      if id_video != "?" else "(desconocido)")
            with open(carpeta / "faltantes.txt", "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M}\nURL: {enlace}\nMotivo: {motivo}\n---\n")
        except OSError:
            pass

    logger = RegistroYtdlp(log, al_fallar)

    # 1) Mostrar de inmediato todas las canciones (amarillas, "en cola")
    destino, entradas, es_playlist = analizar_url(url, carpeta, cfg.get("subcarpeta") or "")
    destino.mkdir(parents=True, exist_ok=True)
    ya_estan = ids_en_archivo(carpeta)
    sub = subcarpeta_relativa(destino, carpeta)
    for e in entradas:
        emitir(("nuevo", e["id"], {"titulo": e["titulo"], "url": e["url"],
                                   "miniatura": e["miniatura"], "subcarpeta": sub}))
        if e["id"] in ya_estan:
            emitir(("omitida", e["id"]))

    # 2) Hooks de progreso (con límite para no saturar la interfaz)
    ultimo = {}

    def hook_progreso(d):
        if cancelar.is_set():
            raise DownloadCancelled("Cancelado por el usuario")
        id_video = (d.get("info_dict") or {}).get("id")
        if not id_video:
            return
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            texto = f"Descargando {d.get('downloaded_bytes', 0) * 100 / total:.0f}%" if total else "Descargando…"
            if ultimo.get(id_video) != texto:
                ultimo[id_video] = texto
                emitir(("estado", id_video, texto))
        elif d["status"] == "finished":
            emitir(("estado", id_video, "Procesando…"))

    def hook_pp(d):
        etapa = ETAPAS.get(d.get("postprocessor"))
        id_video = (d.get("info_dict") or {}).get("id")
        if d.get("status") == "started" and etapa and id_video:
            emitir(("estado", id_video, etapa))

    opciones = construir_opciones(cfg, carpeta, destino, logger, hook_progreso, hook_pp)
    registro = []
    with YoutubeDL(opciones) as ydl:
        agregar_postprocesadores(ydl, cfg, destino, emitir, cancelar, registro)
        ydl.download([url])

    # 3) Playlist/álbum: ahora que se conocen todas las pistas, se añade el ReplayGain de álbum
    if cfg["replaygain"] and es_playlist and len(registro) >= 2:
        album = calcular_album([r["datos"] for r in registro])
        for r in registro:
            try:
                escribir_replaygain(r["ruta"], r["datos"], album)
                emitir(("rg", r["id"], texto_rg(r["datos"], album)))
            except Exception as e:
                log.warning(f"ReplayGain de álbum falló en {r['ruta']}: {e}")


# ==============================================================================
# DETECCIÓN DE ENLACES (portapapeles y arrastrar/soltar)
# ==============================================================================
_RE_YOUTUBE = re.compile(
    r"^https?://(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?|playlist\?|shorts/|live/|browse/)|youtu\.be/[\w-])",
    re.IGNORECASE)


def es_enlace_youtube(texto: str) -> bool:
    """True si el texto es (solo) un enlace de YouTube o YouTube Music."""
    t = (texto or "").strip()
    return bool(t) and " " not in t and "\n" not in t and bool(_RE_YOUTUBE.match(t))


def extraer_enlaces(texto: str) -> list:
    """Enlaces de YouTube dentro de un texto cualquiera (p. ej. lo que llega al soltar
    algo en la ventana, que puede venir entre llaves o como lista de URLs). Sin repetidos."""
    vistos, enlaces = set(), []
    for crudo in re.findall(r"https?://[^\s{}<>\"']+", texto or ""):
        url = crudo.rstrip(".,;)")
        if es_enlace_youtube(url) and url not in vistos:
            vistos.add(url)
            enlaces.append(url)
    return enlaces


# ==============================================================================
# BÚSQUEDA POR NOMBRE ("artista canción")
# ==============================================================================
def _duracion_texto(seg) -> str:
    try:
        seg = int(seg)
    except (TypeError, ValueError):
        return ""
    h, resto = divmod(seg, 3600)
    return f"{h}:{resto // 60:02d}:{resto % 60:02d}" if h else f"{resto // 60}:{resto % 60:02d}"


def _resultado(e: dict) -> dict:
    datos = _entrada(e)
    datos["canal"] = re.sub(r"\s*-\s*Topic$", "", e.get("channel") or e.get("uploader") or "")
    datos["duracion"] = _duracion_texto(e.get("duration"))
    if not datos["miniatura"]:
        datos["miniatura"] = f"https://i.ytimg.com/vi/{e['id']}/mqdefault.jpg"
    return datos


def buscar(texto: str, cantidad: int = 10) -> list:
    """Busca en YouTube y devuelve una lista de resultados
    (id, titulo, url, miniatura, canal, duracion). Lanza la excepción si falla."""
    texto = (texto or "").strip()
    if not texto:
        return []
    opciones = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                "logger": _Silencio()}
    with YoutubeDL(opciones) as ydl:
        info = ydl.extract_info(f"ytsearch{int(cantidad)}:{texto}", download=False)
    return [_resultado(e) for e in (info or {}).get("entries") or []
            if e and e.get("id") and e.get("ie_key") in (None, "Youtube")]


# ==============================================================================
# ACTUALIZAR yt-dlp
# ==============================================================================
def version_ytdlp() -> str:
    from yt_dlp.version import __version__
    return __version__


def _como_tupla(version: str) -> tuple:
    return tuple(int(p) for p in re.findall(r"\d+", version or ""))


def es_mas_nueva(candidata: str, actual: str) -> bool:
    return _como_tupla(candidata) > _como_tupla(actual)


def ultima_version_ytdlp(timeout: int = 8) -> str:
    """Última versión publicada en PyPI. Lanza la excepción si no hay conexión."""
    req = urllib.request.Request("https://pypi.org/pypi/yt-dlp/json",
                                 headers={"User-Agent": "AUDYAX"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))["info"]["version"]


def puede_actualizarse() -> bool:
    """En el ejecutable de PyInstaller yt-dlp va empaquetado y pip no puede cambiarlo."""
    return not getattr(sys, "frozen", False)


def actualizar_ytdlp() -> tuple:
    """Actualiza yt-dlp con pip. Devuelve (ok, mensaje). Hay que reiniciar la app
    para que se use la versión nueva (la vieja ya está cargada en memoria)."""
    if not puede_actualizarse():
        return False, ("Esta es la versión empaquetada (.exe): yt-dlp va dentro del programa. "
                       "Descarga una versión nueva de AUDYAX para actualizarlo.")
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=600, **extra)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if r.returncode != 0:
        lineas = (r.stderr or r.stdout or "").strip().splitlines()
        return False, "\n".join(lineas[-6:]) or f"pip terminó con código {r.returncode}"
    return True, "yt-dlp se actualizó correctamente."


# ==============================================================================
# EDITOR DE ETIQUETAS (archivos ya descargados)
# ==============================================================================
CAMPOS_ETIQUETA = ("titulo", "artista", "album", "anio", "genero")
FORMATOS_EDITABLES = (".mp3", ".m4a", ".mp4", ".opus")

_ID3 = {"titulo": TIT2, "artista": TPE1, "album": TALB, "anio": TDRC, "genero": TCON} if HAY_MUTAGEN else {}
_MP4 = {"titulo": "\xa9nam", "artista": "\xa9ART", "album": "\xa9alb", "anio": "\xa9day", "genero": "\xa9gen"}
_VORBIS = {"titulo": "title", "artista": "artist", "album": "album", "anio": "date", "genero": "genre"}


def se_puede_editar(ruta) -> bool:
    return HAY_MUTAGEN and Path(ruta).suffix.lower() in FORMATOS_EDITABLES


def _primero(valor) -> str:
    if isinstance(valor, (list, tuple)):
        valor = valor[0] if valor else ""
    return str(valor or "")


def leer_etiquetas(ruta) -> dict:
    """Título, artista, álbum, año y género que tiene guardados el archivo."""
    ruta = Path(ruta)
    ext = ruta.suffix.lower()
    datos = dict.fromkeys(CAMPOS_ETIQUETA, "")
    if ext == ".mp3":
        try:
            tags = ID3(ruta)
        except ID3NoHeaderError:
            return datos
        for campo, clase in _ID3.items():
            marco = tags.get(clase.__name__)
            if marco is not None and marco.text:
                datos[campo] = str(marco.text[0])
    elif ext in (".m4a", ".mp4"):
        tags = MP4(ruta).tags or {}
        for campo, clave in _MP4.items():
            datos[campo] = _primero(tags.get(clave))
    elif ext == ".opus":
        audio = OggOpus(ruta)
        for campo, clave in _VORBIS.items():
            datos[campo] = _primero(audio.get(clave))
    else:
        raise ValueError(f"el formato {ext} no se puede editar")
    datos["anio"] = datos["anio"][:4] if re.match(r"\d{4}", datos["anio"]) else datos["anio"]
    return datos


def leer_portada(ruta):
    """Bytes de la portada incrustada, o None si no tiene."""
    ruta = Path(ruta)
    ext = ruta.suffix.lower()
    try:
        if ext == ".mp3":
            fotos = ID3(ruta).getall("APIC")
            return bytes(fotos[0].data) if fotos else None
        if ext in (".m4a", ".mp4"):
            portadas = (MP4(ruta).tags or {}).get("covr")
            return bytes(portadas[0]) if portadas else None
        if ext == ".opus":
            bloques = OggOpus(ruta).get("metadata_block_picture")
            return Picture(base64.b64decode(bloques[0])).data if bloques else None
    except Exception:
        return None
    return None


def escribir_etiquetas(ruta, datos: dict, portada: bytes = None):
    """Guarda los campos indicados (un texto vacío borra la etiqueta) y, si se pasa,
    reemplaza la portada (bytes JPEG). No toca el audio ni el ReplayGain."""
    if not HAY_MUTAGEN:
        raise RuntimeError("falta mutagen (pip install mutagen)")
    ruta = Path(ruta)
    ext = ruta.suffix.lower()
    campos = {k: (str(v).strip() if v is not None else "") for k, v in datos.items()
              if k in CAMPOS_ETIQUETA}

    if ext == ".mp3":
        audio = MP3(ruta, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        for campo, valor in campos.items():
            clase = _ID3[campo]
            audio.tags.delall(clase.__name__)
            if valor:
                audio.tags.add(clase(encoding=3, text=[valor]))
        if portada:
            audio.tags.delall("APIC")
            audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=portada))
        audio.save(v2_version=3)
    elif ext in (".m4a", ".mp4"):
        audio = MP4(ruta)
        if audio.tags is None:
            audio.add_tags()
        for campo, valor in campos.items():
            clave = _MP4[campo]
            if valor:
                audio.tags[clave] = [valor]
            elif clave in audio.tags:
                del audio.tags[clave]
        if portada:
            audio.tags["covr"] = [MP4Cover(portada, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
    elif ext == ".opus":
        audio = OggOpus(ruta)
        for campo, valor in campos.items():
            clave = _VORBIS[campo]
            if valor:
                audio[clave] = [valor]
            elif clave in audio:
                del audio[clave]
        if portada:
            foto = Picture()
            foto.type, foto.mime, foto.desc, foto.data = 3, "image/jpeg", "Cover", portada
            audio["metadata_block_picture"] = [base64.b64encode(foto.write()).decode("ascii")]
        audio.save()
    else:
        raise ValueError(f"el formato {ext} no se puede editar")


# ==============================================================================
# CONFIGURACIÓN GUARDADA
# ==============================================================================
def cargar_config() -> dict:
    cfg = dict(CONFIG_POR_DEFECTO)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def guardar_config(cfg: dict):
    # Solo se guardan las preferencias conocidas (no datos de un trabajo concreto)
    datos = {k: cfg[k] for k in CONFIG_POR_DEFECTO if k in cfg}
    try:
        CONFIG_PATH.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    except OSError:
        pass