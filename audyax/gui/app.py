"""
AUDYAX - music downloader for YouTube / YouTube Music (graphical interface: customtkinter + Pillow).

Main window. The download work lives in audyax.downloader and the player in audyax.player;
the other audyax.gui modules hold the rest of the window (list, detail, settings…).

Requirements:
    pip install -U yt-dlp mutagen customtkinter pillow tkinterdnd2
    ffmpeg installed and in the PATH
    (tkinter comes with Python; on Linux: sudo apt install python3-tk)

Usage:  python main.py
"""

import json
import logging
import logging.handlers
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from audyax import VERSION
from audyax.core.config import DEFAULT_CONFIG, load_config, parallel_downloads, save_config
from audyax.core.lyrics import HAS_MUTAGEN
from audyax.core.metadata import search
from audyax.core.system import setup_bundled_ffmpeg
from audyax.downloader.engine import DownloadCancelled, run_job
from audyax.downloader.network import network_options
from audyax.downloader.updates import (can_self_update, is_newer, latest_app_version, latest_ytdlp_version,
                                       update_ytdlp, ytdlp_version)
from audyax.downloader.youtube import extract_links, is_youtube_link
from audyax.gui.desktop import Tray, batch_summary, no_tray_reason, notifications_available, notify, tray_available
from audyax.gui.detail import DetailMixin
from audyax.gui.dialogs import SearchResultsWindow
from audyax.gui.player_panel import PlayerMixin
from audyax.gui.settings import SettingsMixin
from audyax.gui.song_list import COLUMN_WIDTHS, SongListMixin, setup_columns
from audyax.gui.style import (STATUS_COLORS, C, _draw_icon, choose_font, empty_image, equalizer_image, fetch_bytes,
                              icon, logo, resource_path, shorten)
from audyax.gui.watched_window import WatchedMixin
from audyax.gui.widgets import Notice
from audyax.player.engine import audio_available
from audyax.player.queue import PlayQueue

# Drag and drop: optional (pip install tkinterdnd2). The app works the same without it.
try:
    from tkinterdnd2 import DND_TEXT, TkinterDnD
    _DND_BASES = (TkinterDnD.DnDWrapper,)
except ImportError:
    DND_TEXT, TkinterDnD, _DND_BASES = None, None, ()


REPO_URL = "https://github.com/luxkun65/AUDYAX"


# Keyboard shortcuts (also shown in "About")
SHORTCUTS = [("Ctrl+V", "Pegar un enlace copiado y descargarlo"),
          ("Supr", "Quitar de la lista las canciones seleccionadas (no borra archivos)"),
          ("Ctrl+F", "Buscar en la lista"),
          ("Enter", "Descargar o buscar lo escrito en el cuadro de enlace")]


class App(SettingsMixin, SongListMixin, DetailMixin, PlayerMixin, WatchedMixin, ctk.CTk, *_DND_BASES):
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
            except Exception:  # tkdnd not available on this system: no drag and drop
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

        self.events = queue.Queue()   # worker -> UI
        self.jobs = queue.Queue()  # UI -> worker
        self.pause = threading.Event()        # set = downloads paused
        self.owners = {}                      # song id -> 'skip' set of its job
        self.tokens = set()                   # one cancellation Event per running job
        self.tokens_lock = threading.Lock()
        self.threads = []                       # threads serving the job queue
        self.limit = threading.BoundedSemaphore(parallel_downloads(self.cfg))
        self.limit_n = parallel_downloads(self.cfg)
        self.pool = ThreadPoolExecutor(max_workers=4)  # thumbnail downloads
        self.pending = 0
        self.general_errors = 0
        self.rows = {}   # id -> song data
        self.sel = None   # id of the song shown in the detail panel
        self.order = ("num", False)   # (column, descending); "num" = arrival order
        self._visible = set()        # ids that pass the current filter
        self._view_scheduled = False
        self.results_window = None
        self.export_window = None
        self.watched_window = None
        self.player = None          # mini player bar (created on first use)
        self.player_view = None     # player (part 2), shown instead of the downloader
        self.now_playing = None     # player's "Reproduciendo" section
        self.player_section = "Canciones"
        self.play_queue = PlayQueue(shuffle=bool(self.cfg.get("shuffle")), repeat=self.cfg.get("repeat", "off"))
        self.view_mode = "downloads"   # "downloads" | "player" (double click on the logo switches)
        self.watched = [dict(v) for v in self.cfg.get("watched") or []]
        self.cfg["watched"] = self.watched
        self.checking = set()           # urls of watched playlists being checked
        self.watched_new = {}             # url -> new songs found in the last check
        self._offered_watch = set()      # playlists that were already offered for watching
        self.batch = {"ok": 0, "err": 0, "omit": 0}  # result of the current batch (notification)
        self._background_notice_shown = False
        self.tray = Tray(self._tray_image(),
                               on_open=lambda: self.events.put(("tray", "open")),
                               on_pause=lambda: self.events.put(("tray", "pause")),
                               on_quit=lambda: self.events.put(("tray", "quit")),
                               is_paused=lambda: self.pause.is_set(),
                               logo_ico=str(resource_path("audyax.ico")))
        self.searching = False
        self.updating = False
        self._last_offered = ""     # last clipboard link already offered
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

    # ---------------------------------------------------------------- building
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
        norm_initial = bool(self.cfg["normalize"]) and not rg_initial  # never both at once
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

        # Header
        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(header, text="AUDYAX", font=self.f_title, anchor="w").pack(anchor="w")
        ctk.CTkLabel(header, text="Pega el enlace  •  Descarga  •  Disfruta", font=self.f_normal,
                     text_color=C["muted"], anchor="w").pack(anchor="w")

        # Controls card
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
        # The button says "Buscar" when the typed text isn't a link
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

        # Notices (hidden until needed)
        self.clipboard_notice = Notice(self, ctrl, 3, "link")
        self.update_notice = Notice(self, ctrl, 4, "refresh")
        self.watched_notice = Notice(self, ctrl, 5, "eye")

        # List
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
        # Headers: click to sort (▲ ascending, ▼ descending, third click = arrival order)
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

        # Detail
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
        self.l_chip.pack_forget()  # only shown when there is a year or genre
        self.l_detail_info = ctk.CTkLabel(info, text="", font=self.f_small, text_color=C["muted"],
                                       anchor="w", justify="left")
        self.l_detail_info.pack(anchor="w")
        # Thin separator + technical data of the file
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
        # small width: each button is as wide as its text (otherwise all are 140 and don't fit)
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

        # Status bar: counters per color + overall progress
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

        # Drag and drop links onto the window, the text box or the list
        if self.has_dnd:
            for w in (self, self.entry._entry, self.song_list._parent_canvas):
                try:
                    w.drop_target_register(DND_TEXT)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                except Exception:
                    pass

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=112, corner_radius=0, fg_color=C["sidebar"])
        sidebar.grid(row=0, column=0, rowspan=2, sticky="ns")   # row 1: the player bar
        sidebar.grid_propagate(False)
        sidebar.pack_propagate(False)
        self.l_logo = ctk.CTkLabel(sidebar, text="", image=logo(56), cursor="hand2")
        self.l_logo.pack(pady=(16, 14))
        self.l_logo.bind("<Double-Button-1>", self._on_logo_double_click)   # downloader <-> player

        # Each mode has its own navigation; the logo (double click) switches between them
        self.nav = {"downloads": ctk.CTkFrame(sidebar, fg_color="transparent"),
                    "player": ctk.CTkFrame(sidebar, fg_color="transparent")}
        self.nav_buttons = {"downloads": {}, "player": {}}

        def item(mode, key, text, ico, command=None):
            # the player has more sections: shorter buttons so they fit in the minimum window height
            tall = mode == "downloads"
            button = ctk.CTkButton(self.nav[mode], text=text, compound="top", width=92, height=74 if tall else 60,
                                   corner_radius=12, font=self.f_small, command=command)
            button.pack(padx=10, pady=3 if tall else 2)
            button._nav_icon = ico
            self.nav_buttons[mode][key] = button
            self._nav_style(button, False)

        item("downloads", "home", "Inicio", "home", self._go_home)
        item("downloads", "watched", "Vigiladas", "eye", self._open_watched)
        item("downloads", "settings", "Configuración", "gear", self._open_settings)
        item("downloads", "about", "Acerca de", "info", self._about)
        for view, ico in (("Reproduciendo", "playing"), ("Canciones", "note"), ("Artistas", "artist"),
                          ("Álbumes", "album"), ("Listas", "playlist"), ("Cola", "queue")):
            item("player", view, view, ico, lambda v=view: self._player_section(v))
        item("player", "about", "Acerca de", "info", self._about)
        self.nav["downloads"].pack(fill="x")
        self._mark_nav()

        ctk.CTkLabel(sidebar, text="", image=equalizer_image()).pack(side="bottom", pady=(0, 14))
        self.sw_theme = ctk.CTkSwitch(sidebar, text="", width=46, progress_color=C["accent"],
                                     command=lambda: self._apply_theme(
                                         "dark" if self.sw_theme.get() else "light"))
        self.sw_theme.pack(side="bottom", pady=(0, 6))
        ctk.CTkLabel(sidebar, text="Modo oscuro", font=self.f_small, text_color=C["muted"]).pack(side="bottom")
        if self.theme == "dark":
            self.sw_theme.select()

    def _nav_style(self, button, active):
        ico = button._nav_icon
        button.configure(image=icon(ico, 26, C["text"] if active else C["muted"]),
                         fg_color=C["nav_active"] if active else "transparent",
                         hover_color=C["nav_active"] if active else C["hover"],
                         text_color=C["text"] if active else C["muted"])

    def _mark_nav(self):
        """Shows the sidebar of the current mode and highlights where you are:
        "Inicio" in the downloader; Canciones / Artistas / Álbumes in the player."""
        mode = self.view_mode
        for m, frame in self.nav.items():
            if m == mode:
                frame.pack(fill="x", after=self.l_logo)
            else:
                frame.pack_forget()
        current = "home" if mode == "downloads" else self.player_section
        for key, button in self.nav_buttons[mode].items():
            self._nav_style(button, key == current)

    # ---------------------------------------------------------------- theme / settings
    def _about(self):
        top = ctk.CTkToplevel(self)
        top.title("Acerca de AUDYAX")
        top.geometry("420x440")
        top.resizable(False, False)
        top.transient(self)
        top.configure(fg_color=C["bg"])
        ctk.CTkLabel(top, text="", image=logo(72)).pack(pady=(20, 6))
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
        if self.player_view is not None:
            self.player_view.apply_theme()
        if self.now_playing is not None:
            self.now_playing.apply_theme()
        if mode == "dark":
            self.sw_theme.select()
        else:
            self.sw_theme.deselect()

    # ---------------------------------------------------------------- actions
    def _on_format_change(self):
        audio_format = self.var_format.get()
        original = audio_format == "original"
        if original:
            self.var_norm.set(False)
        # In m4a, YouTube's audio (AAC) is copied as is, without re-encoding: the quality
        # only applies when normalizing (then it does have to be re-encoded).
        uses_quality = audio_format in ("mp3", "opus") or (audio_format == "m4a" and self.var_norm.get())
        self.cb_quality.configure(state="normal" if uses_quality else "disabled")
        self.chk_norm.configure(state="disabled" if original else "normal")

    def _on_rg_checked(self):
        """ReplayGain and normalization are exclusive: checking one unchecks the other."""
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
        chosen = filedialog.askdirectory(initialdir=self.var_folder.get() or None,
                                        title="Elige la carpeta donde guardar")
        if chosen:
            self.var_folder.set(os.path.normpath(chosen))

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
                "lyrics", "musicbrainz", "caa_cover", "volume", "check_watched", "auto_download_new",
                "library_folder", "shuffle", "repeat")},
            "watched": self.watched,
        }

    def _on_download(self):
        """Main button: downloads if it's a link; if it's text, searches by name."""
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

    # ---------------------------------------------------------------- search
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

    # ---------------------------------------------------------------- clipboard / drop
    def _on_focus(self, _event=None):
        # FocusIn arrives once per widget; they are grouped into a single check
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
            # In the .exe yt-dlp is inside the program: what gets updated is the whole of AUDYAX
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
        cfg["subfolder"] = subfolder  # e.g. the playlist's folder when retrying
        cfg["extras"] = extras or {}    # title/artist/album to keep (see _retry)
        cfg.update(overrides or {})       # e.g. watched playlist: its folder and only the new songs
        if self.pending == 0:
            self.batch = {"ok": 0, "err": 0, "omit": 0}  # a new batch starts
        self.pending += 1
        self.jobs.put((url, cfg))
        self._ensure_workers()
        self._update_buttons()
        return True

    def _ensure_workers(self):
        """As many threads serving the queue as parallel downloads: that way several
        individual songs (e.g. chosen in the search) also download at once. The
        self.limit semaphore is what sets the real cap of songs downloading."""
        self.threads = [h for h in self.threads if h.is_alive()]
        while len(self.threads) < self.limit_n:
            h = threading.Thread(target=self._worker_loop, daemon=True)
            h.start()
            self.threads.append(h)

    def _worker_loop(self):
        while True:
            url, cfg = self.jobs.get()
            token = threading.Event()  # this job's own cancellation
            skip = set()             # songs of this job cancelled one by one

            def emit(ev, skip=skip):
                if ev[0] == "new":   # this job downloads this song
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
        try:  # drops the jobs that haven't started yet
            while True:
                self.jobs.get_nowait()
                self.pending -= 1
        except queue.Empty:
            pass
        if self.pause.is_set():
            self._toggle_pause()  # cancelling also leaves the pause
        self._update_buttons()

    def _cancel_songs(self, vids=None):
        """Cancels the selected song (the purple one) and, if any, the ones checked with the
        checkbox. No need to check anything: selecting it is enough. The rest continue."""
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
                owner.add(vid)  # the engine skips it or stops it at the next progress update
            f = self.rows[vid]
            f["previous_text"] = ""
            self._row(vid, color="red", text="Cancelada", reason="Cancelada por el usuario")
            f["cancelled"] = True

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
            # Songs being downloaded or processed show "En pausa"
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
            # Jobs already started keep the previous limit; new ones use this one
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

    def _process_events(self):
        try:
            for _ in range(30):  # in batches, so the window doesn't freeze with big playlists
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(30 if not self.events.empty() else 100, self._process_events)

    def _handle(self, ev):
        kind = ev[0]
        cancelled = kind in ("info", "status", "error", "rg") and self.rows.get(
            ev[1] if len(ev) > 1 else None, {}).get("cancelled")
        if cancelled:
            return  # late events of a song the user cancelled
        if kind == "new":
            if ev[1] in self.rows:  # retry: the file's data is read again
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
                self.rows[ev[1]]["cancelled"] = False  # it finished right before being cancelled
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
                self._toggle_pause()  # nothing is left paused
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

    def _retry(self):
        candidates = self._checked() or ([self.sel] if self.sel in self.rows else [])
        valid = [v for v in candidates if self.rows[v]["color"] == "red" and self.rows[v]["url"]]
        if not valid:
            messagebox.showinfo("Reintentar", "Solo se pueden reintentar canciones con error que tengan enlace.")
            return
        for vid in valid:
            f = self.rows[vid]
            # If it came from a YouTube Music playlist, its title, artist and album are kept
            # (downloading it alone, yt-dlp would use the video's: "(Official Video)", "ArtistVEVO"…)
            extras = None
            if f["url"].startswith("https://music.youtube.com/"):
                extras = {vid: {k: f[k] for k in ("title", "artist", "album")}}
            # Retried in the same subfolder (its playlist's, if it came from one)
            self._enqueue(f["url"], f["subfolder"], extras)

    # ---------------------------------------------------------------- icon, tray and notifications
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
        """True if the user is looking at AUDYAX (no notification in that case)."""
        try:
            return self.state() == "normal" and self.focus_displayof() is not None
        except (tk.TclError, KeyError):
            return False

    def _on_batch_done(self):
        """Everything in the queue finished: notification (if enabled and the window isn't being looked at)."""
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
        """Hides the window and leaves AUDYAX as an icon next to the clock."""
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

    # ---------------------------------------------------------------- keyboard shortcuts
    def _setup_shortcuts(self):
        # In the link box, Ctrl+V comes here first (before the box's normal paste)
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
        """True if the cursor is in a text box (keys behave normally there)."""
        try:
            w = self.focus_get()
        except (KeyError, tk.TclError):
            return False
        return w is not None and w.winfo_class() in ("Entry", "TEntry", "Text")

    def _shortcut_paste(self, in_entry: bool = False):
        """Ctrl+V: if the copied text is a link, it is downloaded right away.
        - Typing in another box (list search, settings…): normal paste.
        - In the link box with something already typed: normal paste (it's being edited).
        - If the copied text isn't a link: it goes into the box to search it with Enter."""
        if not in_entry and (self._focus_in_text() or self.view_mode == "player"):
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
        """Delete: removes the checked rows from the list or, if none, the selected one."""
        if self._focus_in_text() or self.view_mode == "player":
            return None
        target = self._checked() or ([self.sel] if self.sel in self.rows else [])
        if target:
            self._remove_rows(target)
        return "break"

    def _shortcut_search(self, _event=None):
        if self.view_mode == "player":
            self.player_view.focus_search()
            return "break"
        self.filter_entry.focus_set()
        self.filter_entry._entry.select_range(0, "end")
        return "break"

    # ---------------------------------------------------------------- closing
    def _on_window_close(self):
        """X button. With "Keep running in the background" (on by default), AUDYAX doesn't close:
        it hides next to the clock and keeps downloading. To quit completely: icon → Salir.
        Without that option (or without a system tray) it closes, asking if downloads are running."""
        if self.cfg.get("keep_in_background", True) and tray_available() and self._to_tray():
            if not self._background_notice_shown:
                self._background_notice_shown = True  # explained only the first time
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
        if self.player_view is not None:
            self.player_view.shutdown()
        save_config(self._current_cfg())
        self._cancel()
        self.tray.hide()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def self_test(app: "App", output: str):
    """--self-test: checks that the program (especially the .exe) ships everything it
    needs and that FFmpeg works, writes the result as JSON and exits.
    GitHub Actions uses it to test the .exe on Windows."""
    import tempfile

    from audyax.core.replaygain import analyze_loudness
    from audyax.downloader.youtube import HAS_YTMUSIC
    r = {"version": VERSION, "ytdlp": ytdlp_version(), "executable": bool(getattr(sys, "frozen", False)),
         "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
         "mutagen": HAS_MUTAGEN, "ytmusicapi": HAS_YTMUSIC, "drag_and_drop": app.has_dnd,
         "tray": tray_available(), "no_tray_reason": no_tray_reason(),
         "notifications": notifications_available(), "icon": resource_path("audyax.ico").exists()}
    try:  # Real FFmpeg: generates a tone and measures its loudness
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
    try:  # the audio library must be inside the .exe; the sound card may be missing (CI)
        import numpy  # noqa: F401
        import sounddevice  # noqa: F401
        r["audio_library"] = True
    except Exception as e:
        r["audio_library"], r["audio_library_error"] = False, str(e)
    r["audio"], r["audio_reason"] = audio_available()  # informational
    Path(output).write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    app.destroy()


def main():
    setup_bundled_ffmpeg()  # the .exe ships its own FFmpeg
    if "--self-test" in sys.argv:
        i = sys.argv.index("--self-test")
        output = sys.argv[i + 1] if len(sys.argv) > i + 1 else "self-test.json"
        app = App()
        app.after(1500, lambda: self_test(app, output))
        app.mainloop()
    else:
        App().mainloop()
    # When the window closes, exit completely: some library (e.g. the tray's) may
    # leave a thread alive that would keep the process open without a window.
    logging.shutdown()
    os._exit(0)
