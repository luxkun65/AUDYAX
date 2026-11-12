"""
Descargador de música de YouTube / YouTube Music - con interfaz gráfica.

Requisitos:
    pip install -U yt-dlp mutagen
    ffmpeg instalado y en el PATH
    (tkinter viene con Python; en Linux: sudo apt install python3-tk)

Uso:  python descargador_gui.py
"""
import json
import logging
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import sanitize_filename

try:
    from mutagen.id3 import ID3, TXXX
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4, AtomDataType, MP4FreeForm
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
DIR_POR_DEFECTO = (
    "B:/Music/Prueba_de_Python" if Path("B:/").exists()
    else str(Path.home() / "Music" / "Descargas")
)
CONFIG_POR_DEFECTO = {
    "carpeta": DIR_POR_DEFECTO,
    "formato": "mp3",
    "calidad": "192",
    "normalizar": False,
    "recorte": True,
    "replaygain": True,
}

# Los tres estados de cada canción y su color
COLORES = {"verde": "#2ecc71", "amarillo": "#f1c40f", "rojo": "#e74c3c"}


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
        "url": info.get("webpage_url") or info.get("original_url") or "",
    }


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
        if not info.get("album"):
            info["album"] = info.get("playlist_title") or info["title"]

        nombre = sanitize_filename(info["title"])
        if self.destino.exists() and any(f.stem == nombre for f in self.destino.iterdir()):
            nombre = f"{nombre} ({sanitize_filename(info['artist'])})"
        info["nombre_archivo"] = nombre

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


def _entrada(e: dict) -> dict:
    url = str(e.get("webpage_url") or e.get("url") or "")
    if not url.startswith("http"):
        url = f"https://www.youtube.com/watch?v={e['id']}"
    return {"id": e["id"], "titulo": e.get("track") or e.get("title") or e["id"], "url": url}


def analizar_url(url: str, carpeta: Path):
    """Devuelve (carpeta_destino, lista_de_canciones, es_playlist). Si es playlist, usa subcarpeta."""
    destino, entradas, es_playlist = carpeta, [], False
    try:
        opciones = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist",
                    "ignoreerrors": True, "logger": _Silencio()}
        with YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(url, download=False)
        if info and info.get("_type") == "playlist":
            es_playlist = True
            if info.get("title"):
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
    handler = logging.FileHandler(carpeta / "descarga.log", encoding="utf-8")
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
    # La subcarpeta ya viene resuelta en 'destino'. No se pone "/" dentro de un
    # campo de la plantilla porque yt-dlp lo convierte en "⧹".
    plantilla = str(destino / "%(nombre_archivo)s.%(ext)s")
    codec = "best" if cfg["formato"] == "original" else cfg["formato"]

    ppa = {}
    if cfg["recorte"]:
        ppa["thumbnailsconvertor+ffmpeg_o"] = [
            "-c:v", "mjpeg",
            "-vf", "crop='if(gt(ih,iw),iw,ih)':'if(gt(iw,ih),ih,iw)'",
        ]
    if cfg["normalizar"] and cfg["formato"] != "original":
        ppa["extractaudio+ffmpeg_o"] = ["-af", "loudnorm=I=-14:TP=-1:LRA=11"]

    return {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        # "pl_thumbnail" vacío = no guardar la imagen suelta de la playlist
        "outtmpl": {"default": plantilla, "pl_thumbnail": ""},
        "postprocessors": [
            {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
            {"key": "FFmpegExtractAudio", "preferredcodec": codec, "preferredquality": cfg["calidad"]},
            {"key": "FFmpegMetadata", "add_metadata": True},
            {"key": "EmbedThumbnail"},
        ],
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
    destino, entradas, es_playlist = analizar_url(url, carpeta)
    destino.mkdir(parents=True, exist_ok=True)
    ya_estan = ids_en_archivo(carpeta)
    for e in entradas:
        emitir(("nuevo", e["id"], {"titulo": e["titulo"], "url": e["url"]}))
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
        ydl.add_post_processor(LimpiarMetadata(ydl, destino, emitir, cancelar), when="pre_process")
        ydl.add_post_processor(MarcarListo(ydl, emitir, cfg["replaygain"], registro), when="after_move")
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
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


# ==============================================================================
# INTERFAZ
# ==============================================================================
def limpiar_tk(texto: str) -> str:
    """Tk antiguo no soporta algunos emojis; se reemplazan para evitar errores."""
    return "".join(c if ord(c) <= 0xFFFF else "□" for c in str(texto))


def crear_punto(color: str, tam: int = 14) -> tk.PhotoImage:
    img = tk.PhotoImage(width=tam, height=tam)
    r = tam / 2 - 1
    c = tam / 2
    for x in range(tam):
        for y in range(tam):
            if (x + 0.5 - c) ** 2 + (y + 0.5 - c) ** 2 <= r * r:
                img.put(color, (x, y))
    return img


def abrir_ruta(ruta):
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(ruta))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(ruta)])
        else:
            subprocess.Popen(["xdg-open", str(ruta)])
    except Exception as e:
        messagebox.showerror("No se pudo abrir", str(e))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Descargador de Música")
        self.geometry("1100x700")
        self.minsize(860, 560)

        self.cfg = cargar_config()
        self.eventos = queue.Queue()   # worker -> interfaz
        self.trabajos = queue.Queue()  # interfaz -> worker
        self.cancelar = threading.Event()
        self.hilo = None
        self.pendientes = 0
        self.errores_generales = 0
        self.filas = {}  # id -> datos de la canción
        self.puntos = {nombre: crear_punto(c) for nombre, c in COLORES.items()}

        self._construir()
        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.after(100, self._procesar_eventos)
        self.after(400, self._revisar_ffmpeg)

    # ---------------------------------------------------------------- construcción
    def _construir(self):
        estilo = ttk.Style(self)
        estilo.configure("Treeview", rowheight=26)

        self.var_url = tk.StringVar()
        self.var_carpeta = tk.StringVar(value=self.cfg["carpeta"])
        self.var_formato = tk.StringVar(value=self.cfg["formato"])
        self.var_calidad = tk.StringVar(value=self.cfg["calidad"])
        self.var_norm = tk.BooleanVar(value=self.cfg["normalizar"])
        self.var_recorte = tk.BooleanVar(value=self.cfg["recorte"])
        self.var_rg = tk.BooleanVar(value=self.cfg["replaygain"] and HAY_MUTAGEN)

        sup = ttk.Frame(self, padding=(12, 12, 12, 4))
        sup.pack(fill="x")
        sup.columnconfigure(1, weight=1)

        ttk.Label(sup, text="Enlace:").grid(row=0, column=0, sticky="w")
        entrada = ttk.Entry(sup, textvariable=self.var_url)
        entrada.grid(row=0, column=1, sticky="ew", padx=8, pady=3)
        entrada.bind("<Return>", lambda e: self._descargar())
        entrada.focus_set()
        ttk.Button(sup, text="Pegar", command=self._pegar).grid(row=0, column=2, padx=2)
        ttk.Button(sup, text="Descargar", command=self._descargar).grid(row=0, column=3, padx=2)

        ttk.Label(sup, text="Guardar en:").grid(row=1, column=0, sticky="w")
        ttk.Entry(sup, textvariable=self.var_carpeta).grid(row=1, column=1, sticky="ew", padx=8, pady=3)
        ttk.Button(sup, text="Cambiar…", command=self._elegir_carpeta).grid(row=1, column=2, padx=2)
        ttk.Button(sup, text="Abrir", command=self._abrir_carpeta_base).grid(row=1, column=3, padx=2)

        opc = ttk.Frame(self, padding=(12, 0, 12, 6))
        opc.pack(fill="x")
        ttk.Label(opc, text="Formato:").pack(side="left")
        self.cb_formato = ttk.Combobox(opc, textvariable=self.var_formato, width=9, state="readonly",
                                       values=["mp3", "m4a", "opus", "original"])
        self.cb_formato.pack(side="left", padx=(4, 12))
        self.cb_formato.bind("<<ComboboxSelected>>", lambda e: self._al_cambiar_formato())
        ttk.Label(opc, text="Calidad (kbps):").pack(side="left")
        self.cb_calidad = ttk.Combobox(opc, textvariable=self.var_calidad, width=6, state="readonly",
                                       values=["128", "192", "256", "320"])
        self.cb_calidad.pack(side="left", padx=(4, 12))
        chk_rg = ttk.Checkbutton(opc, text="ReplayGain (recomendado)", variable=self.var_rg)
        chk_rg.pack(side="left", padx=(0, 12))
        if not HAY_MUTAGEN:
            chk_rg.configure(state="disabled")
        self.chk_norm = ttk.Checkbutton(opc, text="Normalizar volumen (modifica el audio)", variable=self.var_norm)
        self.chk_norm.pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opc, text="Portada cuadrada", variable=self.var_recorte).pack(side="left")
        self.btn_cancelar = ttk.Button(opc, text="Cancelar todo", command=self._cancelar, state="disabled")
        self.btn_cancelar.pack(side="right")
        self._al_cambiar_formato()

        # Lista
        marco = ttk.Frame(self, padding=(12, 0))
        marco.pack(fill="both", expand=True)
        cols = ("estado", "titulo", "artista", "album", "anio", "enlace")
        self.tree = ttk.Treeview(marco, columns=cols, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=46, stretch=False, anchor="center")
        for col, texto, ancho in [("estado", "Estado", 150), ("titulo", "Título", 230),
                                  ("artista", "Artista", 160), ("album", "Álbum", 200),
                                  ("anio", "Año", 55), ("enlace", "Enlace", 260)]:
            self.tree.heading(col, text=texto)
            self.tree.column(col, width=ancho, minwidth=50, stretch=(col != "anio"))
        sy = ttk.Scrollbar(marco, orient="vertical", command=self.tree.yview)
        sx = ttk.Scrollbar(marco, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._mostrar_detalle())
        self.tree.bind("<Double-1>", self._doble_clic)

        # Detalle
        det = ttk.LabelFrame(self, text="Detalle de la canción seleccionada", padding=8)
        det.pack(fill="x", padx=12, pady=8)
        self.txt = tk.Text(det, height=8, wrap="word", font="TkFixedFont", relief="flat",
                           background=self.cget("background"))
        self.txt.pack(fill="x")
        self.txt.configure(state="disabled")
        botones = ttk.Frame(det)
        botones.pack(fill="x", pady=(6, 0))
        for texto, cmd in [("Copiar enlace", self._copiar_enlace), ("Abrir enlace", self._abrir_enlace),
                           ("Abrir carpeta", self._abrir_carpeta_cancion), ("Reintentar", self._reintentar)]:
            ttk.Button(botones, text=texto, command=cmd).pack(side="left", padx=(0, 6))
        ttk.Button(botones, text="Limpiar lista", command=self._limpiar_lista).pack(side="right")

        # Barra de estado: contadores por color
        barra = ttk.Frame(self, padding=(12, 0, 12, 8))
        barra.pack(fill="x")
        self.contadores = {}
        for nombre, etiqueta in [("verde", "descargadas"), ("amarillo", "en proceso"), ("rojo", "con error")]:
            lbl = ttk.Label(barra, image=self.puntos[nombre], compound="left", text=f" 0 {etiqueta}")
            lbl.pack(side="left", padx=(0, 18))
            self.contadores[nombre] = (lbl, etiqueta)

    # ---------------------------------------------------------------- acciones
    def _al_cambiar_formato(self):
        estado = "disabled" if self.var_formato.get() == "original" else "readonly"
        self.cb_calidad.configure(state=estado)
        self.chk_norm.configure(state="disabled" if estado == "disabled" else "normal")

    def _pegar(self):
        try:
            self.var_url.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def _elegir_carpeta(self):
        nueva = filedialog.askdirectory(initialdir=self.var_carpeta.get() or None,
                                        title="Elige la carpeta donde guardar")
        if nueva:
            self.var_carpeta.set(os.path.normpath(nueva))

    def _cfg_actual(self) -> dict:
        return {
            "carpeta": self.var_carpeta.get().strip(),
            "formato": self.var_formato.get(),
            "calidad": self.var_calidad.get(),
            "normalizar": bool(self.var_norm.get()),
            "recorte": bool(self.var_recorte.get()),
            "replaygain": bool(self.var_rg.get()) and HAY_MUTAGEN,
        }

    def _descargar(self):
        url = self.var_url.get().strip()
        if not url.startswith(("http://", "https://")):
            messagebox.showwarning("Enlace no válido", "Pega un enlace de YouTube o YouTube Music.")
            return
        if self._encolar(url):
            self.var_url.set("")

    def _encolar(self, url: str) -> bool:
        cfg = self._cfg_actual()
        if not cfg["carpeta"]:
            messagebox.showwarning("Falta la carpeta", "Elige dónde guardar las canciones.")
            return False
        try:
            Path(cfg["carpeta"]).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Carpeta no válida", str(e))
            return False
        guardar_config(cfg)
        self.pendientes += 1
        self.trabajos.put((url, cfg))
        if self.hilo is None or not self.hilo.is_alive():
            self.hilo = threading.Thread(target=self._hilo_trabajo, daemon=True)
            self.hilo.start()
        self._actualizar_botones()
        return True

    def _hilo_trabajo(self):
        while True:
            url, cfg = self.trabajos.get()
            self.cancelar.clear()
            try:
                ejecutar_trabajo(url, cfg, self.eventos.put, self.cancelar)
            except DownloadCancelled:
                self.eventos.put(("cancelado",))
            except Exception as e:
                self.eventos.put(("error", "?", str(e)))
            finally:
                self.eventos.put(("fin",))

    def _cancelar(self):
        self.cancelar.set()
        try:  # descarta los trabajos que aún no empezaron
            while True:
                self.trabajos.get_nowait()
                self.pendientes -= 1
        except queue.Empty:
            pass
        self._actualizar_botones()

    def _actualizar_botones(self):
        self.btn_cancelar.configure(state="normal" if self.pendientes > 0 else "disabled")

    def _revisar_ffmpeg(self):
        if not shutil.which("ffmpeg"):
            messagebox.showwarning(
                "Falta FFmpeg",
                "No encuentro FFmpeg en tu equipo. Sin él no se puede convertir el audio, "
                "añadir las portadas ni calcular el ReplayGain.\n\nInstálalo y añádelo al PATH.")
        if not HAY_MUTAGEN:
            messagebox.showwarning(
                "Falta mutagen",
                "Para escribir el ReplayGain hace falta la librería mutagen.\n\n"
                "Instálala con:  pip install mutagen")

    # ---------------------------------------------------------------- lista
    def _fila(self, vid: str, **campos):
        nueva = vid not in self.filas
        if nueva:
            self.filas[vid] = {"color": "amarillo", "texto": "En cola", "titulo": vid, "artista": "",
                               "album": "", "anio": "", "url": "", "ruta": "", "motivo": "",
                               "replaygain": ""}
        self.filas[vid].update({k: v for k, v in campos.items() if v is not None})
        f = self.filas[vid]
        valores = [limpiar_tk(x) for x in (f["texto"], f["titulo"], f["artista"], f["album"], f["anio"], f["url"])]
        if nueva:
            self.tree.insert("", "end", iid=vid, image=self.puntos[f["color"]], values=valores)
            self.tree.see(vid)
        else:
            self.tree.item(vid, image=self.puntos[f["color"]], values=valores)
        if self.tree.selection() == (vid,):
            self._mostrar_detalle()
        self._actualizar_contadores()

    def _actualizar_contadores(self):
        cuenta = {"verde": 0, "amarillo": 0, "rojo": 0}
        for f in self.filas.values():
            cuenta[f["color"]] += 1
        for nombre, (lbl, etiqueta) in self.contadores.items():
            lbl.configure(text=f" {cuenta[nombre]} {etiqueta}")

    def _procesar_eventos(self):
        try:
            while True:
                self._manejar(self.eventos.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._procesar_eventos)

    def _manejar(self, ev):
        tipo = ev[0]
        if tipo == "nuevo":
            self._fila(ev[1], color="amarillo", texto="En cola", motivo="", ruta="", **ev[2])
        elif tipo == "info":
            self._fila(ev[1], **ev[2])
        elif tipo == "estado":
            if self.filas.get(ev[1], {}).get("color") != "rojo":
                self._fila(ev[1], color="amarillo", texto=ev[2])
        elif tipo == "listo":
            self._fila(ev[1], color="verde", texto="Descargada", motivo="", **ev[2])
        elif tipo == "rg":
            self._fila(ev[1], replaygain=ev[2])
        elif tipo == "omitida":
            self._fila(ev[1], color="verde", texto="Ya descargada antes")
        elif tipo == "error":
            vid, motivo = ev[1], ev[2]
            if vid == "?":
                self.errores_generales += 1
                vid = f"error-{self.errores_generales}"
                self._fila(vid, titulo="Error general", color="rojo", texto="Error", motivo=motivo)
            else:
                self._fila(vid, color="rojo", texto="Error: " + motivo[:60], motivo=motivo)
        elif tipo == "cancelado":
            for vid, f in list(self.filas.items()):
                if f["color"] == "amarillo":
                    self._fila(vid, color="rojo", texto="Cancelada", motivo="Cancelada por el usuario")
        elif tipo == "fin":
            self.pendientes = max(0, self.pendientes - 1)
            self._actualizar_botones()

    def _limpiar_lista(self):
        for vid, f in list(self.filas.items()):
            if f["color"] != "amarillo":
                self.tree.delete(vid)
                del self.filas[vid]
        self._mostrar_detalle()
        self._actualizar_contadores()

    # ---------------------------------------------------------------- detalle
    def _seleccion(self):
        sel = self.tree.selection()
        return sel[0] if sel and sel[0] in self.filas else None

    def _mostrar_detalle(self):
        vid = self._seleccion()
        lineas = []
        if vid:
            f = self.filas[vid]
            estado = {"verde": "Descargada", "amarillo": "En proceso", "rojo": "Error"}[f["color"]]
            lineas = [f"Estado:   {estado} ({f['texto']})", f"Título:   {f['titulo']}",
                      f"Artista:  {f['artista']}", f"Álbum:    {f['album']}", f"Año:      {f['anio']}",
                      f"Enlace:   {f['url']}", f"Archivo:  {f['ruta']}"]
            if f["replaygain"]:
                lineas.append(f"Volumen:  ReplayGain {f['replaygain']}")
            if f["motivo"]:
                lineas.append(f"Motivo:   {f['motivo']}")
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", limpiar_tk("\n".join(lineas)) if lineas else "Selecciona una canción de la lista.")
        self.txt.configure(state="disabled")

    def _doble_clic(self, evento):
        vid = self.tree.identify_row(evento.y)
        if vid in self.filas and self.filas[vid]["url"]:
            webbrowser.open(self.filas[vid]["url"])

    def _copiar_enlace(self):
        vid = self._seleccion()
        if vid and self.filas[vid]["url"]:
            self.clipboard_clear()
            self.clipboard_append(self.filas[vid]["url"])

    def _abrir_enlace(self):
        vid = self._seleccion()
        if vid and self.filas[vid]["url"]:
            webbrowser.open(self.filas[vid]["url"])

    def _abrir_carpeta_cancion(self):
        vid = self._seleccion()
        ruta = self.filas[vid]["ruta"] if vid else ""
        if ruta and Path(ruta).exists():
            abrir_ruta(Path(ruta).parent)
        else:
            self._abrir_carpeta_base()

    def _abrir_carpeta_base(self):
        carpeta = Path(self.var_carpeta.get())
        if carpeta.exists():
            abrir_ruta(carpeta)
        else:
            messagebox.showinfo("Carpeta", "Esa carpeta todavía no existe.")

    def _reintentar(self):
        vid = self._seleccion()
        if not vid:
            return
        f = self.filas[vid]
        if f["color"] != "rojo" or not f["url"]:
            messagebox.showinfo("Reintentar", "Solo se pueden reintentar canciones con error que tengan enlace.")
            return
        self._encolar(f["url"])

    # ---------------------------------------------------------------- cierre
    def _cerrar(self):
        guardar_config(self._cfg_actual())
        self.cancelar.set()
        self.destroy()


def main():
    if sys.platform.startswith("win"):
        try:  # texto nítido en pantallas de alta resolución
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()