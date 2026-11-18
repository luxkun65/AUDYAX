"""
AUDYAX - descargador de música de YouTube / YouTube Music (interfaz gráfica: customtkinter + Pillow).

Este archivo solo contiene la ventana. Todo el trabajo de descarga está en
motor_descarga.py, que debe estar en la misma carpeta.

Requisitos:
    pip install -U yt-dlp mutagen customtkinter pillow tkinterdnd2
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
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import font as tkfont

import customtkinter as ctk
from PIL import Image, ImageDraw

from desktop import (
    Tray,
    tray_available,
    no_tray_reason,
    notifications_available,
    notify,
    batch_summary,
)
import player
import watched
from engine import (
    DEFAULT_CONFIG,
    HAS_MUTAGEN,
    MAX_PARALLEL,
    COOKIE_BROWSERS,
    VALID_PROXY,
    VERSION,
    DownloadCancelled,
    update_ytdlp,
    search,
    load_config,
    parallel_downloads,
    run_job,
    network_options,
    is_youtube_link,
    is_newer,
    write_tags,
    export_csv,
    export_m3u,
    extract_links,
    save_config,
    file_info,
    read_tags,
    read_cover,
    valid_filename,
    setup_bundled_ffmpeg,
    can_self_update,
    rename_file,
    is_editable,
    latest_app_version,
    latest_ytdlp_version,
    ytdlp_version,
)

# Arrastrar y soltar: opcional (pip install tkinterdnd2). Sin él la app funciona igual.
try:
    from tkinterdnd2 import DND_TEXT, TkinterDnD
    _DND_BASES = (TkinterDnD.DnDWrapper,)
except ImportError:
    DND_TEXT, TkinterDnD, _DND_BASES = None, None, ()

# ==============================================================================
# PALETA (cada color es una tupla: (modo claro, modo oscuro))
# ==============================================================================
C = {
    "bg": ("#f4f1fa", "#0f0a1a"), "sidebar": ("#e9e3f5", "#0b0714"),
    "card": ("#ffffff", "#1a1228"), "card2": ("#ece6f7", "#271a3d"),
    "input": ("#f7f4fc", "#130d20"), "border": ("#d9cfee", "#33264d"),
    "row_a": ("#ffffff", "#160f24"), "row_b": ("#f7f4fc", "#1c132d"),
    "sel": ("#e0d3fa", "#5b34a8"), "nav_active": ("#e0d3fa", "#3b2268"),
    "accent": ("#7c3aed", "#a78bfa"), "accent_hover": ("#6d28d9", "#c4b5fd"),
    "text": ("#1e1530", "#efeaf8"), "muted": ("#6e6285", "#a095bd"),
    "on_accent": ("#ffffff", "#1a0b3b"), "hover": ("#e4dbf5", "#35264f"),
    "red": ("#d63c2c", "#e74c3c"),
}
REPO_URL = "https://github.com/luxkun65/AUDYAX"

# Opciones de "Organizar descargas" (Configuración): clave guardada -> texto y ejemplo
ORGANIZE_OPTIONS = {
    "flat": ("Todo junto (sin carpetas)", "Descargas/One More Time.mp3"),
    "artist": ("Una carpeta por artista", "Descargas/Daft Punk/One More Time.mp3"),
    "artist_album": ("Artista / Álbum", "Descargas/Daft Punk/Discovery/One More Time.mp3"),
}
STATUS_COLORS = {"green": "#2ecc71", "yellow": "#f1c40f", "red": "#e74c3c"}

# Filtro por estado de la lista: texto del menú -> color de la fila (None = todas)
STATUS_FILTERS = {"Todas": None, "Descargadas": "green", "En proceso": "yellow", "Con error": "red"}
STATUS_ORDER = {"red": 0, "yellow": 1, "green": 2}  # al ordenar por estado, errores primero

# Ancho de cada columna de la lista (0 = ocupa el espacio sobrante)
COLUMN_WIDTHS = [34, 150, 52, 0, 170, 170, 60, 250]


# ==============================================================================
# UTILIDADES GRÁFICAS (Pillow)
# ==============================================================================
def tk_safe(text) -> str:
    """Tk antiguo no soporta algunos emojis; se reemplazan para evitar errores."""
    return "".join(c if ord(c) <= 0xFFFF else "□" for c in str(text))


def shorten(text, n: int) -> str:
    t = tk_safe(text)
    return t if len(t) <= n else t[: n - 1] + "…"


def _draw_icon(name: str, color: str, size: int) -> Image.Image:
    """Dibuja un icono sencillo con líneas, a 4x de tamaño para que quede suave."""
    S = 4
    n = size * S
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = max(2, round(n * 0.09))

    def pt(x, y):
        return (x * n, y * n)

    def line(points, width=w, c=color):
        d.line([pt(x, y) for x, y in points], fill=c, width=width, joint="curve")

    def box(x0, y0, x1, y1):
        return (x0 * n, y0 * n, x1 * n, y1 * n)

    if name == "home":
        d.polygon([pt(.5, .1), pt(.95, .52), pt(.8, .52), pt(.8, .9), pt(.6, .9), pt(.6, .62),
                   pt(.4, .62), pt(.4, .9), pt(.2, .9), pt(.2, .52), pt(.05, .52)], fill=color)
    elif name == "gear":
        c = n / 2
        for k in range(8):
            a = math.radians(k * 45)
            d.line([(c + math.cos(a) * n * .28, c + math.sin(a) * n * .28),
                    (c + math.cos(a) * n * .44, c + math.sin(a) * n * .44)],
                   fill=color, width=int(n * .16))
        d.ellipse(box(.2, .2, .8, .8), fill=color)
        d.ellipse(box(.38, .38, .62, .62), fill=(0, 0, 0, 0))
    elif name == "info":
        d.ellipse(box(.08, .08, .92, .92), outline=color, width=w)
        d.ellipse(box(.45, .24, .55, .34), fill=color)
        line([(.5, .45), (.5, .72)], w + 1)
    elif name == "link":
        d.rounded_rectangle(box(.04, .32, .58, .68), radius=n * .18, outline=color, width=w)
        d.rounded_rectangle(box(.42, .32, .96, .68), radius=n * .18, outline=color, width=w)
    elif name == "folder":
        line([(.06, .24), (.38, .24), (.48, .34), (.94, .34), (.94, .84), (.06, .84), (.06, .24)])
    elif name == "download":
        line([(.5, .1), (.5, .62)])
        line([(.28, .42), (.5, .64), (.72, .42)])
        line([(.14, .72), (.14, .88), (.86, .88), (.86, .72)])
    elif name == "cancel":
        d.ellipse(box(.1, .1, .9, .9), outline=color, width=w)
        line([(.22, .78), (.78, .22)])
    elif name == "copy":
        d.rounded_rectangle(box(.3, .3, .9, .92), radius=n * .08, outline=color, width=w)
        line([(.18, .68), (.18, .1), (.62, .1)])
    elif name == "external":
        d.rounded_rectangle(box(.08, .28, .72, .92), radius=n * .08, outline=color, width=w)
        line([(.45, .55), (.9, .1)])
        line([(.6, .1), (.9, .1), (.9, .4)])
    elif name == "refresh":
        d.arc(box(.14, .14, .86, .86), start=60, end=350, fill=color, width=w)
        d.polygon([pt(.66, .42), pt(1.0, .42), pt(.83, .7)], fill=color)
    elif name == "trash":
        line([(.12, .24), (.88, .24)])
        line([(.36, .24), (.36, .1), (.64, .1), (.64, .24)])
        line([(.24, .34), (.30, .9), (.70, .9), (.76, .34)])
        line([(.42, .45), (.42, .78)])
        line([(.58, .45), (.58, .78)])
    elif name == "search":
        d.ellipse(box(.1, .1, .64, .64), outline=color, width=w)
        line([(.58, .58), (.9, .9)], w + 1)
    elif name == "edit":
        d.polygon([pt(.18, .66), pt(.66, .18), pt(.82, .34), pt(.34, .82)], outline=color, width=w)
        d.polygon([pt(.12, .88), pt(.18, .66), pt(.34, .82)], fill=color)
    elif name == "pause":
        d.rounded_rectangle(box(.2, .14, .4, .86), radius=n * .04, fill=color)
        d.rounded_rectangle(box(.6, .14, .8, .86), radius=n * .04, fill=color)
    elif name == "play":
        d.polygon([pt(.26, .12), pt(.86, .5), pt(.26, .88)], fill=color)
    elif name == "export":
        line([(.5, .62), (.5, .1)])
        line([(.28, .32), (.5, .1), (.72, .32)])
        line([(.14, .6), (.14, .88), (.86, .88), (.86, .6)])
    elif name == "note":
        d.ellipse(box(.08, .62, .42, .92), fill=color)
        d.ellipse(box(.56, .52, .9, .82), fill=color)
        d.polygon([pt(.36, .76), pt(.36, .18), pt(.84, .08), pt(.84, .66), pt(.76, .66),
                   pt(.76, .3), pt(.44, .37), pt(.44, .76)], fill=color)
    elif name in ("ok", "clock", "err"):
        base = {"ok": STATUS_COLORS["green"], "clock": STATUS_COLORS["yellow"], "err": STATUS_COLORS["red"]}[name]
        d.ellipse(box(.04, .04, .96, .96), fill=base)
        if name == "ok":
            line([(.28, .52), (.44, .68), (.74, .34)], w + 1, "#ffffff")
        elif name == "clock":
            line([(.5, .24), (.5, .5), (.7, .62)], w, "#5a4500")
        else:
            line([(.33, .33), (.67, .67)], w + 1, "#ffffff")
            line([(.67, .33), (.33, .67)], w + 1, "#ffffff")
    elif name == "dot":
        d.ellipse(box(.05, .05, .95, .95), fill=color)
    elif name == "eye":
        d.chord(box(.04, .22, .96, 1.18), start=200, end=340, outline=color, width=w)
        d.chord(box(.04, -.18, .96, .78), start=20, end=160, outline=color, width=w)
        d.ellipse(box(.36, .36, .64, .64), fill=color)
    return im.resize((size, size), Image.LANCZOS)


def icon(name: str, size: int = 18, color=None) -> ctk.CTkImage:
    """Icono que cambia solo de color entre modo claro y oscuro."""
    color = color or C["text"]
    if isinstance(color, str):
        color = (color, color)
    return ctk.CTkImage(light_image=_draw_icon(name, color[0], size * 2),
                        dark_image=_draw_icon(name, color[1], size * 2), size=(size, size))


def rounded_thumbnail(img: Image.Image, side_px: int, radius: int) -> Image.Image:
    """Recorta al centro en cuadrado y redondea las esquinas."""
    S = 3
    img = img.convert("RGB")
    w, h = img.size
    m = min(w, h)
    x0, y0 = (w - m) // 2, (h - m) // 2
    img = img.crop((x0, y0, x0 + m, y0 + m)).resize((side_px * S, side_px * S), Image.LANCZOS)
    mask = Image.new("L", (side_px * S, side_px * S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, side_px * S - 1, side_px * S - 1),
                                              radius=radius * S, fill=255)
    img = img.convert("RGBA")
    img.putalpha(mask)
    return img.resize((side_px, side_px), Image.LANCZOS)


def square_image(img: Image.Image, side_px: int, radius: int) -> ctk.CTkImage:
    p = rounded_thumbnail(img, side_px * 2, radius * 2)
    return ctk.CTkImage(light_image=p, dark_image=p, size=(side_px, side_px))


def empty_image(side_px: int) -> ctk.CTkImage:
    """Cuadro gris que se muestra mientras llega la miniatura real."""
    light = rounded_thumbnail(Image.new("RGB", (8, 8), "#d9cfee"), side_px * 2, 12)
    dark = rounded_thumbnail(Image.new("RGB", (8, 8), "#33264d"), side_px * 2, 12)
    return ctk.CTkImage(light_image=light, dark_image=dark, size=(side_px, side_px))


def equalizer_image() -> ctk.CTkImage:
    """Adorno de la barra lateral: bloques morados que se desvanecen hacia arriba."""
    width, tall, S = 96, 100, 2
    im = Image.new("RGBA", (width * S, tall * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cell_px, gap = 14 * S, 4 * S
    heights = [4, 6, 3, 5]
    total = len(heights) * cell_px + (len(heights) - 1) * gap
    x0 = (width * S - total) // 2
    for c, h in enumerate(heights):
        for r in range(h):
            alpha = int(255 * (0.9 - 0.7 * r / max(1, h - 1)))
            x = x0 + c * (cell_px + gap)
            y = tall * S - (r + 1) * (cell_px + gap) + gap
            d.rounded_rectangle((x, y, x + cell_px, y + cell_px), radius=3 * S, fill=(167, 139, 250, alpha))
    return ctk.CTkImage(light_image=im, dark_image=im, size=(width, tall))


def cover_jpeg(img: Image.Image, square: bool, max_side: int = 1200) -> bytes:
    """Convierte una imagen cualquiera en la portada JPEG que se incrusta en el archivo."""
    img = img.convert("RGB")
    if square:
        w, h = img.size
        m = min(w, h)
        img = img.crop(((w - m) // 2, (h - m) // 2, (w - m) // 2 + m, (h - m) // 2 + m))
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    output = BytesIO()
    img.save(output, "JPEG", quality=90)
    return output.getvalue()


def resource_path(name: str) -> Path:
    """Archivo de la carpeta assets/, tanto ejecutando con Python como desde el .exe."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / name


# Atajos de teclado (también se muestran en "Acerca de")
SHORTCUTS = [("Ctrl+V", "Pegar un enlace copiado y descargarlo"),
          ("Supr", "Quitar de la lista las canciones seleccionadas (no borra archivos)"),
          ("Ctrl+F", "Buscar en la lista"),
          ("Enter", "Descargar o buscar lo escrito en el cuadro de enlace")]

# Opciones del menú de cookies: texto -> valor guardado
COOKIE_SOURCES = {"Firefox (recomendado)": "firefox", "Archivo cookies.txt": "file"}
COOKIE_SOURCES.update({n.capitalize(): n for n in COOKIE_BROWSERS if n != "firefox"})


def fetch_bytes(url: str, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def open_path(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        messagebox.showerror("No se pudo abrir", str(e))


def choose_font(root) -> str:
    families = set(tkfont.families(root))
    for f in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Ubuntu", "DejaVu Sans"):
        if f in families:
            return f
    return tkfont.nametofont("TkDefaultFont").actual("family")


def release_variable(var: tk.Variable):
    """Libera ya, en el hilo principal, una variable de tkinter que se deja de usar.
    Si no, Python la libera donde caiga su última referencia, a veces en un hilo de
    descarga, y tkinter avisa en consola "main thread is not in main loop"."""
    try:
        if var._tk is not None:
            var._tk.globalunsetvar(var._name)
            for name in var._tclCommands or ():
                var._tk.deletecommand(name)
            var._tclCommands = None
    except (tk.TclError, AttributeError):
        pass
    var._tk = None  # así su __del__ ya no hace nada


def setup_columns(frame):
    for i, a in enumerate(COLUMN_WIDTHS):
        frame.grid_columnconfigure(i, minsize=a, weight=1 if a == 0 else 0)


# ==============================================================================
# FILA DE LA LISTA
# ==============================================================================
class ListRow:
    """Una canción en la lista: casilla, estado, miniatura y datos."""

    def __init__(self, app, parent, vid):
        self.app, self.vid = app, vid
        F = app.f_normal
        self.frame = ctk.CTkFrame(parent, corner_radius=8, fg_color=C["row_a"])
        self.frame.pack(fill="x", pady=1)
        setup_columns(self.frame)
        self._last = {}

        self.chk = ctk.CTkCheckBox(
            self.frame, text="", variable=app.rows[vid]["check"], width=24,
            checkbox_width=18, checkbox_height=18, corner_radius=5, border_width=2,
            fg_color=C["accent"], hover_color=C["accent_hover"], border_color=C["muted"],
            checkmark_color=C["on_accent"], command=app._on_row_checked)
        self.chk.grid(row=0, column=0, padx=(10, 0), pady=6)

        def label(col, width, **kw):
            lbl = ctk.CTkLabel(self.frame, text="", anchor="w", width=width, font=F, **kw)
            lbl.grid(row=0, column=col, sticky="ew", padx=(0, 6))
            return lbl

        self.l_status = label(1, 140, compound="left", image=app.ico_status["yellow"])
        self.l_thumb = ctk.CTkLabel(self.frame, text="", image=app.img_empty, width=40, height=40)
        self.l_thumb.grid(row=0, column=2, padx=6, pady=6)
        self.l_title = label(3, 200)
        self.l_artist = label(4, 160)
        self.l_album = label(5, 160)
        self.l_year = label(6, 50)
        self.l_link = label(7, 230, compound="left", image=app.ico_external)

        for w in (self.frame, self.l_status, self.l_thumb, self.l_title, self.l_artist,
                  self.l_album, self.l_year, self.l_link):
            w.bind("<Button-1>", lambda e: app._select(vid), add=True)
            w.bind("<Double-Button-1>", lambda e: app._on_double_click(vid), add=True)
            # Clic derecho (Button-2 en macOS): menú con las acciones de esa canción
            for button in ("<Button-3>", "<Button-2>" if sys.platform == "darwin" else None):
                if button:
                    w.bind(button, lambda e: app._row_menu(vid, e), add=True)

        # Pasar el mouse por el estado de una canción en cola/descargándose lo convierte en
        # "✕ Cancelar"; un clic ahí la cancela (sin tener que seleccionarla ni marcarla).
        self._over_status = False
        self.l_status.bind("<Enter>", lambda e: self._hover_status(True), add=True)
        self.l_status.bind("<Leave>", lambda e: self._hover_status(False), add=True)
        self.l_status.bind("<Button-1>", self._on_status_click, add=True)

    def _is_cancellable(self) -> bool:
        f = self.app.rows.get(self.vid)
        return bool(f) and f["color"] == "yellow"

    def _hover_status(self, inside: bool):
        self._over_status = inside
        if self.vid in self.app.rows:
            self._paint_status(self.app.rows[self.vid])

    def _paint_status(self, f):
        if self._over_status and f["color"] == "yellow":
            self._set("status", self.l_status, text="  Cancelar", image=self.app.ico_status["red"],
                      text_color=STATUS_COLORS["red"], cursor="hand2")
        else:
            self._set("status", self.l_status, text="  " + shorten(f["text"], 20),
                      image=self.app.ico_status[f["color"]], text_color=C["text"], cursor="")

    def _on_status_click(self, _event=None):
        if self._is_cancellable():
            self.app._cancel_songs([self.vid])

    def _set(self, key, widget, **kw):
        if self._last.get(key) != kw:
            widget.configure(**kw)
            self._last[key] = kw

    def refresh_row(self, f):
        self._paint_status(f)
        self._set("title", self.l_title, text=shorten(f["title"], 34))
        # 19 caracteres caben en la columna; más largo la ensancha y desalinea la fila
        self._set("artist", self.l_artist, text=shorten(f["artist"] or "—", 19))
        self._set("album", self.l_album, text=shorten(f["album"] or "—", 19))
        self._set("year", self.l_year, text=f["year"] or "—")
        self._set("link", self.l_link, text="  " + shorten(f["url"], 32))
        if f["img_s"] is not None:
            self._set("thumb", self.l_thumb, image=f["img_s"])

    def paint(self, selected: bool):
        pos = self.app.rows[self.vid].get("pos", 0)  # posición en la vista (filtrada/ordenada)
        color = C["sel"] if selected else (C["row_a"] if pos % 2 == 0 else C["row_b"])
        if self._last.get("color") != color:
            self.frame.configure(fg_color=color)
            self._last["color"] = color

    def destroy(self):
        self.frame.destroy()


# ==============================================================================
# AVISO (franja dentro de la tarjeta de controles: enlace copiado, actualización…)
# ==============================================================================
class Notice:
    def __init__(self, app, parent, row: int, ico: str):
        self.app, self.row = app, row
        self.frame = ctk.CTkFrame(parent, fg_color=C["card2"], corner_radius=10)
        # Primero los botones: así el texto nunca les quita espacio
        ctk.CTkButton(self.frame, text="✕", width=28, height=28, fg_color="transparent",
                      hover_color=C["hover"], text_color=C["muted"],
                      command=self.hide).pack(side="right", padx=(0, 6))
        self.buttons = ctk.CTkFrame(self.frame, fg_color="transparent")
        self.buttons.pack(side="right")
        self.lbl = ctk.CTkLabel(self.frame, text="", image=icon(ico, 16), compound="left",
                                font=app.f_normal, anchor="w")
        self.lbl.pack(side="left", fill="x", expand=True, padx=(10, 8), pady=6)
        self.visible = False

    def show(self, text: str, actions=()):
        """acciones: lista de (texto, función, es_principal)."""
        for b in self.buttons.winfo_children():
            b.destroy()
        self.lbl.configure(text="  " + tk_safe(text))
        for btn_text, func, primary in actions:
            def action(f=func):
                self.hide()
                f()
            self.app._button(self.buttons, btn_text, action, accent=primary, height=28).pack(
                side="left", padx=(0, 6))
        self.frame.grid(row=self.row, column=0, columnspan=4, sticky="ew", padx=14, pady=(0, 10))
        self.visible = True

    def hide(self):
        self.frame.grid_remove()
        self.visible = False


# ==============================================================================
# RESULTADOS DE BÚSQUEDA
# ==============================================================================
class SearchResultsWindow(ctk.CTkToplevel):
    """Lista de resultados de 'artista canción' para elegir cuáles descargar."""

    def __init__(self, app, query: str, results: list):
        super().__init__(app)
        self.app = app
        self.title(f"Resultados: {tk_safe(query)}")
        self.geometry("760x580")
        self.minsize(560, 380)
        self.transient(app)
        self.configure(fg_color=C["bg"])
        self.results = results
        self.marks = {}
        self.thumbnails = {}

        ctk.CTkLabel(self, text=f"  Resultados para “{shorten(query, 50)}”", image=icon("search", 18),
                     compound="left", font=app.f_detail, anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        ctk.CTkLabel(self, text="Marca las canciones que quieres descargar.", font=app.f_normal,
                     text_color=C["muted"], anchor="w").pack(fill="x", padx=16, pady=(0, 8))

        song_list = ctk.CTkScrollableFrame(self, fg_color=C["card"], corner_radius=14, border_width=1,
                                       border_color=C["border"])
        song_list.pack(fill="both", expand=True, padx=16)
        for i, r in enumerate(results):
            self._row(song_list, i, r)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=12)
        self.btn_download_selected = app._button(footer, "", self._on_download, icon("download", 18, C["on_accent"]),
                                    accent=True, height=36)
        self.btn_download_selected.pack(side="right")
        app._button(footer, "Cancelar", self.destroy, height=36).pack(side="right", padx=8)
        app._button(footer, "Abrir en el navegador", self._open, icon("external", 16),
                   height=36).pack(side="left")
        if results:
            self.marks[results[0]["id"]].set(True)  # el primero suele ser el correcto
        self._count()
        self.after(150, self.focus)

    def _row(self, parent, i, r):
        var = tk.BooleanVar(value=False)
        self.marks[r["id"]] = var
        row = ctk.CTkFrame(parent, fg_color=C["row_a"] if i % 2 == 0 else C["row_b"], corner_radius=8)
        row.pack(fill="x", pady=1)
        ctk.CTkCheckBox(row, text="", variable=var, width=24, checkbox_width=18, checkbox_height=18,
                        corner_radius=5, border_width=2, fg_color=C["accent"],
                        hover_color=C["accent_hover"], border_color=C["muted"],
                        checkmark_color=C["on_accent"], command=self._count).pack(
            side="left", padx=(10, 6), pady=6)
        thumb = ctk.CTkLabel(row, text="", image=self.app.img_empty, width=40, height=40)
        thumb.pack(side="left", padx=6, pady=6)
        self.thumbnails[r["id"]] = thumb
        texts = ctk.CTkFrame(row, fg_color="transparent")
        texts.pack(side="left", fill="x", expand=True, padx=6)
        ctk.CTkLabel(texts, text=shorten(r["title"], 70), font=self.app.f_bold, anchor="w").pack(anchor="w")
        sub = " • ".join(p for p in (r.get("canal"), r.get("duration")) if p)
        ctk.CTkLabel(texts, text=tk_safe(sub) or "—", font=self.app.f_small, text_color=C["muted"],
                     anchor="w").pack(anchor="w")

        def toggle(_e=None, v=var):
            v.set(not v.get())
            self._count()
        for w in (row, thumb, texts, *texts.winfo_children()):
            w.bind("<Button-1>", toggle, add=True)
        if r.get("thumbnail"):
            self.app.pool.submit(self.app._fetch_result_image, r["id"], r["thumbnail"])

    def set_image(self, vid, data):
        lbl = self.thumbnails.get(vid)
        if lbl is None:
            return
        try:
            img = Image.open(BytesIO(data))
            img.load()
            lbl.configure(image=square_image(img, 40, 8))
        except Exception:
            pass

    def _chosen(self):
        return [r for r in self.results if self.marks[r["id"]].get()]

    def _count(self):
        n = len(self._chosen())
        self.btn_download_selected.configure(text=f"Descargar ({n})" if n else "Descargar",
                                 state="normal" if n else "disabled")

    def _open(self):
        for r in self._chosen()[:5] or self.results[:1]:
            webbrowser.open(r["url"])

    def _on_download(self):
        for r in self._chosen():
            self.app._enqueue(r["url"])
        self.app.var_url.set("")
        self.destroy()


# ==============================================================================
# EXPORTAR
# ==============================================================================
class ExportWindow(ctk.CTkToplevel):
    """Elegir qué exportar: lista de reproducción .m3u o tabla .csv."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Exportar")
        self.geometry("460x330")
        self.resizable(False, False)
        self.transient(app)
        self.configure(fg_color=C["bg"])
        view = app._rows_in_view()
        downloaded = sum(1 for v in view if app.rows[v]["color"] == "green" and app.rows[v]["path"]
                          and Path(app.rows[v]["path"]).exists())
        filtered = len(view) != len(app.rows)

        ctk.CTkLabel(self, text="  Exportar", image=icon("export", 18), compound="left",
                     font=app.f_detail, anchor="w").pack(fill="x", padx=18, pady=(14, 2))
        ctk.CTkLabel(self, text="Se exporta lo que se ve en la lista" + (" (con el filtro actual)." if filtered
                                                                          else "."),
                     font=app.f_small, text_color=C["muted"], anchor="w").pack(fill="x", padx=18, pady=(0, 10))
        self.btn_m3u = self._option(
            "Lista de reproducción (.m3u)",
            f"{downloaded} {'canción descargada' if downloaded == 1 else 'canciones descargadas'} · "
            "para abrir en VLC, foobar2000, Winamp…", app._export_m3u, downloaded > 0)
        self.btn_csv = self._option(
            "Tabla (.csv)", f"{len(view)} {'fila' if len(view) == 1 else 'filas'} · "
            "título, artista, álbum, estado… para Excel", app._export_csv, len(view) > 0)
        app._button(self, "Cerrar", self.destroy, width=100, height=34).pack(anchor="e", padx=18, pady=(4, 12))
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(150, self.focus)

    def _option(self, title, detail, action, active):
        container = self.app._card(self)
        container.pack(fill="x", padx=18, pady=5)

        def choose():
            self.destroy()       # primero se cierra esta ventana; luego sale "Guardar como"
            self.app.after(50, action)
        button = self.app._button(container, title, choose, icon("export", 16, C["on_accent"]),
                                accent=True, height=36, width=300, state="normal" if active else "disabled")
        button.pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(container, text=detail, font=self.app.f_small, text_color=C["muted"], anchor="w",
                     justify="left", wraplength=400).pack(fill="x", padx=14, pady=(0, 10))
        return button


# ==============================================================================
# EDITOR DE ETIQUETAS
# ==============================================================================
class TagEditorWindow(ctk.CTkToplevel):
    """Corrige título, artista, álbum, año, género y portada de un archivo ya descargado."""

    FIELDS = [("title", "Título"), ("artist", "Artista"), ("album", "Álbum"),
              ("year", "Año"), ("genre", "Género")]

    def __init__(self, app, vid: str, path: str):
        super().__init__(app)
        self.app, self.vid, self.path = app, vid, path
        self.title("Editar etiquetas")
        self.geometry("660x470")
        self.resizable(False, False)
        self.transient(app)
        self.configure(fg_color=C["bg"])
        self.new_cover = None
        current_items = read_tags(path)

        ctk.CTkLabel(self, text="  Editar etiquetas", image=icon("edit", 18), compound="left",
                     font=app.f_detail, anchor="w").pack(fill="x", padx=18, pady=(14, 0))
        ctk.CTkLabel(self, text=shorten(Path(path).name, 80), font=app.f_small, text_color=C["muted"],
                     anchor="w").pack(fill="x", padx=18, pady=(0, 10))

        body = self.app._card(self)
        body.pack(fill="both", expand=True, padx=18)
        left = ctk.CTkFrame(body, fg_color="transparent")
        left.pack(side="left", padx=16, pady=16, anchor="n")
        self.l_cover = ctk.CTkLabel(left, text="", image=app.img_empty_cover, width=160, height=160)
        self.l_cover.pack()
        data = read_cover(path)
        if data:
            self._show_cover(data)
        app._button(left, "Cambiar portada…", self._choose_cover, icon("folder", 16),
                   height=32).pack(pady=(10, 0), fill="x")

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(0, 16), pady=16)
        right.grid_columnconfigure(1, weight=1)
        self.vars = {}
        for i, (key, title) in enumerate(self.FIELDS):
            ctk.CTkLabel(right, text=title, font=app.f_bold, anchor="w").grid(
                row=i, column=0, sticky="w", padx=(0, 12), pady=5)
            var = tk.StringVar(value=tk_safe(current_items.get(key, "")))
            self.vars[key] = var
            ctk.CTkEntry(right, textvariable=var, height=34, corner_radius=10, font=app.f_normal,
                         fg_color=C["input"], border_color=C["border"], text_color=C["text"]).grid(
                row=i, column=1, sticky="ew", pady=5)

        # Nombre del archivo (sin extensión). Si hoy coincide con el título, lo sigue al
        # cambiar el título, hasta que el usuario lo escriba a mano.
        row = len(self.FIELDS)
        ctk.CTkLabel(right, text="Archivo", font=app.f_bold, anchor="w").grid(
            row=row, column=0, sticky="w", padx=(0, 12), pady=(14, 5))
        box = ctk.CTkFrame(right, fg_color="transparent")
        box.grid(row=row, column=1, sticky="ew", pady=(14, 5))
        box.grid_columnconfigure(0, weight=1)
        self.extension = Path(path).suffix
        self.var_filename = tk.StringVar(value=tk_safe(Path(path).stem))
        self.entry_file = ctk.CTkEntry(box, textvariable=self.var_filename, height=34, corner_radius=10,
                                      font=app.f_normal, fg_color=C["input"], border_color=C["border"],
                                      text_color=C["text"])
        self.entry_file.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(box, text=self.extension, font=app.f_normal, text_color=C["muted"]).grid(
            row=0, column=1, padx=(6, 0))
        self.l_name = ctk.CTkLabel(right, text="", font=app.f_small, text_color=C["muted"], anchor="w",
                                     justify="left", wraplength=330)
        self.l_name.grid(row=row + 1, column=1, sticky="w")
        self.follows_title = Path(path).stem == valid_filename(current_items.get("title", ""))
        self.entry_file._entry.bind("<Key>", lambda e: setattr(self, "follows_title", False), add=True)
        self.vars["title"].trace_add("write", self._on_title_change)
        self.var_filename.trace_add("write", lambda *a: self._show_final_name())
        self._show_final_name()

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=12)
        app._button(footer, "Guardar", self._save, accent=True, width=120, height=36).pack(side="right")
        app._button(footer, "Cancelar", self.destroy, width=100, height=36).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(150, self.focus)

    def _on_title_change(self, *_):
        if self.follows_title:
            self.var_filename.set(valid_filename(self.vars["title"].get()))

    def _show_final_name(self):
        """Avisa si el nombre se va a guardar distinto (caracteres no permitidos) o si queda vacío."""
        typed = self.var_filename.get().strip()
        final = valid_filename(typed, self.extension)
        if not final:
            text = "El nombre no puede quedar vacío."
        elif final != typed:
            text = f"Se guardará como: {shorten(final + self.extension, 60)}"
        else:
            text = ""
        self.l_name.configure(text=text, text_color=C["red"] if not final else C["muted"])

    def _show_cover(self, data: bytes):
        try:
            img = Image.open(BytesIO(data))
            img.load()
            self.l_cover.configure(image=square_image(img, 160, 14))
        except Exception:
            pass

    def _choose_cover(self):
        file = filedialog.askopenfilename(
            parent=self, title="Elige la imagen de portada",
            filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp *.bmp"), ("Todos", "*.*")])
        if not file:
            return
        try:
            with Image.open(file) as img:
                self.new_cover = cover_jpeg(img, square=bool(self.app.var_crop.get()))
        except Exception as e:
            messagebox.showerror("Imagen no válida", str(e), parent=self)
            return
        self._show_cover(self.new_cover)

    def _save(self):
        data = {k: v.get().strip() for k, v in self.vars.items()}
        name = valid_filename(self.var_filename.get(), self.extension)
        if not name:
            messagebox.showerror("Nombre no válido", "El nombre del archivo no puede quedar vacío.", parent=self)
            return
        try:
            write_tags(self.path, data, self.new_cover)
        except Exception as e:
            messagebox.showerror("No se pudieron guardar las etiquetas", str(e), parent=self)
            return
        # Primero las etiquetas, luego el nombre: si renombrar falla, lo demás ya quedó guardado
        try:
            new_item = rename_file(self.path, name)
        except (OSError, ValueError) as e:
            self.app._on_tags_saved(self.vid, data, self.new_cover)
            self.new_cover = None
            detail = str(e) if isinstance(e, (FileExistsError, ValueError)) else (
                f"{e}\n\n¿Está abierto en un reproductor? Ciérralo e inténtalo de nuevo.")
            messagebox.showerror("No se pudo cambiar el nombre",
                                 f"Las etiquetas sí se guardaron, pero el archivo no se renombró.\n\n{detail}",
                                 parent=self)
            return
        self.app._on_tags_saved(self.vid, data, self.new_cover, str(new_item))
        self.destroy()


# ==============================================================================
# VENTANA PRINCIPAL
# ==============================================================================
# ==============================================================================
# PLAYLISTS VIGILADAS
# ==============================================================================
class WatchedWindow(ctk.CTkToplevel):
    """Playlists guardadas: AUDYAX revisa si tienen canciones nuevas y baja solo esas."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Playlists vigiladas")
        self.geometry("640x520")
        self.minsize(560, 380)
        self.transient(app)
        self.configure(fg_color=C["bg"])

        ctk.CTkLabel(self, text="Playlists vigiladas", font=app.f_detail, anchor="w").pack(
            anchor="w", padx=16, pady=(14, 0))
        ctk.CTkLabel(self, text="AUDYAX revisa si tienen canciones nuevas y baja solo esas, a la "
                                "carpeta donde guardas tu música.", font=app.f_small,
                     text_color=C["muted"], anchor="w", wraplength=600, justify="left").pack(
            anchor="w", padx=16, pady=(0, 8))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 8))
        self.var_url = tk.StringVar(value=self._suggested())
        e = ctk.CTkEntry(row, textvariable=self.var_url, height=34, corner_radius=10, font=app.f_normal,
                         placeholder_text="Enlace de una playlist o un álbum…", fg_color=C["input"],
                         border_color=C["border"], text_color=C["text"])
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda _e: self._add())
        app._button(row, "Vigilar", self._add, icon("eye", 16, C["on_accent"]),
                   accent=True, width=110, height=34).pack(side="left", padx=(8, 0))

        self.song_list = ctk.CTkScrollableFrame(self, fg_color=C["card"], corner_radius=14, border_width=1,
                                            border_color=C["border"])
        self.song_list.pack(fill="both", expand=True, padx=16)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=12)
        self.btn_check_all = app._button(footer, "Revisar todas", app._check_all_watched, icon("refresh", 16),
                                    height=34)
        self.btn_check_all.pack(side="left")
        app._button(footer, "Cerrar", self.destroy, height=34, width=90).pack(side="right")
        self.refresh()
        self.after(150, self.focus)

    def _suggested(self) -> str:
        """El enlace copiado o la última playlist bajada, si es una playlist que aún no se vigila."""
        candidates = [getattr(self.app, "last_playlist", "")]
        try:
            candidates.insert(0, self.app.clipboard_get().strip())
        except tk.TclError:
            pass
        for url in candidates:
            if url and watched.is_playlist(url):
                canonical = watched.playlist_url(url)
                if not any(v["url"] == canonical for v in self.app.watched):
                    return canonical
        return ""

    def _add(self):
        url = self.var_url.get().strip()
        if url and self.app._watch(url):
            self.var_url.set("")

    def refresh(self):
        app = self.app
        for w in self.song_list.winfo_children():
            w.destroy()
        self.btn_check_all.configure(state="normal" if app.watched else "disabled")
        if not app.watched:
            ctk.CTkLabel(self.song_list, text="Todavía no vigilas ninguna playlist.\nPega arriba el enlace de "
                                          "una y dale «Vigilar».", font=app.f_normal, text_color=C["muted"],
                         justify="center").pack(pady=40)
            return
        for i, v in enumerate(app.watched):
            if i:
                ctk.CTkFrame(self.song_list, height=1, fg_color=C["border"]).pack(fill="x", padx=8)
            self._row(v)

    def _row(self, v):
        app = self.app
        container = ctk.CTkFrame(self.song_list, fg_color="transparent")
        container.pack(fill="x", padx=4, pady=6)
        buttons = ctk.CTkFrame(container, fg_color="transparent")
        buttons.pack(side="right")  # primero: así el texto nunca les quita espacio
        text = ctk.CTkFrame(container, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True, padx=(6, 8))
        ctk.CTkLabel(text, text=shorten(tk_safe(v.get("name") or v["url"]), 40), font=app.f_bold,
                     anchor="w").pack(anchor="w")
        checking = v["url"] in app.checking
        status = "Revisando…" if checking else watched.check_summary(v)
        ctk.CTkLabel(text, text=shorten(tk_safe(status), 110), font=app.f_small, anchor="w",
                     wraplength=300, justify="left", text_color=C["red"] if v.get("error") and not checking else C["muted"]).pack(anchor="w")
        ctk.CTkLabel(text, text=shorten(v["folder"], 60), font=app.f_small, text_color=C["muted"],
                     anchor="w", image=icon("folder", 12, C["muted"]), compound="left").pack(anchor="w")

        n = len(app.watched_new.get(v["url"]) or [])
        b = app._button(buttons, f"Bajar {n} nueva{'s' if n != 1 else ''}" if n else "Bajar nuevas",
                       lambda u=v["url"]: app._download_new(u), accent=bool(n), height=30)
        b.configure(state="normal" if n and not checking else "disabled")
        b.pack(side="left", padx=(0, 6))
        r = app._button(buttons, "", lambda x=v: app._check_watched(x), icon("refresh", 16),
                       width=34, height=30)
        r.configure(state="disabled" if checking else "normal")
        r.pack(side="left", padx=(0, 6))
        app._button(buttons, "", lambda u=v["url"]: self._remove(u), icon("trash", 16),
                   width=34, height=30).pack(side="left")

    def _remove(self, url):
        v = next((x for x in self.app.watched if x["url"] == url), None)
        name = (v or {}).get("name") or url
        if messagebox.askyesno("Dejar de vigilar", f"¿Dejar de vigilar «{name}»?\n\n"
                                                   "Las canciones que ya bajaste no se borran.", parent=self):
            self.app._unwatch(url)


class App(ctk.CTk, *_DND_BASES):
    def __init__(self):
        self.cfg = load_config()
        self.theme = "light" if self.cfg.get("theme") == "light" else "dark"
        ctk.set_appearance_mode(self.theme)
        super().__init__()
        self.has_dnd = False
        if TkinterDnD is not None:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self.has_dnd = True
            except Exception:  # tkdnd no disponible en este sistema: sin arrastrar/soltar
                pass
        self.title("AUDYAX")
        self._set_window_icon()
        self.geometry("1180x800")
        self.minsize(1050, 640)
        self.configure(fg_color=C["bg"])

        fam = choose_font(self)
        self.f_normal = ctk.CTkFont(family=fam, size=13)
        self.f_bold = ctk.CTkFont(family=fam, size=13, weight="bold")
        self.f_small = ctk.CTkFont(family=fam, size=11)
        self.f_title = ctk.CTkFont(family=fam, size=24, weight="bold")
        self.f_detail = ctk.CTkFont(family=fam, size=18, weight="bold")

        self.events = queue.Queue()   # worker -> interfaz
        self.jobs = queue.Queue()  # interfaz -> worker
        self.pause = threading.Event()        # activado = descargas en pausa
        self.owners = {}                      # id de canción -> conjunto 'omitir' de su trabajo
        self.tokens = set()                   # un Event de cancelación por trabajo en curso
        self.tokens_lock = threading.Lock()
        self.threads = []                       # hilos que atienden la cola de trabajos
        self.limit = threading.BoundedSemaphore(parallel_downloads(self.cfg))
        self.limit_n = parallel_downloads(self.cfg)
        self.pool = ThreadPoolExecutor(max_workers=4)  # descarga de miniaturas
        self.pending = 0
        self.general_errors = 0
        self.rows = {}   # id -> datos de la canción
        self.sel = None   # id de la canción mostrada en el detalle
        self.order = ("num", False)   # (columna, descendente); "num" = orden de llegada
        self._visible = set()        # ids que pasan el filtro actual
        self._view_scheduled = False
        self.results_window = None
        self.export_window = None
        self.watched_window = None
        self.player = None          # barra del mini reproductor (se crea al usarla)
        self.watched = [dict(v) for v in self.cfg.get("watched") or []]
        self.cfg["watched"] = self.watched
        self.checking = set()           # urls de vigiladas que se están revisando
        self.watched_new = {}             # url -> canciones nuevas encontradas en la última revisión
        self._offered_watch = set()      # playlists a las que ya se les ofreció vigilarlas
        self.batch = {"ok": 0, "err": 0, "omit": 0}  # resultado de la tanda actual (notificación)
        self._background_notice_shown = False
        self.tray = Tray(self._tray_image(),
                               on_open=lambda: self.events.put(("tray", "open")),
                               on_pause=lambda: self.events.put(("tray", "pause")),
                               on_quit=lambda: self.events.put(("tray", "quit")),
                               is_paused=lambda: self.pause.is_set())
        self.searching = False
        self.updating = False
        self._last_offered = ""     # último enlace del portapapeles que ya se ofreció
        self._clipboard_scheduled = False

        self.ico_status = {"green": icon("ok", 20, "#2ecc71"), "yellow": icon("clock", 20, "#f1c40f"),
                           "red": icon("err", 20, "#e74c3c")}
        self.ico_dot = {k: icon("dot", 14, c) for k, c in STATUS_COLORS.items()}
        self.ico_external = icon("external", 16, C["muted"])
        self.img_empty = empty_image(40)
        self.img_empty_big = empty_image(88)
        self.img_empty_cover = empty_image(160)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self._setup_shortcuts()
        self.after(100, self._process_events)
        self.after(400, self._check_ffmpeg)
        self.after(800, self._check_clipboard)
        self.bind("<FocusIn>", self._on_focus, add=True)
        if self.cfg.get("check_ytdlp", True):
            self.after(2500, lambda: self._check_ytdlp(manual=False))
        if self.cfg.get("check_watched", True) and self.watched:
            self.after(4000, lambda: self._check_all_watched(on_open=True))

    # ---------------------------------------------------------------- construcción
    def _button(self, parent, text, command, icon_=None, accent=False, **kw):
        if accent:
            style = dict(fg_color=C["accent"], hover_color=C["accent_hover"],
                          text_color=C["on_accent"], border_width=0)
        else:
            style = dict(fg_color="transparent", hover_color=C["hover"], text_color=C["text"],
                          border_width=1, border_color=C["border"])
        return ctk.CTkButton(parent, text=text, command=command, image=icon_, compound="left",
                             font=self.f_bold if accent else self.f_normal, corner_radius=10,
                             height=kw.pop("height", 36), **style, **kw)

    def _card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=C["card"], corner_radius=14, border_width=1,
                            border_color=C["border"], **kw)

    def _build(self):
        rg_initial = bool(self.cfg["replaygain"]) and HAS_MUTAGEN
        norm_initial = bool(self.cfg["normalize"]) and not rg_initial  # nunca las dos a la vez
        self.var_url = tk.StringVar()
        self.var_folder = tk.StringVar(value=self.cfg["folder"])
        self.var_format = tk.StringVar(value=self.cfg["audio_format"])
        self.var_quality = tk.StringVar(value=self.cfg["quality"])
        self.var_norm = tk.BooleanVar(value=norm_initial)
        self.var_crop = tk.BooleanVar(value=self.cfg["crop"])
        self.var_rg = tk.BooleanVar(value=rg_initial)
        self.var_all = tk.BooleanVar(value=False)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        main = ctk.CTkFrame(self, fg_color="transparent")
        self.main = main
        main.grid(row=0, column=1, sticky="nsew", padx=(14, 16), pady=(12, 10))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        # Encabezado
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(header, text="AUDYAX", font=self.f_title, anchor="w").pack(anchor="w")
        ctk.CTkLabel(header, text="Pega el enlace  •  Descarga  •  Disfruta", font=self.f_normal,
                     text_color=C["muted"], anchor="w").pack(anchor="w")

        # Tarjeta de controles
        ctrl = self._card(main)
        ctrl.grid(row=1, column=0, sticky="ew")
        ctrl.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(ctrl, text=" Enlace:", image=icon("link", 16), compound="left",
                     font=self.f_bold).grid(row=0, column=0, sticky="w", padx=(14, 8), pady=(12, 6))
        entry = ctk.CTkEntry(ctrl, textvariable=self.var_url, height=38, corner_radius=10,
                               placeholder_text="Pega un enlace de YouTube o escribe artista y canción…", font=self.f_normal,
                               fg_color=C["input"], border_color=C["border"], text_color=C["text"])
        entry.grid(row=0, column=1, sticky="ew", pady=(12, 6))
        entry.bind("<Return>", lambda e: self._on_download())
        entry.focus_set()
        self.entry = entry
        self._button(ctrl, "Pegar", self._paste, width=90, height=38).grid(
            row=0, column=2, padx=(10, 4), pady=(12, 6))
        self.ico_download = icon("download", 18, C["on_accent"])
        self.ico_search = icon("search", 18, C["on_accent"])
        self.btn_download = self._button(ctrl, "Descargar", self._on_download, self.ico_download,
                                         accent=True, width=130, height=38)
        self.btn_download.grid(row=0, column=3, padx=(4, 14), pady=(12, 6))
        # El botón dice "Buscar" cuando lo escrito no es un enlace
        self.var_url.trace_add("write", lambda *a: self._update_main_button())

        row2 = ctk.CTkFrame(ctrl, fg_color="transparent")
        row2.grid(row=1, column=0, columnspan=4, sticky="ew", padx=14, pady=4)
        ctk.CTkLabel(row2, text=" Guardar en:", image=icon("folder", 16), compound="left",
                     font=self.f_bold).pack(side="left")
        ctk.CTkEntry(row2, textvariable=self.var_folder, width=250, height=34, corner_radius=10,
                     font=self.f_normal, fg_color=C["input"], border_color=C["border"],
                     text_color=C["text"]).pack(side="left", padx=(10, 4))
        self._button(row2, "", self._choose_folder, icon("folder", 18), width=38, height=34).pack(
            side="left", padx=(0, 18))
        ctk.CTkLabel(row2, text="Formato:", font=self.f_normal).pack(side="left")
        self.cb_format = ctk.CTkOptionMenu(
            row2, variable=self.var_format, values=["mp3", "m4a", "opus", "original"], width=100,
            height=34, font=self.f_normal, dropdown_font=self.f_normal, fg_color=C["input"],
            button_color=C["card2"], button_hover_color=C["hover"], text_color=C["text"],
            dropdown_fg_color=C["card"], dropdown_text_color=C["text"],
            dropdown_hover_color=C["hover"], command=lambda v: self._on_format_change())
        self.cb_format.pack(side="left", padx=(6, 16))
        ctk.CTkLabel(row2, text="Calidad (kbps):", font=self.f_normal).pack(side="left")
        self.cb_quality = ctk.CTkOptionMenu(
            row2, variable=self.var_quality, values=["128", "192", "256", "320"], width=80,
            height=34, font=self.f_normal, dropdown_font=self.f_normal, fg_color=C["input"],
            button_color=C["card2"], button_hover_color=C["hover"], text_color=C["text"],
            dropdown_fg_color=C["card"], dropdown_text_color=C["text"],
            dropdown_hover_color=C["hover"])
        self.cb_quality.pack(side="left", padx=(6, 0))

        row3 = ctk.CTkFrame(ctrl, fg_color="transparent")
        row3.grid(row=2, column=0, columnspan=4, sticky="ew", padx=14, pady=(4, 12))

        def checkbox(text, var, command=None):
            c = ctk.CTkCheckBox(row3, text=text, variable=var, command=command, font=self.f_normal,
                                checkbox_width=20, checkbox_height=20, corner_radius=5,
                                fg_color=C["accent"], hover_color=C["accent_hover"],
                                border_color=C["muted"], checkmark_color=C["on_accent"])
            c.pack(side="left", padx=(0, 22))
            return c

        self.chk_rg = checkbox("ReplayGain (recomendado)", self.var_rg, self._on_rg_checked)
        if not HAS_MUTAGEN:
            self.chk_rg.configure(state="disabled")
        self.chk_norm = checkbox("Normalizar volumen (modifica el audio)", self.var_norm, self._on_norm_checked)
        checkbox("Portada cuadrada", self.var_crop)
        self._on_format_change()

        # Avisos (ocultos hasta que hagan falta)
        self.clipboard_notice = Notice(self, ctrl, 3, "link")
        self.update_notice = Notice(self, ctrl, 4, "refresh")
        self.watched_notice = Notice(self, ctrl, 5, "eye")

        # Lista
        list_frame = self._card(main)
        list_frame.grid(row=2, column=0, sticky="nsew", pady=10)
        self._build_list_bar(list_frame)
        list_header = ctk.CTkFrame(list_frame, fg_color=C["card2"], corner_radius=10, height=38)
        list_header.pack(fill="x", padx=6, pady=(6, 2), ipady=2)
        setup_columns(list_header)
        ctk.CTkCheckBox(list_header, text="", variable=self.var_all, width=24, checkbox_width=18,
                        checkbox_height=18, corner_radius=5, fg_color=C["accent"],
                        hover_color=C["accent_hover"], border_color=C["muted"],
                        checkmark_color=C["on_accent"], command=self._check_all).grid(
            row=0, column=0, padx=(10, 0), pady=6)
        # Cabeceras: clic para ordenar (▲ ascendente, ▼ descendente, tercer clic = orden de llegada)
        self.headers = {}
        for col, text, span, key in [(1, "Estado", 1, "status"), (2, "Título", 2, "title"),
                                        (4, "Artista", 1, "artist"), (5, "Álbum", 1, "album"),
                                        (6, "Año", 1, "year"), (7, "Enlace", 1, None)]:
            lbl = ctk.CTkLabel(list_header, text=text, font=self.f_bold, anchor="w")
            lbl.grid(row=0, column=col, columnspan=span, sticky="w", padx=(4, 0))
            if key:
                self.headers[key] = (lbl, text)
                lbl.configure(cursor="hand2")
                lbl.bind("<Button-1>", lambda e, c=key: self._sort_by(c))
        list_header.grid_columnconfigure(7, minsize=COLUMN_WIDTHS[7] + 14)
        self.song_list = ctk.CTkScrollableFrame(list_frame, fg_color="transparent", corner_radius=0,
                                            scrollbar_button_color=C["card2"],
                                            scrollbar_button_hover_color=C["border"])
        self.song_list.pack(fill="both", expand=True, padx=(6, 2), pady=(0, 6))

        # Detalle
        detail_card = self._card(main)
        detail_card.grid(row=3, column=0, sticky="ew")
        self.detail_header = ctk.CTkFrame(detail_card, fg_color="transparent")
        self.detail_header.pack(fill="x", padx=10, pady=(6, 0))
        self.btn_collapse = ctk.CTkButton(self.detail_header, text="▾", width=26, height=26, fg_color="transparent",
                                        hover_color=C["hover"], text_color=C["text"],
                                        command=self._toggle_detail)
        self.btn_collapse.pack(side="left")
        ctk.CTkLabel(self.detail_header, text=" Detalle de la canción seleccionada", image=icon("note", 16),
                     compound="left", font=self.f_bold).pack(side="left")

        self.detail_body = ctk.CTkFrame(detail_card, fg_color="transparent")
        self.detail_body.pack(fill="x", padx=12, pady=(2, 0))
        self.detail_body.grid_columnconfigure(3, weight=1)
        self.l_cover = ctk.CTkLabel(self.detail_body, text="", image=self.img_empty_big,
                                      width=88, height=88)
        self.l_cover.grid(row=0, column=0, padx=(0, 14), pady=4)
        info = ctk.CTkFrame(self.detail_body, fg_color="transparent", width=300)
        info.grid(row=0, column=1, sticky="nw", pady=4)
        self.l_detail_title = ctk.CTkLabel(info, text="Selecciona una canción de la lista.",
                                         font=self.f_detail, anchor="w")
        self.l_detail_title.pack(anchor="w")
        self.l_detail_artist = ctk.CTkLabel(info, text="", font=self.f_normal, text_color=C["muted"], anchor="w")
        self.l_detail_artist.pack(anchor="w")
        self.l_chip = ctk.CTkLabel(info, text="", font=self.f_small, fg_color=C["card2"],
                                   text_color=C["muted"], corner_radius=12, height=24)
        self.l_chip.pack(anchor="w", pady=4)
        self.l_chip.pack_forget()  # solo se muestra cuando hay año o género
        self.l_detail_info = ctk.CTkLabel(info, text="", font=self.f_small, text_color=C["muted"],
                                       anchor="w", justify="left")
        self.l_detail_info.pack(anchor="w")
        # Separador fino + datos técnicos del archivo
        ctk.CTkFrame(self.detail_body, width=1, height=70, fg_color=C["border"]).grid(
            row=0, column=2, sticky="ns", padx=(6, 24))
        data = ctk.CTkFrame(self.detail_body, fg_color="transparent")
        data.grid(row=0, column=3, sticky="w")
        self.l_info = {}
        for i, (key, title) in enumerate([("audio_format", "Formato"), ("duration", "Duración"),
                                             ("bitrate", "Bitrate"), ("size", "Tamaño"),
                                             ("sample_rate", "Muestreo"), ("rg", "ReplayGain")]):
            row, col = divmod(i, 3)
            cell = ctk.CTkFrame(data, fg_color="transparent")
            cell.grid(row=row, column=col, sticky="w", padx=(0, 36), pady=4)
            ctk.CTkLabel(cell, text=title.upper(), font=self.f_small, text_color=C["muted"],
                         anchor="w").pack(anchor="w")
            self.l_info[key] = ctk.CTkLabel(cell, text="—", font=self.f_bold, anchor="w")
            self.l_info[key].pack(anchor="w")

        self.detail_buttons = ctk.CTkFrame(detail_card, fg_color="transparent")
        self.detail_buttons.pack(fill="x", padx=12, pady=(2, 10))
        # width pequeño: cada botón mide lo que su texto (si no, todos miden 140 y no caben)
        for text, ico, cmd in [("Copiar enlace", "copy", self._copy_link),
                                ("Abrir enlace", "external", lambda: self._open_link()),
                                ("Carpeta", "folder", self._open_song_folder),
                                ("Etiquetas", "edit", self._edit_tags),
                                ("Reintentar", "refresh", self._retry),
                                ("Cancelar", "cancel", self._cancel_songs)]:
            self._button(self.detail_buttons, text, cmd, icon(ico, 16), height=34, width=60).pack(
                side="left", padx=(0, 8))
        self._button(self.detail_buttons, "Limpiar lista", self._clear_list, icon("trash", 16),
                    height=34).pack(side="right")

        # Barra de estado: contadores por color + progreso general
        bar = ctk.CTkFrame(main, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.counters = {}
        for name, label in [("green", "descargadas"), ("yellow", "en proceso"), ("red", "con error")]:
            lbl = ctk.CTkLabel(bar, image=self.ico_dot[name], compound="left",
                               text=f"  0 {label}", font=self.f_normal)
            lbl.pack(side="left", padx=(6, 26))
            self.counters[name] = (lbl, label)
        self.lbl_pause = ctk.CTkLabel(bar, text="  En pausa", image=icon("pause", 14, STATUS_COLORS["yellow"]),
                                      compound="left", font=self.f_bold, text_color=STATUS_COLORS["yellow"])
        self.lbl_pct = ctk.CTkLabel(bar, text="0%", width=44, anchor="e", font=self.f_normal)
        self.lbl_pct.pack(side="right")
        self.progress = ctk.CTkProgressBar(bar, width=260, height=8, progress_color=C["accent"],
                                           fg_color=C["card2"])
        self.progress.set(0)
        self.progress.pack(side="right", padx=10)

        # Arrastrar y soltar enlaces sobre la ventana, el cuadro de texto o la lista
        if self.has_dnd:
            for w in (self, self.entry._entry, self.song_list._parent_canvas):
                try:
                    w.drop_target_register(DND_TEXT)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                except Exception:
                    pass
    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=112, corner_radius=0, fg_color=C["sidebar"])
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.pack_propagate(False)
        ctk.CTkLabel(sidebar, text="", image=icon("note", 44, C["accent"])).pack(pady=(18, 18))

        def item(text, ico, active=False, command=None):
            ctk.CTkButton(sidebar, text=text, image=icon(ico, 26, C["text"] if active else C["muted"]),
                          compound="top", width=92, height=74, corner_radius=12, font=self.f_small,
                          fg_color=C["nav_active"] if active else "transparent",
                          hover_color=C["nav_active"] if active else C["hover"],
                          text_color=C["text"] if active else C["muted"],
                          command=command).pack(padx=10, pady=3)

        item("Inicio", "home", active=True)
        item("Vigiladas", "eye", command=self._open_watched)
        item("Configuración", "gear", command=self._open_settings)
        item("Acerca de", "info", command=self._about)

        ctk.CTkLabel(sidebar, text="", image=equalizer_image()).pack(side="bottom", pady=(0, 14))
        self.sw_theme = ctk.CTkSwitch(sidebar, text="", width=46, progress_color=C["accent"],
                                     command=lambda: self._apply_theme(
                                         "dark" if self.sw_theme.get() else "light"))
        self.sw_theme.pack(side="bottom", pady=(0, 6))
        ctk.CTkLabel(sidebar, text="Modo oscuro", font=self.f_small, text_color=C["muted"]).pack(side="bottom")
        if self.theme == "dark":
            self.sw_theme.select()

    # ---------------------------------------------------------------- tema / configuración
    def _about(self):
        top = ctk.CTkToplevel(self)
        top.title("Acerca de AUDYAX")
        top.geometry("420x400")
        top.resizable(False, False)
        top.transient(self)
        top.configure(fg_color=C["bg"])
        ctk.CTkLabel(top, text="", image=icon("note", 44, C["accent"])).pack(pady=(22, 4))
        ctk.CTkLabel(top, text="AUDYAX", font=self.f_detail).pack()
        ctk.CTkLabel(top, text=f"Versión {VERSION}  ·  yt-dlp {ytdlp_version()}", font=self.f_small,
                     text_color=C["muted"]).pack()
        ctk.CTkLabel(top, text="Usa yt-dlp, FFmpeg, mutagen, ytmusicapi,\ncustomtkinter y Pillow.", font=self.f_small,
                     text_color=C["muted"], justify="center").pack(pady=(4, 10))
        table = ctk.CTkFrame(top, fg_color=C["card"], corner_radius=10)
        table.pack(padx=24, pady=(0, 12), fill="x")
        ctk.CTkLabel(table, text="Atajos de teclado", font=self.f_bold).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 4))
        for i, (keystroke, what) in enumerate(SHORTCUTS, 1):
            ctk.CTkLabel(table, text=keystroke, font=self.f_bold, text_color=C["accent"]).grid(
                row=i, column=0, sticky="w", padx=(12, 10), pady=1)
            ctk.CTkLabel(table, text=what, font=self.f_small, wraplength=260, justify="left").grid(
                row=i, column=1, sticky="w", padx=(0, 12), pady=1)
        table.grid_rowconfigure(len(SHORTCUTS) + 1, minsize=8)
        self._button(top, "Ver en GitHub", lambda: webbrowser.open(REPO_URL), icon("external", 16),
                    height=34).pack()
        self.after(150, top.focus)

    def _apply_theme(self, mode: str):
        self.theme = mode
        ctk.set_appearance_mode(mode)
        if mode == "dark":
            self.sw_theme.select()
        else:
            self.sw_theme.deselect()

    def _open_settings(self):
        top = ctk.CTkToplevel(self)
        top.title("Configuración")
        top.geometry("500x640")
        top.resizable(False, True)
        top.transient(self)
        top.configure(fg_color=C["bg"])
        tabs = ctk.CTkTabview(top, fg_color=C["card"], corner_radius=14, border_width=1,
                                  border_color=C["border"], segmented_button_fg_color=C["card2"],
                                  segmented_button_selected_color=C["accent"],
                                  segmented_button_selected_hover_color=C["accent_hover"],
                                  segmented_button_unselected_color=C["card2"],
                                  segmented_button_unselected_hover_color=C["hover"],
                                  text_color=C["text"])
        tabs.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.settings_tabs = tabs

        def tab(name):
            tabs.add(name)
            container = ctk.CTkScrollableFrame(tabs.tab(name), fg_color="transparent")
            container.pack(fill="both", expand=True)
            return container
        self._tab_general(tab("General"))
        self._tab_downloads(tab("Descargas"))
        self._tab_extras(tab("Letras y álbum"))
        self._tab_advanced(tab("Avanzado"))
        tabs.set("General")
        self.after(150, top.focus)

    # Piezas comunes de la ventana de configuración
    def _cfg_title(self, parent, text, first=False):
        ctk.CTkLabel(parent, text=text, font=self.f_bold).pack(anchor="w", padx=12, pady=(6 if first else 18, 6))

    def _cfg_note(self, parent, text, color=None):
        lbl = ctk.CTkLabel(parent, text=text, font=self.f_small, text_color=color or C["muted"],
                           wraplength=385, justify="left", anchor="w")
        lbl.pack(anchor="w", padx=12, pady=(2, 0), fill="x")
        return lbl

    def _cfg_switch(self, parent, text, key, on_change=None, status="normal"):
        var = tk.BooleanVar(value=bool(self.cfg.get(key, DEFAULT_CONFIG.get(key))))

        def change():
            self.cfg[key] = bool(var.get())
            save_config(self._current_cfg())
            if on_change:
                on_change()
        sw = ctk.CTkSwitch(parent, text=text, variable=var, command=change, font=self.f_normal,
                           progress_color=C["accent"], state=status)
        sw.pack(anchor="w", padx=12, pady=(6, 2))
        return var

    def _cfg_entry(self, parent, width=200, **kw):
        return ctk.CTkEntry(parent, width=width, height=32, corner_radius=10, font=self.f_normal,
                            fg_color=C["input"], border_color=C["border"], text_color=C["text"], **kw)

    def _tab_general(self, t):
        self._cfg_title(t, "Apariencia", first=True)
        seg = ctk.CTkSegmentedButton(
            t, values=["Claro", "Oscuro"], font=self.f_normal, selected_color=C["accent"],
            selected_hover_color=C["accent_hover"], unselected_color=C["card2"],
            unselected_hover_color=C["hover"], text_color=C["text"],
            command=lambda v: self._apply_theme("dark" if v == "Oscuro" else "light"))
        seg.set("Oscuro" if self.theme == "dark" else "Claro")
        seg.pack(anchor="w", padx=12)

        self._cfg_title(t, "Notificaciones y bandeja del sistema")
        has_notifications, has_tray = notifications_available(), tray_available()
        self._cfg_switch(t, "Avisarme cuando terminen las descargas", "notifications",
                              status="normal" if has_notifications else "disabled")
        self._cfg_note(t, "Solo avisa si AUDYAX no está en primer plano (minimizado, en la bandeja "
                          "o detrás de otra ventana)." if has_notifications else
                       "No disponible: falta la librería plyer (pip install plyer).")
        self._cfg_switch(t, "Seguir en segundo plano al cerrar (X)", "keep_in_background",
                              status="normal" if has_tray else "disabled")
        self._cfg_note(t, "Al cerrar la ventana, AUDYAX sigue abierto junto al reloj (y sigue "
                          "descargando). Clic en su icono para volver; para salir del todo: clic "
                          "derecho en el icono → Salir. Minimizar funciona como siempre."
                       if has_tray else "No disponible en este sistema (" + (no_tray_reason() or
                                                                                  "falta pystray") + ").")

        self._cfg_title(t, "Portapapeles")
        self._cfg_switch(t, "Ofrecer pegar los enlaces de YouTube copiados", "detect_clipboard")

        self._cfg_title(t, "Archivos del programa")
        row = ctk.CTkFrame(t, fg_color="transparent")
        row.pack(anchor="w", padx=12)
        self._button(row, "Abrir log", lambda: self._open_program_file("descarga.log"), height=32).pack(
            side="left", padx=(0, 8))
        self._button(row, "Faltantes", lambda: self._open_program_file("faltantes.txt"), height=32).pack(
            side="left")

    def _tab_downloads(self, t):
        self._organize_section(t)

        self._cfg_title(t, "Descargas simultáneas")
        seg_n = ctk.CTkSegmentedButton(
            t, values=[str(i) for i in range(1, MAX_PARALLEL + 1)], font=self.f_normal,
            selected_color=C["accent"], selected_hover_color=C["accent_hover"],
            unselected_color=C["card2"], unselected_hover_color=C["hover"], text_color=C["text"],
            command=lambda v: self._set_parallel(int(v)))
        seg_n.set(str(parallel_downloads(self.cfg)))
        seg_n.pack(anchor="w", padx=12)
        self._cfg_note(t, "Canciones que se bajan a la vez. Con más, YouTube puede frenar las descargas.")

        self._cfg_title(t, "Playlists vigiladas")
        self._cfg_switch(t, "Revisar si tienen canciones nuevas al abrir AUDYAX", "check_watched")
        self._cfg_switch(t, "Bajar las canciones nuevas sin preguntar", "auto_download_new")
        self._cfg_note(t, "Las playlists se agregan desde «Vigiladas» en la barra de la izquierda.")

        self._cfg_title(t, "Actualizaciones")
        if can_self_update():
            self._cfg_note(t, f"AUDYAX {VERSION}  ·  yt-dlp {ytdlp_version()}")
        else:
            self._cfg_note(t, f"AUDYAX {VERSION} (incluye yt-dlp {ytdlp_version()})")
        self._cfg_switch(t, "Avisar al abrir si hay una versión nueva", "check_ytdlp")
        self._button(t, "Buscar actualización", lambda: self._check_ytdlp(manual=True),
                    icon("refresh", 16), height=32).pack(anchor="w", padx=12, pady=(10, 0))

    def _tab_extras(self, t):
        """Letras y datos de MusicBrainz / Cover Art Archive."""
        self._cfg_title(t, "Letras", first=True)
        names = {"off": "No", "lrc": "Archivo .lrc", "embedded": "Dentro del archivo", "both": "Ambas"}
        inverse = {v: k for k, v in names.items()}

        def change_lyrics(v):
            self.cfg["lyrics"] = inverse[v]
            save_config(self._current_cfg())
        seg = ctk.CTkSegmentedButton(
            t, values=list(names.values()), font=self.f_normal, selected_color=C["accent"],
            selected_hover_color=C["accent_hover"], unselected_color=C["card2"],
            unselected_hover_color=C["hover"], text_color=C["text"], command=change_lyrics)
        seg.set(names.get(self.cfg.get("lyrics", "off"), "No"))
        seg.pack(anchor="w", padx=12)
        self._cfg_note(t, "Busca la letra en LRCLIB al terminar cada canción. «Archivo .lrc» la deja "
                          "al lado de la canción (la leen la mayoría de reproductores); «Dentro del "
                          "archivo» la guarda en sus etiquetas. Si hay letra sincronizada, el mini "
                          "reproductor la va mostrando mientras suena.")

        self._cfg_title(t, "Álbum, año y portada")
        self._cfg_switch(t, "Completar álbum y año con MusicBrainz", "musicbrainz")
        self._cfg_switch(t, "Usar la portada del álbum (Cover Art Archive)", "caa_cover")
        self._cfg_note(t, "Solo cuando YouTube no trae el álbum o el año. Si la canción no aparece "
                          "con seguridad en MusicBrainz, se deja como viene. La portada del álbum "
                          "reemplaza la miniatura del video; si no hay, se usa la miniatura.")


    def _tab_advanced(self, t):
        """Opciones avanzadas. Todas vienen apagadas; solo actúan si se encienden."""
        self._cfg_note(t, "Estas opciones vienen apagadas. Actívalas solo si las necesitas.")
        dependents = []  # (interruptor BooleanVar, [widgets que dependen de él])

        def refresh():
            for var, widgets in dependents:
                for w in widgets:
                    w.configure(state="normal" if var.get() else "disabled")
            show_file_row()
            validate_proxy()

        # 1) Límite de velocidad
        self._cfg_title(t, "Límite de velocidad")
        var_speed = self._cfg_switch(t, "Limitar la velocidad de descarga", "speed_limit_enabled",
                                        on_change=refresh)
        row = ctk.CTkFrame(t, fg_color="transparent")
        row.pack(anchor="w", padx=12, pady=(4, 0))
        var_mb = tk.StringVar(value=str(self.cfg.get("speed_limit_mb", 2.0)))
        e_mb = self._cfg_entry(row, 70, textvariable=var_mb)
        e_mb.pack(side="left")
        ctk.CTkLabel(row, text="MB/s en total", font=self.f_normal).pack(side="left", padx=8)
        self._cfg_note(t, "Para que una playlist grande no te deje sin internet para lo demás. "
                          "Se reparte entre las descargas simultáneas.")
        dependents.append((var_speed, [e_mb]))

        def save_mb(*_):
            try:
                value = float(var_mb.get().replace(",", "."))
            except ValueError:
                return
            if value > 0:
                self.cfg["speed_limit_mb"] = value
                save_config(self._current_cfg())
        var_mb.trace_add("write", save_mb)

        # 2) Proxy
        self._cfg_title(t, "Proxy")
        var_px = self._cfg_switch(t, "Usar un proxy", "proxy_enabled", on_change=refresh)
        var_proxy = tk.StringVar(value=self.cfg.get("proxy", ""))
        e_proxy = self._cfg_entry(t, 420, textvariable=var_proxy)
        e_proxy.pack(anchor="w", padx=12, pady=(4, 0))
        proxy_error = ctk.CTkLabel(t, text="", font=self.f_small, text_color=C["red"], wraplength=385,
                                justify="left", anchor="w")
        proxy_help = self._cfg_note(t, "Ejemplo: http://servidor:8080  o  socks5://127.0.0.1:1080. "
                                     "Para redes que bloquean YouTube o videos bloqueados en tu país; se usa "
                                     "en descargas, búsquedas y playlists de YouTube Music.")
        dependents.append((var_px, [e_proxy]))

        def validate_proxy(*_):
            text = var_proxy.get().strip()
            bad = bool(var_px.get()) and text and not VALID_PROXY.match(text)
            error = ("Formato no válido: usa http://, https://, socks4:// o socks5:// seguido de "
                     "servidor:puerto." if bad else
                     ("Escribe la dirección del proxy." if var_px.get() and not text else ""))
            proxy_error.configure(text=error)
            if error:  # el aviso rojo solo ocupa espacio cuando hay algo que decir
                proxy_error.pack(anchor="w", padx=12, pady=(2, 0), fill="x", before=proxy_help)
            else:
                proxy_error.pack_forget()
            self.cfg["proxy"] = text
            save_config(self._current_cfg())
        var_proxy.trace_add("write", validate_proxy)

        # 3) Cookies
        self._cfg_title(t, "Cookies (videos con restricción de edad)")
        var_cookies = self._cfg_switch(t, "Usar mi sesión de YouTube", "cookies_enabled", on_change=refresh)
        by_value = {v: k for k, v in COOKIE_SOURCES.items()}
        var_source = tk.StringVar(value=by_value.get(self.cfg.get("cookies_source", "firefox"),
                                                      "Firefox (recomendado)"))
        cookies_row = ctk.CTkFrame(t, fg_color="transparent")
        cookies_row.pack(anchor="w", padx=12, pady=(4, 0))
        ctk.CTkLabel(cookies_row, text="Tomarla de:", font=self.f_normal).pack(side="left", padx=(0, 8))
        cookies_menu = ctk.CTkOptionMenu(
            cookies_row, variable=var_source, values=list(COOKIE_SOURCES), width=210, height=32,
            font=self.f_normal, dropdown_font=self.f_normal, fg_color=C["input"], button_color=C["card2"],
            button_hover_color=C["hover"], text_color=C["text"], dropdown_fg_color=C["card"],
            dropdown_text_color=C["text"], dropdown_hover_color=C["hover"],
            command=lambda v: (self.cfg.__setitem__("cookies_source", COOKIE_SOURCES[v]),
                               save_config(self._current_cfg()), show_file_row()))
        cookies_menu.pack(side="left")
        file_row = ctk.CTkFrame(t, fg_color="transparent")
        var_file = tk.StringVar(value=self.cfg.get("cookies_file", ""))
        e_file = self._cfg_entry(file_row, 300, textvariable=var_file, placeholder_text="cookies.txt")
        e_file.pack(side="left")

        def choose_file():
            path = filedialog.askopenfilename(parent=t.winfo_toplevel(), title="Elige tu archivo cookies.txt",
                                              filetypes=[("Cookies", "*.txt"), ("Todos", "*.*")])
            if path:
                var_file.set(path)
                self.cfg["cookies_file"] = path
                save_config(self._current_cfg())
        btn_file = self._button(file_row, "Elegir…", choose_file, icon("folder", 16), height=32, width=90)
        btn_file.pack(side="left", padx=8)
        cookies_notice = self._cfg_note(t, "")
        self._cfg_note(t, "Usar tu cuenta con programas de descarga puede hacer que YouTube la limite. "
                          "Si puedes, usa una cuenta secundaria (mayor de edad).", color=STATUS_COLORS["yellow"])
        dependents.append((var_cookies, [cookies_menu, e_file, btn_file]))

        def show_file_row():
            source = self.cfg.get("cookies_source", "firefox")
            if source == "file":
                file_row.pack(anchor="w", padx=12, pady=(6, 0), after=cookies_row)
                cookies_notice.configure(text="Un archivo cookies.txt (formato Netscape) exportado con una "
                                        "extensión del navegador, p. ej. «Get cookies.txt LOCALLY».")
            else:
                file_row.pack_forget()
                cookies_notice.configure(text=(
                    "Firefox funciona bien: basta con haber iniciado sesión en YouTube en Firefox."
                    if source == "firefox" else
                    "Ojo: en Windows, Chrome, Edge y demás navegadores basados en Chromium ya no dejan "
                    "que otros programas lean sus cookies. Si falla, usa Firefox o un archivo cookies.txt."))

        refresh()

    def _organize_section(self, top):
        """Configuración > Organizar descargas (desactivado por defecto: todo junto)."""
        self._cfg_title(top, "Organizar descargas", first=True)
        current = self.cfg.get("organize", "flat")
        if current not in ORGANIZE_OPTIONS:
            current = "flat"
        by_text = {text: key for key, (text, _) in ORGANIZE_OPTIONS.items()}
        var_mode = tk.StringVar(value=ORGANIZE_OPTIONS[current][0])
        var_pl = tk.BooleanVar(value=self.cfg.get("playlists_in_folder", True))
        example = ctk.CTkLabel(top, text="", font=self.f_small, text_color=C["muted"], anchor="w")
        sw_pl = ctk.CTkSwitch(top, text="Las playlists van juntas en su propia carpeta", variable=var_pl,
                              font=self.f_normal, progress_color=C["accent"])

        def apply(*_):
            mode = by_text.get(var_mode.get(), "flat")
            self.cfg["organize"] = mode
            self.cfg["playlists_in_folder"] = bool(var_pl.get())
            example.configure(text="Ejemplo:  " + ORGANIZE_OPTIONS[mode][1])
            sw_pl.configure(state="disabled" if mode == "flat" else "normal")
            save_config(self._current_cfg())

        ctk.CTkOptionMenu(top, variable=var_mode, values=[t for t, _ in ORGANIZE_OPTIONS.values()], width=260,
                          height=32, font=self.f_normal, dropdown_font=self.f_normal, fg_color=C["input"],
                          button_color=C["card2"], button_hover_color=C["hover"], text_color=C["text"],
                          dropdown_fg_color=C["card"], dropdown_text_color=C["text"],
                          dropdown_hover_color=C["hover"], command=apply).pack(anchor="w", padx=12)
        example.pack(anchor="w", padx=12, pady=(6, 6))
        sw_pl.configure(command=apply)
        sw_pl.pack(anchor="w", padx=12)
        apply()

    def _open_program_file(self, name):
        path = Path(self.var_folder.get()) / name
        if path.exists():
            open_path(path)
        else:
            messagebox.showinfo("Archivo", f"Todavía no existe {name} en la carpeta de descargas.")

    def _toggle_detail(self):
        if self.detail_body.winfo_manager():
            self.detail_body.pack_forget()
            self.btn_collapse.configure(text="▸")
        else:
            self.detail_body.pack(fill="x", padx=12, pady=(2, 0), after=self.detail_header)
            self.btn_collapse.configure(text="▾")

    # ---------------------------------------------------------------- acciones
    def _on_format_change(self):
        audio_format = self.var_format.get()
        original = audio_format == "original"
        if original:
            self.var_norm.set(False)
        # En m4a el audio de YouTube (AAC) se copia tal cual, sin recodificar: la calidad
        # solo se aplica si se normaliza (entonces sí hay que recodificar).
        uses_quality = audio_format in ("mp3", "opus") or (audio_format == "m4a" and self.var_norm.get())
        self.cb_quality.configure(state="normal" if uses_quality else "disabled")
        self.chk_norm.configure(state="disabled" if original else "normal")

    def _on_rg_checked(self):
        """ReplayGain y normalizar son excluyentes: marcar una desmarca la otra."""
        if self.var_rg.get():
            self.var_norm.set(False)
        self._on_format_change()

    def _on_norm_checked(self):
        if self.var_norm.get():
            self.var_rg.set(False)
        self._on_format_change()

    def _paste(self):
        try:
            self.var_url.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def _choose_folder(self):
        new_item = filedialog.askdirectory(initialdir=self.var_folder.get() or None,
                                        title="Elige la carpeta donde guardar")
        if new_item:
            self.var_folder.set(os.path.normpath(new_item))

    def _current_cfg(self) -> dict:
        return {
            "folder": self.var_folder.get().strip(),
            "audio_format": self.var_format.get(),
            "quality": self.var_quality.get(),
            "normalize": bool(self.var_norm.get()),
            "crop": bool(self.var_crop.get()),
            "replaygain": bool(self.var_rg.get()) and HAS_MUTAGEN,
            "theme": self.theme,
            "check_ytdlp": bool(self.cfg.get("check_ytdlp", True)),
            "detect_clipboard": bool(self.cfg.get("detect_clipboard", True)),
            "organize": self.cfg.get("organize", "flat"),
            "playlists_in_folder": bool(self.cfg.get("playlists_in_folder", True)),
            "parallel_downloads": parallel_downloads(self.cfg),
            **{k: self.cfg.get(k, DEFAULT_CONFIG[k]) for k in (
                "notifications", "keep_in_background", "speed_limit_enabled", "speed_limit_mb",
                "proxy_enabled", "proxy", "cookies_enabled", "cookies_source", "cookies_file",
                "lyrics", "musicbrainz", "caa_cover", "volume", "check_watched", "auto_download_new")},
            "watched": self.watched,
        }

    def _on_download(self):
        """Botón principal: descarga si es un enlace; si es texto, busca por nombre."""
        text = self.var_url.get().strip()
        if not text:
            messagebox.showinfo("AUDYAX", "Pega un enlace de YouTube o escribe artista y canción.")
            return
        if not text.startswith(("http://", "https://")):
            self._search(text)
            return
        if self._enqueue(text):
            self.var_url.set("")

    def _update_main_button(self):
        text = self.var_url.get().strip()
        if self.searching:
            return
        if text and not text.startswith(("http://", "https://")):
            self.btn_download.configure(text="Buscar", image=self.ico_search)
        else:
            self.btn_download.configure(text="Descargar", image=self.ico_download)

    # ---------------------------------------------------------------- búsqueda
    def _search(self, text: str):
        if self.searching:
            return
        self.searching = True
        self.btn_download.configure(text="Buscando…", state="disabled")

        def job():
            try:
                self.events.put(("search_results", text, search(text, 15, network_options(self.cfg))))
            except Exception as e:
                self.events.put(("search_error", text, str(e)))
        threading.Thread(target=job, daemon=True).start()

    def _on_search_done(self):
        self.searching = False
        self.btn_download.configure(state="normal")
        self._update_main_button()

    def _show_results(self, text, results):
        self._on_search_done()
        if not results:
            messagebox.showinfo("Sin resultados", f"No encontré nada para “{text}”.")
            return
        if self.results_window is not None and self.results_window.winfo_exists():
            self.results_window.destroy()
        self.results_window = SearchResultsWindow(self, text, results)

    def _fetch_result_image(self, vid, url):
        try:
            self.events.put(("result_image", vid, fetch_bytes(url)))
        except Exception:
            pass

    # ---------------------------------------------------------------- portapapeles / soltar
    def _on_focus(self, _event=None):
        # FocusIn llega una vez por cada widget; se agrupa en una sola revisión
        if not self._clipboard_scheduled:
            self._clipboard_scheduled = True
            self.after(200, self._check_clipboard)

    def _check_clipboard(self):
        self._clipboard_scheduled = False
        if not self.cfg.get("detect_clipboard", True):
            return
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            return
        already_listed = any(f["url"] == text for f in self.rows.values())
        if (not is_youtube_link(text) or text == self._last_offered
                or text == self.var_url.get().strip() or already_listed):
            return
        self._last_offered = text
        self.clipboard_notice.show(f"Enlace copiado:  {shorten(text, 60)}", [
            ("Pegar", lambda: self.var_url.set(text), False),
            ("Descargar", lambda: self._enqueue(text), True)])

    def _on_drop(self, event):
        links = extract_links(getattr(event, "data", "") or "")
        if not links:
            messagebox.showinfo("Arrastrar y soltar", "No encontré enlaces de YouTube en lo que soltaste.")
        for url in links:
            self._enqueue(url)
        return getattr(event, "action", None)

    # ---------------------------------------------------------------- yt-dlp
    def _check_ytdlp(self, manual: bool):
        if not can_self_update():
            # En el .exe yt-dlp va dentro del programa: lo que se actualiza es AUDYAX entero
            def app_job():
                try:
                    latest = latest_app_version()
                except Exception as e:
                    if manual:
                        self.events.put(("ytdlp_offline", str(e)))
                    return
                if is_newer(latest, VERSION):
                    self.events.put(("app_new", VERSION, latest))
                elif manual:
                    self.events.put(("app_up_to_date",))
            threading.Thread(target=app_job, daemon=True).start()
            return

        def job():
            current = ytdlp_version()
            try:
                latest = latest_ytdlp_version()
            except Exception as e:
                if manual:
                    self.events.put(("ytdlp_offline", str(e)))
                return
            if is_newer(latest, current):
                self.events.put(("ytdlp_new", current, latest))
            elif manual:
                self.events.put(("ytdlp_up_to_date", current))
        threading.Thread(target=job, daemon=True).start()

    def _offer_update(self, current, latest):
        if can_self_update():
            self.update_notice.show(
                f"Hay una versión nueva de yt-dlp ({latest}; tienes {current}).",
                [("Más tarde", lambda: None, False), ("Actualizar", self._update_ytdlp, True)])
        else:
            self.update_notice.show(
                f"Hay una versión nueva de yt-dlp ({latest}). Descarga la última versión de AUDYAX.",
                [("Ver versiones", lambda: webbrowser.open(REPO_URL + "/releases"), True)])

    def _update_ytdlp(self):
        if self.pending > 0:
            messagebox.showwarning("Actualizar yt-dlp", "Espera a que terminen las descargas en curso.")
            self.update_notice.show("yt-dlp: actualización pendiente.",
                                  [("Actualizar", self._update_ytdlp, True)])
            return
        if self.updating:
            return
        self.updating = True
        self.update_notice.show("Actualizando yt-dlp… (puede tardar un minuto)")

        def job():
            ok, message = update_ytdlp()
            self.events.put(("ytdlp_updated", ok, message))
        threading.Thread(target=job, daemon=True).start()

    def _on_update_done(self, ok, message):
        self.updating = False
        if not ok:
            self.update_notice.hide()
            messagebox.showerror("No se pudo actualizar yt-dlp", message)
            return
        self.update_notice.show("yt-dlp se actualizó. Reinicia AUDYAX para usar la versión nueva.",
                              [("Reiniciar ahora", self._restart, True)])

    def _restart(self):
        if self.pending > 0 and not messagebox.askyesno(
                "Reiniciar", "Hay descargas en curso que se cancelarán. ¿Reiniciar igualmente?"):
            return
        try:
            subprocess.Popen([sys.executable, *sys.argv])
        except OSError as e:
            messagebox.showerror("No se pudo reiniciar", f"{e}\n\nCierra y abre AUDYAX a mano.")
            return
        self._close()

    def _enqueue(self, url: str, subfolder: str = "", extras=None, overrides=None) -> bool:
        cfg = self._current_cfg()
        if not cfg["folder"]:
            messagebox.showwarning("Falta la carpeta", "Elige dónde guardar las canciones.")
            return False
        try:
            Path(cfg["folder"]).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Carpeta no válida", str(e))
            return False
        save_config(cfg)
        cfg["subfolder"] = subfolder  # p. ej. la carpeta de la playlist al reintentar
        cfg["extras"] = extras or {}    # título/artista/álbum a conservar (ver _reintentar)
        cfg.update(overrides or {})       # p. ej. playlist vigilada: su carpeta y solo las nuevas
        if self.pending == 0:
            self.batch = {"ok": 0, "err": 0, "omit": 0}  # empieza una tanda nueva
        self.pending += 1
        self.jobs.put((url, cfg))
        self._ensure_workers()
        self._update_buttons()
        return True

    def _ensure_workers(self):
        """Tantos hilos atendiendo la cola como descargas simultáneas: así varias canciones
        sueltas (p. ej. elegidas en la búsqueda) también se bajan a la vez. El semáforo
        self.limite es el que pone el tope real de canciones descargándose."""
        self.threads = [h for h in self.threads if h.is_alive()]
        while len(self.threads) < self.limit_n:
            h = threading.Thread(target=self._worker_loop, daemon=True)
            h.start()
            self.threads.append(h)

    def _worker_loop(self):
        while True:
            url, cfg = self.jobs.get()
            token = threading.Event()  # cancelación propia de este trabajo
            skip = set()             # canciones de este trabajo canceladas una a una

            def emit(ev, skip=skip):
                if ev[0] == "new":   # esta canción la baja este trabajo
                    self.owners[ev[1]] = skip
                self.events.put(ev)
            with self.tokens_lock:
                self.tokens.add(token)
            try:
                run_job(url, cfg, emit, token, self.pause, self.limit, skip)
            except DownloadCancelled:
                self.events.put(("cancelled",))
            except Exception as e:
                self.events.put(("error", "?", str(e)))
            finally:
                with self.tokens_lock:
                    self.tokens.discard(token)
                self.events.put(("end",))

    def _cancel(self):
        with self.tokens_lock:
            for token in self.tokens:
                token.set()
        try:  # descarta los trabajos que aún no empezaron
            while True:
                self.jobs.get_nowait()
                self.pending -= 1
        except queue.Empty:
            pass
        if self.pause.is_set():
            self._toggle_pause()  # al cancelar, se sale de la pausa
        self._update_buttons()

    def _cancel_songs(self, vids=None):
        """Cancela la canción seleccionada (la de color morado) y, si hay, las marcadas con la
        casilla. No hace falta marcar nada: basta con seleccionarla. Las demás siguen."""
        if vids is None:
            vids = ([self.sel] if self.sel in self.rows else []) + \
                   [v for v in self._checked() if v != self.sel]
        candidates = vids
        valid = [v for v in candidates if v in self.rows and self.rows[v]["color"] == "yellow"]
        if not valid:
            messagebox.showinfo("Cancelar", "Solo se pueden cancelar canciones en cola o descargándose.")
            return
        for vid in valid:
            owner = self.owners.get(vid)
            if owner is not None:
                owner.add(vid)  # el motor la salta o la corta en el próximo aviso de progreso
            f = self.rows[vid]
            f["previous_text"] = ""
            self._row(vid, color="red", text="Cancelada", reason="Cancelada por el usuario")
            f["cancelled"] = True

    def _new_menu(self) -> tk.Menu:
        """Menú emergente con los colores del tema actual (claro u oscuro)."""
        i = 1 if self.theme == "dark" else 0
        return tk.Menu(self, tearoff=0, bg=C["card"][i], fg=C["text"][i], activebackground=C["sel"][i],
                       activeforeground=C["text"][i], disabledforeground=C["muted"][i],
                       relief="flat", borderwidth=1, font=(self.f_normal.cget("family"), 11))

    def _row_menu(self, vid, event):
        """Menú de clic derecho sobre una canción de la lista."""
        if vid not in self.rows:
            return
        self._select(vid)
        f = self.rows[vid]
        menu = self._new_menu()
        if f["color"] == "yellow":
            menu.add_command(label="Cancelar esta canción", command=lambda: self._cancel_songs([vid]))
        if f["color"] == "red" and f["url"]:
            menu.add_command(label="Reintentar", command=self._retry)
        if self._is_playable(f):
            menu.add_command(label="Reproducir", command=lambda: self._play(vid))
        if f["color"] == "green" and f["path"]:
            menu.add_command(label="Editar etiquetas", command=self._edit_tags)
            menu.add_command(label="Abrir carpeta", command=self._open_song_folder)
        if f["url"]:
            if menu.index("end") is not None:
                menu.add_separator()
            menu.add_command(label="Copiar enlace", command=self._copy_link)
            menu.add_command(label="Abrir enlace", command=lambda: self._open_link(vid))
        if menu.index("end") is not None:
            menu.tk_popup(event.x_root, event.y_root)

    def _toggle_pause(self):
        if self.pause.is_set():
            self.pause.clear()
            for vid, f in self.rows.items():
                if f.get("previous_text") and f["text"] == "En pausa":
                    self._row(vid, text=f["previous_text"])
                f["previous_text"] = ""
            self.lbl_pause.pack_forget()
        else:
            self.pause.set()
            # Las que se estaban bajando o procesando muestran "En pausa"
            for vid, f in list(self.rows.items()):
                if f["color"] == "yellow" and f["text"] != "En cola":
                    f["previous_text"] = f["text"]
                    self._row(vid, text="En pausa")
            self.lbl_pause.pack(side="left", padx=(6, 0))
        self._update_buttons()

    def _set_parallel(self, n: int):
        self.cfg["parallel_downloads"] = n
        save_config(self._current_cfg())
        n = parallel_downloads(self.cfg)
        if n != self.limit_n:
            # Los trabajos ya empezados siguen con el límite anterior; los nuevos usan este
            self.limit_n = n
            self.limit = threading.BoundedSemaphore(n)
            if self.pending:
                self._ensure_workers()

    def _update_buttons(self):
        busy = self.pending > 0
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        if self.pause.is_set():
            self.btn_pause.configure(text="Reanudar", image=self.ico_play, state="normal")
        else:
            self.btn_pause.configure(text="Pausar", image=self.ico_pause,
                                     state="normal" if busy else "disabled")

    def _check_ffmpeg(self):
        if not shutil.which("ffmpeg"):
            messagebox.showwarning(
                "Falta FFmpeg",
                "No encuentro FFmpeg en tu equipo. Sin él no se puede convertir el audio, "
                "añadir las portadas ni calcular el ReplayGain.\n\n"
                "Instálalo y añádelo al PATH.")
        if not HAS_MUTAGEN:
            messagebox.showwarning(
                "Falta mutagen",
                "Para escribir el ReplayGain hace falta la librería mutagen.\n\n"
                "Instálala con:  pip install mutagen")

    # ---------------------------------------------------------------- lista
    def _row(self, vid: str, **fields):
        new_item = vid not in self.rows
        if new_item:
            self.rows[vid] = {
                "num": len(self.rows) + 1, "color": "yellow", "text": "En cola", "title": vid,
                "artist": "", "album": "", "year": "", "genre": "", "url": "", "path": "",
                "subfolder": "", "reason": "", "replaygain": "", "thumbnail": "", "img_requested": False,
                "img_s": None, "img_b": None, "img_pil": None, "lyrics": "", "musicbrainz": False, "data": None, "data_requested": False,
                "previous_text": "", "pos": len(self.rows), "check": tk.BooleanVar(value=False)}
            self.rows[vid]["w"] = ListRow(self, self.song_list, vid)
            if self._view_active():
                self.rows[vid]["w"].frame.pack_forget()  # la vista decide si y dónde va
        f = self.rows[vid]
        if self.pause.is_set() and fields.get("text") and f["color"] == "yellow" \
                and fields.get("color", "yellow") == "yellow" and f["text"] == "En pausa" \
                and fields["text"] != "En pausa":
            f["previous_text"] = fields.pop("text")  # en pausa se sigue mostrando "En pausa"
        before = {k: f.get(k) for k in ("color", "title", "artist", "album", "year")}
        for k, v in fields.items():
            if v is not None and k in f and k not in ("w", "check"):
                f[k] = v
        if self._view_active():
            changed = any(f.get(k) != v for k, v in before.items())
            if new_item or (changed and (vid in self._visible) != self._matches(f)) or \
                    (changed and self.order[0] != "num"):
                self._schedule_view()
        elif new_item:
            self._visible.add(vid)
        if f["thumbnail"] and not f["img_requested"]:
            f["img_requested"] = True
            self.pool.submit(self._fetch_image, vid, f["thumbnail"])
        f["w"].refresh_row(f)
        f["w"].paint(vid == self.sel)
        if new_item and self.sel is None:
            self._select(vid)
        elif vid == self.sel:
            self._show_detail()
        self._update_counters()

    def _fetch_image(self, vid, url):
        try:
            self.events.put(("image", vid, fetch_bytes(url)))
        except Exception:
            pass

    def _renumber(self):
        for i, (vid, f) in enumerate(self.rows.items(), 1):
            f["num"] = i
        self._apply_view()

    # ---------------------------------------------------------------- filtrar / ordenar
    def _build_list_bar(self, container):
        bar = ctk.CTkFrame(container, fg_color="transparent")
        bar.pack(fill="x", padx=8, pady=(8, 2))
        self.var_filter = tk.StringVar()
        self.var_status = tk.StringVar(value="Todas")
        self.filter_entry = filter_entry = ctk.CTkEntry(bar, textvariable=self.var_filter, width=280, height=32, corner_radius=10,
                            placeholder_text="Buscar en la lista (título, artista, álbum)…", font=self.f_normal,
                            fg_color=C["input"], border_color=C["border"], text_color=C["text"])
        filter_entry.pack(side="left")
        filter_entry.bind("<Escape>", lambda e: self.var_filter.set(""))
        self.var_filter.trace_add("write", lambda *a: self._schedule_view(250))
        ctk.CTkLabel(bar, text="Estado:", font=self.f_normal).pack(side="left", padx=(16, 6))
        ctk.CTkOptionMenu(bar, variable=self.var_status, values=list(STATUS_FILTERS), width=140,
                          height=32, font=self.f_normal, dropdown_font=self.f_normal, fg_color=C["input"],
                          button_color=C["card2"], button_hover_color=C["hover"], text_color=C["text"],
                          dropdown_fg_color=C["card"], dropdown_text_color=C["text"],
                          dropdown_hover_color=C["hover"],
                          command=lambda v: self._apply_view()).pack(side="left")
        self.lbl_showing = ctk.CTkLabel(bar, text="", font=self.f_small, text_color=C["muted"])
        self.lbl_showing.pack(side="left", padx=14)
        # Controles de la cola, a la derecha: Exportar | Pausar | Cancelar todo
        self.btn_cancel = self._button(bar, "Cancelar todo", self._cancel, icon("cancel", 16),
                                        width=130, height=32, state="disabled")
        self.btn_cancel.pack(side="right")
        self.ico_pause, self.ico_play = icon("pause", 16), icon("play", 16)
        self.btn_pause = self._button(bar, "Pausar", self._toggle_pause, self.ico_pause,
                                     width=112, height=32, state="disabled")
        self.btn_pause.pack(side="right", padx=(0, 8))
        self.btn_export = self._button(bar, "Exportar", self._export_menu, icon("export", 16),
                                        height=32, width=112)
        self.btn_export.pack(side="right", padx=(0, 16))

    def _view_active(self) -> bool:
        """True si hay filtro u orden distinto al de llegada (entonces la lista se re-arma)."""
        return (bool(self.var_filter.get().strip()) or STATUS_FILTERS.get(self.var_status.get())
                is not None or self.order[0] != "num")

    def _matches(self, f) -> bool:
        color = STATUS_FILTERS.get(self.var_status.get())
        if color and f["color"] != color:
            return False
        text = " ".join(str(f.get(k) or "") for k in ("title", "artist", "album")).casefold()
        return all(p in text for p in self.var_filter.get().casefold().split())

    def _rows_in_view(self) -> list:
        """Ids visibles, en el orden en que se muestran."""
        items = [(vid, f) for vid, f in self.rows.items() if self._matches(f)]
        col, desc = self.order
        if col == "num":
            items.sort(key=lambda it: it[1]["num"], reverse=desc)
        elif col == "status":
            items.sort(key=lambda it: (STATUS_ORDER.get(it[1]["color"], 9), it[1]["num"]), reverse=desc)
        else:
            # Las que no tienen ese dato van siempre al final, en orden de llegada
            filled = [it for it in items if str(it[1].get(col) or "").strip()]
            empty_rows = [it for it in items if not str(it[1].get(col) or "").strip()]
            filled.sort(key=lambda it: (str(it[1][col]).casefold(), it[1]["num"]), reverse=desc)
            items = filled + empty_rows
        return [vid for vid, _ in items]

    def _schedule_view(self, ms: int = 300):
        if not self._view_scheduled:
            self._view_scheduled = True
            self.after(ms, self._apply_view)

    def _apply_view(self):
        self._view_scheduled = False
        order = self._rows_in_view()
        for f in self.rows.values():
            f["w"].frame.pack_forget()
        for i, vid in enumerate(order):
            f = self.rows[vid]
            f["pos"] = i
            f["w"].frame.pack(fill="x", pady=1)
            f["w"].paint(vid == self.sel)
        self._visible = set(order)
        is_filtered = len(order) != len(self.rows)
        self.lbl_showing.configure(text=f"Mostrando {len(order)} de {len(self.rows)}" if is_filtered else "")
        self._on_row_checked()

    def _sort_by(self, key: str):
        col, desc = self.order
        if col != key:
            self.order = (key, False)
        elif not desc:
            self.order = (key, True)
        else:
            self.order = ("num", False)  # tercer clic: vuelve al orden de llegada
        for c, (lbl, text) in self.headers.items():
            arrow = (" ▼" if self.order[1] else " ▲") if c == self.order[0] else ""
            lbl.configure(text=text + arrow)
        self._apply_view()

    # ---------------------------------------------------------------- exportar
    def _export_menu(self):
        """Botón Exportar: ventanita con las dos opciones. (Antes era un menú emergente de
        Windows, que en algunos equipos no llegaba a aparecer.)"""
        if self.export_window is not None and self.export_window.winfo_exists():
            self.export_window.lift()
            self.export_window.focus()
            return
        self.export_window = ExportWindow(self)

    def _export_m3u(self):
        songs = [self.rows[v] for v in self._rows_in_view()
                     if self.rows[v]["color"] == "green" and self.rows[v]["path"]
                     and Path(self.rows[v]["path"]).exists()]
        if not songs:
            messagebox.showinfo("Exportar", "No hay canciones descargadas en esta sesión (con el filtro actual).")
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Guardar lista de reproducción", defaultextension=".m3u",
            initialdir=self.var_folder.get() or None, initialfile="AUDYAX.m3u",
            filetypes=[("Lista M3U", "*.m3u"), ("Lista M3U (UTF-8)", "*.m3u8")])
        if not path:
            return
        try:
            n = export_m3u(path, songs)
        except OSError as e:
            messagebox.showerror("No se pudo exportar", str(e))
            return
        messagebox.showinfo("Exportar", f"Lista guardada con {n} {'canción' if n == 1 else 'canciones'}:\n{path}")

    def _export_csv(self):
        order = self._rows_in_view()
        if not order:
            messagebox.showinfo("Exportar", "La lista está vacía.")
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Guardar la lista como CSV", defaultextension=".csv",
            initialdir=self.var_folder.get() or None, initialfile="AUDYAX.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        rows = [dict(self.rows[v], num=i, status=self.rows[v]["text"]) for i, v in enumerate(order, 1)]
        try:
            n = export_csv(path, rows)
        except OSError as e:
            messagebox.showerror("No se pudo exportar", str(e))
            return
        messagebox.showinfo("Exportar", f"Se exportaron {n} filas:\n{path}")

    def _update_counters(self):
        counts = {"green": 0, "yellow": 0, "red": 0}
        for f in self.rows.values():
            counts[f["color"]] += 1
        for name, (lbl, label) in self.counters.items():
            lbl.configure(text=f"  {counts[name]} {label}")
        total = len(self.rows)
        finished_count = counts["green"] + counts["red"]
        pct = int(finished_count * 100 / total) if total else 0
        self.progress.set(pct / 100)
        self.lbl_pct.configure(text=f"{pct}%")

    def _process_events(self):
        try:
            for _ in range(30):  # por tandas, para que la ventana no se congele con playlists grandes
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(30 if not self.events.empty() else 100, self._process_events)

    def _handle(self, ev):
        kind = ev[0]
        cancelled = kind in ("info", "status", "error", "rg") and self.rows.get(
            ev[1] if len(ev) > 1 else None, {}).get("cancelled")
        if cancelled:
            return  # avisos atrasados de una canción que el usuario canceló
        if kind == "new":
            if ev[1] in self.rows:  # reintento: se vuelven a leer los datos del archivo
                self.rows[ev[1]].update(data=None, data_requested=False, cancelled=False)
            self._row(ev[1], color="yellow", text="En cola", reason="", path="", **ev[2])
        elif kind == "info":
            self._row(ev[1], **ev[2])
        elif kind == "status":
            if self.rows.get(ev[1], {}).get("color") != "red":
                self._row(ev[1], color="yellow", text=ev[2])
        elif kind == "done":
            self.batch["ok"] += 1
            if ev[1] in self.rows:
                self.rows[ev[1]]["cancelled"] = False  # terminó justo antes de cancelarla
            self._row(ev[1], color="green", text="Descargada", reason="", **ev[2])
        elif kind == "rg":
            self._row(ev[1], replaygain=ev[2])
        elif kind == "skipped":
            self.batch["omit"] += 1
            self._row(ev[1], color="green", text="Ya descargada antes")
        elif kind == "error":
            self.batch["err"] += 1
            vid, reason = ev[1], ev[2]
            if vid == "?":
                self.general_errors += 1
                vid = f"error-{self.general_errors}"
                self._row(vid, title="Error general", color="red", text="Error", reason=reason)
            else:
                self._row(vid, color="red", text="Error: " + reason[:60], reason=reason)
        elif kind == "cancelled":
            for vid, f in list(self.rows.items()):
                if f["color"] == "yellow":
                    self._row(vid, color="red", text="Cancelada", reason="Cancelada por el usuario")
        elif kind == "image":
            self._set_image(ev[1], ev[2])
        elif kind == "data":
            f = self.rows.get(ev[1])
            if f and ev[2]:
                f["data"] = ev[2]
                if self.sel == ev[1]:
                    self._show_detail()
        elif kind == "playlist":
            self._on_playlist_downloaded(ev[1], ev[2])
        elif kind == "watched_checked":
            self._on_watched_checked(ev[1], ev[2], ev[3], ev[4])
        elif kind == "end":
            self.pending = max(0, self.pending - 1)
            if self.pending == 0 and self.pause.is_set():
                self._toggle_pause()  # no queda nada en pausa
            if self.pending == 0:
                self._on_batch_done()
            self._update_buttons()
        elif kind == "search_results":
            self._show_results(ev[1], ev[2])
        elif kind == "search_error":
            self._on_search_done()
            messagebox.showerror("No se pudo buscar", shorten(ev[2], 300))
        elif kind == "result_image":
            v = self.results_window
            if v is not None and v.winfo_exists():
                v.set_image(ev[1], ev[2])
        elif kind == "ytdlp_new":
            self._offer_update(ev[1], ev[2])
        elif kind == "app_new":
            self.update_notice.show(f"Hay una versión nueva de AUDYAX ({ev[2]}; tienes {ev[1]}).",
                                  [("Más tarde", lambda: None, False),
                                   ("Descargar", lambda: webbrowser.open(REPO_URL + "/releases/latest"), True)])
        elif kind == "app_up_to_date":
            messagebox.showinfo("AUDYAX", f"Ya tienes la última versión ({VERSION}).")
        elif kind == "tray":
            self._tray_action(ev[1])
        elif kind == "ytdlp_up_to_date":
            messagebox.showinfo("yt-dlp", f"Ya tienes la última versión ({ev[1]}).")
        elif kind == "ytdlp_offline":
            messagebox.showwarning("yt-dlp", "No se pudo consultar la última versión:\n" + shorten(ev[1], 200))
        elif kind == "ytdlp_updated":
            self._on_update_done(ev[1], ev[2])

    def _set_image(self, vid, data):
        f = self.rows.get(vid)
        if not f:
            return
        try:
            img = Image.open(BytesIO(data))
            img.load()
            f["img_pil"] = img
            f["img_s"] = square_image(img, 40, 8)
            f["img_b"] = square_image(img, 88, 12)
        except Exception:
            return
        f["w"].refresh_row(f)
        if self.sel == vid:
            self._show_detail()

    # ---------------------------------------------------------------- selección
    def _select(self, vid):
        previous = self.sel
        self.sel = vid
        if previous in self.rows:
            self.rows[previous]["w"].paint(False)
        if vid in self.rows:
            self.rows[vid]["w"].paint(True)
        self._show_detail()

    def _on_row_checked(self):
        visible_ids = [self.rows[v] for v in self._visible if v in self.rows]
        self.var_all.set(bool(visible_ids) and all(f["check"].get() for f in visible_ids))

    def _check_all(self):
        """Marca/desmarca solo las filas que se ven (respeta el filtro)."""
        value = self.var_all.get()
        for vid in self._visible:
            if vid in self.rows:
                self.rows[vid]["check"].set(value)

    def _checked(self):
        return [vid for vid, f in self.rows.items() if f["check"].get()]

    def _clear_list(self):
        # Sin marcadas: se quitan las que se ven (p. ej. filtrando "Con error" se limpian los errores)
        self._remove_rows(self._checked() or [v for v in self.rows if v in self._visible])

    def _remove_rows(self, target):
        """Quita filas de la lista (no borra archivos). Las que están en cola o bajándose se
        dejan: primero hay que cancelarlas."""
        in_progress = [v for v in target if v in self.rows and self.rows[v]["color"] == "yellow"]
        if target and len(in_progress) == len(target):
            messagebox.showinfo("Quitar de la lista",
                                "Las canciones en cola o descargándose no se quitan: cancélalas primero (✕).")
            return
        for vid in target:
            f = self.rows.get(vid)
            if f is None or f["color"] == "yellow":
                continue
            f["w"].destroy()
            release_variable(f["check"])
            del self.rows[vid]
            if self.sel == vid:
                self.sel = None
        if self.sel is None and self.rows:
            self.sel = next(iter(self.rows))
        self.var_all.set(False)
        self._renumber()
        self._show_detail()
        self._update_counters()

    # ---------------------------------------------------------------- detalle
    def _show_detail(self):
        vid = self.sel if self.sel in self.rows else None
        if not vid:
            self.l_detail_title.configure(text="Selecciona una canción de la lista.")
            self.l_detail_artist.configure(text="")
            self.l_chip.pack_forget()
            self.l_detail_info.configure(text="")
            self.l_cover.configure(image=self.img_empty_big)
            for lbl in self.l_info.values():
                lbl.configure(text="—")
            return
        f = self.rows[vid]
        self.l_detail_title.configure(text=shorten(f["title"], 30))
        self.l_detail_artist.configure(text=shorten(f["artist"] or "—", 40))
        chip = " • ".join(p for p in (f["year"], f["genre"]) if p)
        if chip:
            self.l_chip.configure(text=f"  {tk_safe(chip)}  ")
            self.l_chip.pack(anchor="w", pady=4, before=self.l_detail_info)
        else:
            self.l_chip.pack_forget()
        path = f["path"]
        if f["color"] == "red":
            self.l_detail_info.configure(text=shorten(f["reason"] or f["text"], 60), text_color=C["red"])
        elif f["color"] == "green" and path:
            extra = [{"synced": "letra sincronizada", "text": "lyrics"}.get(f["lyrics"], "")]
            extra.append("álbum de MusicBrainz" if f["musicbrainz"] else "")
            text = "  ·  ".join([shorten(Path(path).name, 50)] + [e for e in extra if e])
            self.l_detail_info.configure(text=text, text_color=C["muted"])
        else:
            self.l_detail_info.configure(text=shorten(f["text"], 60), text_color=C["muted"])
        self.l_cover.configure(image=f["img_b"] or self.img_empty_big)

        d = f["data"] or {}
        rg = f["replaygain"].split(" · ")[0].replace("pista ", "") if f["replaygain"] else ""
        values = {k: d.get(k) or "—" for k in ("audio_format", "duration", "bitrate", "size", "sample_rate")}
        values["rg"] = rg or "—"
        for key, text in values.items():
            self.l_info[key].configure(text=text)
        if (f["data"] is None and f["color"] == "green" and path and not f["data_requested"]
                and Path(path).exists()):
            f["data_requested"] = True
            threading.Thread(target=self._read_file_info, args=(vid, path), daemon=True).start()

    def _read_file_info(self, vid, path):
        try:
            self.events.put(("data", vid, file_info(path)))
        except Exception:
            pass

    def _link_of(self, vid=None):
        vid = vid or self.sel
        return self.rows[vid]["url"] if vid in self.rows else ""

    def _copy_link(self):
        url = self._link_of()
        if url:
            self.clipboard_clear()
            self.clipboard_append(url)

    def _open_link(self, vid=None):
        url = self._link_of(vid)
        if url:
            webbrowser.open(url)

    # ---------------------------------------------------------------- mini reproductor
    def _is_playable(self, f) -> bool:
        return f["color"] == "green" and bool(f["path"]) and Path(f["path"]).exists()

    def _on_double_click(self, vid):
        """Doble clic: si ya está descargada, suena; si no, se abre su enlace."""
        f = self.rows.get(vid)
        if f and self._is_playable(f):
            self._play(vid)
        else:
            self._open_link(vid)

    def _player_item(self, vid) -> dict:
        f = self.rows[vid]
        return {"id": vid, "path": f["path"], "title": f["title"], "artist": f["artist"],
                "image": f["img_pil"]}

    def _neighbor(self, item, step):
        """La canción descargada anterior/siguiente según el orden en que se ve la lista."""
        ids = [v for v in self._rows_in_view() if self._is_playable(self.rows[v])]
        if not ids:
            return None
        i = ids.index(item["id"]) if item["id"] in ids else (-1 if step > 0 else len(ids))
        j = i + step
        return self._player_item(ids[j]) if 0 <= j < len(ids) else None

    def _play(self, vid):
        if self.player is None:
            colors = {k: C[k] for k in ("card", "text", "muted", "accent", "accent_hover",
                                         "on_accent", "hover", "border")}
            fonts = {"bold": self.f_bold, "normal": self.f_normal, "small": self.f_small}
            self.player = player.PlayerBar(
                self.main, colors, fonts, neighbor=self._neighbor,
                on_volume_change=lambda v: self.cfg.__setitem__("volume", round(v, 2)),
                on_close=lambda: self.player.grid_remove(),
                volume=float(self.cfg.get("volume", 0.8)))
        try:
            self.player.play(self._player_item(vid))
        except RuntimeError as e:
            messagebox.showwarning("No se puede reproducir", f"No se pudo reproducir la canción: {e}.")
            return
        self.player.grid(row=5, column=0, sticky="ew", pady=(10, 0))

    # ---------------------------------------------------------------- playlists vigiladas
    def _open_watched(self):
        v = self.watched_window
        if v is not None and v.winfo_exists():
            v.focus()
            return
        self.watched_window = WatchedWindow(self)

    def _refresh_watched(self):
        v = self.watched_window
        if v is not None and v.winfo_exists():
            v.refresh()

    def _watch(self, url: str, name: str = "") -> bool:
        folder = self.var_folder.get().strip()
        if not folder:
            messagebox.showwarning("Falta la carpeta", "Elige primero dónde guardar las canciones.")
            return False
        try:
            v = watched.add(self.watched, url, folder, name)
        except ValueError:
            messagebox.showwarning("No es una playlist", "Pega el enlace de una playlist o un álbum de "
                                                         "YouTube o YouTube Music.")
            return False
        save_config(self._current_cfg())
        self._refresh_watched()
        self._check_watched(v)
        return True

    def _unwatch(self, url: str):
        watched.remove(self.watched, url)
        self.watched_new.pop(url, None)
        save_config(self._current_cfg())
        self._refresh_watched()

    def _check_watched(self, v, on_open=False):
        if v["url"] in self.checking:
            return
        self.checking.add(v["url"])
        snapshot, net = dict(v), network_options(self._current_cfg())

        def job():
            try:
                new_songs = watched.check(snapshot, net)
            except Exception as e:  # no debería pasar: revisar ya atrapa los errores
                snapshot["error"], new_songs = str(e), []
            self.events.put(("watched_checked", v["url"], snapshot, new_songs, on_open))
        threading.Thread(target=job, daemon=True).start()
        self._refresh_watched()

    def _check_all_watched(self, on_open=False):
        for v in list(self.watched):
            self._check_watched(v, on_open)

    def _on_watched_checked(self, url, snapshot, new_songs, on_open):
        self.checking.discard(url)
        for v in self.watched:
            if v["url"] == url:
                v.update(snapshot)
                break
        else:
            return  # la quitaron mientras se revisaba
        self.watched_new[url] = new_songs
        save_config(self._current_cfg())
        self._refresh_watched()
        if on_open and not self.checking:  # terminó la revisión de todas: un solo aviso
            with_new = [v for v in self.watched if self.watched_new.get(v["url"])]
            if not with_new:
                return
            if self.cfg.get("auto_download_new"):
                for v in with_new:
                    self._download_new(v["url"])
                return
            total = sum(len(self.watched_new[v["url"]]) for v in with_new)
            where = (f"«{with_new[0].get('name') or 'tu playlist'}»" if len(with_new) == 1
                     else f"{len(with_new)} playlists vigiladas")
            self.watched_notice.show(
                f"Hay {total} canción{'es' if total != 1 else ''} nueva{'s' if total != 1 else ''} en {where}.",
                [("Ver", self._open_watched, False),
                 ("Bajar nuevas", lambda: [self._download_new(v["url"]) for v in with_new], True)])

    def _download_new(self, url: str):
        v = next((x for x in self.watched if x["url"] == url), None)
        if v is None:
            return
        if self._enqueue(v["url"], overrides={"folder": v["folder"], "only_new": True}):
            self.watched_new[url] = []
            v["new_count"] = 0
            save_config(self._current_cfg())
            self._refresh_watched()

    def _on_playlist_downloaded(self, url, title):
        """Se bajó una playlist: ofrecer vigilarla (una vez por sesión y si aún no lo está)."""
        self.last_playlist = url
        canonical = watched.playlist_url(url) if watched.is_playlist(url) else ""
        if not canonical or canonical in self._offered_watch or any(v["url"] == canonical for v in self.watched):
            return
        self._offered_watch.add(canonical)
        name = title or "esta playlist"
        self.watched_notice.show(f"¿Vigilar «{name}»? AUDYAX te avisará cuando tenga canciones nuevas.",
                               [("No", lambda: None, False),
                                ("Vigilar", lambda: self._watch(canonical, title), True)])

    def _open_song_folder(self):
        path = self.rows[self.sel]["path"] if self.sel in self.rows else ""
        if path and Path(path).exists():
            open_path(Path(path).parent)
        else:
            self._open_base_folder()

    def _open_base_folder(self):
        folder = Path(self.var_folder.get())
        if folder.exists():
            open_path(folder)
        else:
            messagebox.showinfo("Carpeta", "Esa carpeta todavía no existe.")

    def _edit_tags(self):
        f = self.rows.get(self.sel)
        path = f["path"] if f else ""
        if not f or f["color"] != "green" or not path or not Path(path).exists():
            messagebox.showinfo("Editar etiquetas", "Selecciona una canción ya descargada en esta sesión.")
            return
        if not is_editable(path):
            messagebox.showinfo("Editar etiquetas",
                                "Solo se pueden editar archivos MP3, M4A y Opus (hace falta mutagen).")
            return
        try:
            TagEditorWindow(self, self.sel, path)
        except Exception as e:
            messagebox.showerror("No se pudo leer el archivo", str(e))

    def _on_tags_saved(self, vid, data, cover, path=None):
        f = self.rows.get(vid)
        if not f:
            return
        f.update(data=None, data_requested=False)  # el tamaño del archivo cambia
        self._row(vid, **data, **({"path": path} if path else {}))
        if cover:
            self._set_image(vid, cover)

    def _retry(self):
        candidates = self._checked() or ([self.sel] if self.sel in self.rows else [])
        valid = [v for v in candidates if self.rows[v]["color"] == "red" and self.rows[v]["url"]]
        if not valid:
            messagebox.showinfo("Reintentar", "Solo se pueden reintentar canciones con error que tengan enlace.")
            return
        for vid in valid:
            f = self.rows[vid]
            # Si venía de una playlist de YouTube Music se conservan su título, artista y álbum
            # (al bajarla sola, yt-dlp pondría los del video: "(Official Video)", "ArtistaVEVO"…)
            extras = None
            if f["url"].startswith("https://music.youtube.com/"):
                extras = {vid: {k: f[k] for k in ("title", "artist", "album")}}
            # Se reintenta en la misma subcarpeta (la de su playlist, si venía de una)
            self._enqueue(f["url"], f["subfolder"], extras)

    # ---------------------------------------------------------------- icono, bandeja y avisos
    def _set_window_icon(self):
        try:
            if sys.platform.startswith("win") and resource_path("audyax.ico").exists():
                self.iconbitmap(default=str(resource_path("audyax.ico")))
            elif resource_path("audyax.png").exists():
                self._tk_icon = tk.PhotoImage(file=str(resource_path("audyax.png")))
                self.iconphoto(True, self._tk_icon)
        except tk.TclError:
            pass

    def _tray_image(self) -> Image.Image:
        try:
            return Image.open(resource_path("audyax.png")).convert("RGBA")
        except OSError:
            return _draw_icon("note", "#a78bfa", 64)

    def _window_active(self) -> bool:
        """True si el usuario está mirando AUDYAX (para no notificar en ese caso)."""
        try:
            return self.state() == "normal" and self.focus_displayof() is not None
        except (tk.TclError, KeyError):
            return False

    def _on_batch_done(self):
        """Terminó todo lo que había en cola: notificación (si está activada y no se está mirando)."""
        ok, err, omit = self.batch["ok"], self.batch["err"], self.batch["omit"]
        self.batch = {"ok": 0, "err": 0, "omit": 0}
        self._tray_text()
        if not self.cfg.get("notifications", True) or ok + err + omit == 0 or self._window_active():
            return
        title = "AUDYAX: descargas terminadas" if not err else "AUDYAX: terminó con errores"
        message = batch_summary(ok, err, omit)
        if not self.tray.notify(title, message):
            notify(title, message, str(resource_path("audyax.ico")))

    def _tray_text(self):
        if self.tray.visible:
            in_progress = sum(1 for f in self.rows.values() if f["color"] == "yellow")
            self.tray.update_text(f"AUDYAX — {in_progress} en proceso" if in_progress else "AUDYAX")

    def _to_tray(self) -> bool:
        """Esconde la ventana y deja AUDYAX como icono junto al reloj."""
        if not self.tray.show("AUDYAX"):
            return False
        self.withdraw()
        self._tray_text()
        return True

    def _from_tray(self):
        self.tray.hide()
        self.deiconify()
        self.state("normal")
        self.lift()
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _tray_action(self, action: str):
        if action == "open":
            self._from_tray()
        elif action == "pause":
            if self.pending or self.pause.is_set():
                self._toggle_pause()
        elif action == "quit":
            if self.pending:
                self._from_tray()
                if not messagebox.askyesno("Salir", "Hay descargas en curso. ¿Cancelarlas y cerrar AUDYAX?"):
                    return
            self._close()

    # ---------------------------------------------------------------- atajos de teclado
    def _setup_shortcuts(self):
        # En el cuadro de enlace, Ctrl+V va primero aquí (antes del pegado normal del cuadro)
        for keystroke in ("<Control-v>", "<Control-V>"):
            self.entry._entry.bind(keystroke, lambda e: self._shortcut_paste(in_entry=True), add=True)
            self.bind(keystroke, lambda e: self._shortcut_paste())
        self.bind("<Delete>", self._shortcut_delete)
        for keystroke in ("<Control-f>", "<Control-F>"):
            self.bind(keystroke, self._shortcut_search)
        if sys.platform == "darwin":
            self.entry._entry.bind("<Command-v>", lambda e: self._shortcut_paste(in_entry=True), add=True)
            self.bind("<Command-v>", lambda e: self._shortcut_paste())
            self.bind("<Command-f>", self._shortcut_search)

    def _focus_in_text(self) -> bool:
        """True si el cursor está en un cuadro de texto (ahí las teclas hacen lo normal)."""
        try:
            w = self.focus_get()
        except (KeyError, tk.TclError):
            return False
        return w is not None and w.winfo_class() in ("Entry", "TEntry", "Text")

    def _shortcut_paste(self, in_entry: bool = False):
        """Ctrl+V: si lo copiado es un enlace, se descarga directamente.
        - Escribiendo en otro cuadro (búsqueda de la lista, configuración…): pegado normal.
        - En el cuadro de enlace con algo ya escrito: pegado normal (se está editando).
        - Si lo copiado es texto (no un enlace): se pone en el cuadro para buscarlo con Enter."""
        if not in_entry and self._focus_in_text():
            return None
        if in_entry and self.var_url.get().strip():
            return None
        try:
            text = self.clipboard_get().strip()
        except tk.TclError:
            return None
        links = extract_links(text) or ([text] if re.match(r"^https?://\S+$", text) else [])
        if links:
            for url in links:
                self._enqueue(url)
            self._last_offered = text
            self.clipboard_notice.hide()
            return "break"
        if in_entry or not text:
            return None
        self.var_url.set(text[:300])
        self.entry.focus_set()
        self.entry._entry.icursor("end")
        return "break"

    def _shortcut_delete(self, _event=None):
        """Supr: quita de la lista las marcadas o, si no hay, la seleccionada."""
        if self._focus_in_text():
            return None
        target = self._checked() or ([self.sel] if self.sel in self.rows else [])
        if target:
            self._remove_rows(target)
        return "break"

    def _shortcut_search(self, _event=None):
        self.filter_entry.focus_set()
        self.filter_entry._entry.select_range(0, "end")
        return "break"

    # ---------------------------------------------------------------- cierre
    def _on_window_close(self):
        """Botón X. Con "Seguir en segundo plano" (activado por defecto), AUDYAX no se cierra:
        se esconde junto al reloj y sigue descargando. Para salir del todo: icono → Salir.
        Sin esa opción (o sin bandeja en el sistema) se cierra, preguntando si hay descargas."""
        if self.cfg.get("keep_in_background", True) and tray_available() and self._to_tray():
            if not self._background_notice_shown:
                self._background_notice_shown = True  # se explica solo la primera vez
                title = "AUDYAX sigue en segundo plano"
                message = "Está junto al reloj. Para salir del todo: clic derecho en su icono → Salir."
                if self.cfg.get("notifications", True) and not self.tray.notify(title, message):
                    notify(title, message, str(resource_path("audyax.ico")))
            return
        if self.pending > 0 and not messagebox.askyesno(
                "Descargas en curso", "Hay descargas en curso. ¿Cancelarlas y cerrar AUDYAX?"):
            return
        self._close()

    def _close(self):
        if self.player is not None:
            self.player.engine.stop()
        save_config(self._current_cfg())
        self._cancel()
        self.tray.hide()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def self_test(app: "App", output: str):
    """--prueba-arranque: comprueba que el programa (sobre todo el .exe) trae todo lo que
    necesita y que FFmpeg funciona, escribe el resultado en JSON y se cierra.
    La usa GitHub Actions para probar el .exe en Windows."""
    import tempfile

    from engine import HAS_YTMUSIC, analyze_loudness
    r = {"version": VERSION, "ytdlp": ytdlp_version(), "executable": bool(getattr(sys, "frozen", False)),
         "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
         "mutagen": HAS_MUTAGEN, "ytmusicapi": HAS_YTMUSIC, "drag_and_drop": app.has_dnd,
         "tray": tray_available(), "no_tray_reason": no_tray_reason(),
         "notifications": notifications_available(), "icon": resource_path("audyax.ico").exists()}
    try:  # FFmpeg de verdad: genera un tono y mide su volumen
        with tempfile.TemporaryDirectory() as tmp:
            tone = Path(tmp) / "tono.mp3"
            subprocess.run([r["ffmpeg"] or "ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                            "sine=duration=1", "-c:a", "libmp3lame", str(tone)], check=True, timeout=60,
                           **({"creationflags": subprocess.CREATE_NO_WINDOW}
                              if sys.platform.startswith("win") else {}))
            r["ffmpeg_works"] = analyze_loudness(tone) is not None
    except Exception as e:
        r["ffmpeg_works"] = False
        r["ffmpeg_error"] = str(e)
    try:
        r["tray_shown"] = app.tray.show("AUDYAX (prueba)")
        app.tray.hide()
    except Exception as e:
        r["tray_shown"] = False
        r["tray_error"] = str(e)
    try:  # la librería de audio tiene que venir en el .exe; la tarjeta de sonido puede no estar (CI)
        import numpy  # noqa: F401
        import sounddevice  # noqa: F401
        r["audio_library"] = True
    except Exception as e:
        r["audio_library"], r["audio_library_error"] = False, str(e)
    r["audio"], r["audio_reason"] = player.audio_available()  # informativo
    Path(output).write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    app.destroy()


def main():
    setup_bundled_ffmpeg()  # el .exe trae su propio FFmpeg
    if "--prueba-arranque" in sys.argv:
        i = sys.argv.index("--prueba-arranque")
        output = sys.argv[i + 1] if len(sys.argv) > i + 1 else "prueba-arranque.json"
        app = App()
        app.after(1500, lambda: self_test(app, output))
        app.mainloop()
    else:
        App().mainloop()
    # Al cerrar la ventana, terminar del todo: alguna librería (p. ej. la de la bandeja) puede
    # dejar un hilo vivo que mantendría el proceso abierto sin ventana.
    logging.shutdown()
    os._exit(0)


if __name__ == "__main__":
    main()