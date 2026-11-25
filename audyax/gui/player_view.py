"""AUDYAX player (part 2): the music library view (songs, artists, albums), shown in the
main window in place of the downloader, plus the animation that switches between the two
when the sidebar logo is double-clicked. Playback goes through the app's single player bar.

The library itself (scan, cache, groups, search) is in audyax/core/library.py, without UI.
"""

import math
import queue
import sys
import threading
from io import BytesIO
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, ttk

    import customtkinter as ctk
    from PIL import Image, ImageDraw, ImageTk
except ImportError:  # the animation helpers can be imported (and tested) without a UI
    ctk = None

from audyax.core import history, library, playlists, tags
from audyax.core.library import format_duration

SMART_LISTS = {"smart:most": "Más escuchadas", "smart:recent": "Escuchadas recientemente",
               "smart:added": "Añadidas recientemente"}
SMART_SIZE = 50
THUMB = 40          # px of the album covers in the "Álbumes" list

VIEWS = ("Canciones", "Artistas", "Álbumes", "Listas", "Cola")

# Song table: column -> (header, width, field used to sort)
SONG_COLUMNS = {"title": ("Título", 260, "title"), "artist": ("Artista", 180, "artist"),
                "album": ("Álbum", 180, "album"), "year": ("Año", 60, "year"),
                "duration": ("Duración", 100, "duration")}

# ==============================================================================
# INTRO ANIMATION (double click on the sidebar logo)
# ==============================================================================
INTRO_MS = 1100          # total length
INTRO_STEP_MS = 16       # ~60 fps
INTRO_BARS = 7


def _ease_out_back(x: float) -> float:
    """0 -> 1 overshooting a little at the end (the logo "pops")."""
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def intro_frame(t: float) -> dict:
    """State of the animation at t (0..1): window opacity, logo scale (1 = full size),
    equalizer bar heights (0..1) and opacity of the text below.
    Pure function so it can be tested without a window."""
    t = max(0.0, min(1.0, t))
    if t < 0.18:                      # fade in
        alpha = t / 0.18
    elif t > 0.78:                    # fade out
        alpha = max(0.0, (1 - t) / 0.22)
    else:
        alpha = 1.0
    grow = min(1.0, t / 0.45)
    scale = 0.35 + 0.65 * _ease_out_back(grow)
    if t > 0.78:                      # it moves slightly towards the viewer while fading
        scale *= 1 + 0.25 * (t - 0.78) / 0.22
    rise = min(1.0, max(0.0, (t - 0.12) / 0.3))
    bars = []
    for i in range(INTRO_BARS):
        wave = 0.5 + 0.5 * math.sin(t * 22 + i * 0.9)
        bars.append(rise * (0.25 + 0.75 * wave) * (1 - max(0.0, t - 0.78) / 0.22))
    text = max(0.0, min(1.0, (t - 0.3) / 0.15)) * (alpha if t > 0.78 else 1.0)
    return {"alpha": alpha * 0.97, "scale": scale, "bars": bars, "text": text}


def _mix(c1: str, c2: str, k: float) -> str:
    """Color between c1 (k=0) and c2 (k=1), both "#rrggbb"."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(a, b))


SWAP_AT = 0.5   # moment of the animation (0..1) when the view behind it changes: fully covered


class IntroAnimation:
    """Borderless layer over the main window: the logo grows, the purple equalizer bounces,
    `text` ("Reproductor" / "Descargas") appears and everything fades out.
    on_swap() is called halfway, while the window is fully covered (that's when the view
    behind changes), and on_done() at the end."""

    LOGO_PX = 200

    def __init__(self, app, text, on_swap, on_done=None):
        from audyax.gui.style import C, resource_path
        self.app, self.text_shown = app, text
        self.on_swap, self.on_done = on_swap, on_done
        self._swapped = False
        dark = ctk.get_appearance_mode() == "Dark"
        i = 1 if dark else 0
        self.bg, self.accent, self.text = C["bg"][i], C["accent"][i], C["text"][i]
        try:
            self.logo = Image.open(resource_path("audyax.png")).convert("RGBA")
        except OSError:
            self.logo = None

        app.update_idletasks()
        w, h = app.winfo_width(), app.winfo_height()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        self.top = tk.Toplevel(app)
        self.top.overrideredirect(True)
        self.top.geometry(f"{w}x{h}+{x}+{y}")
        self.top.configure(bg=self.bg)
        self._set_alpha(0.0)
        self.top.lift()
        self.canvas = tk.Canvas(self.top, width=w, height=h, bg=self.bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.w, self.h = w, h
        self._frames = {}       # size -> PhotoImage (each logo size is drawn once)
        self._elapsed = 0
        self.top.bind("<Button-1>", lambda _e: self._finish())   # a click skips it
        self._step()

    def _set_alpha(self, value: float):
        try:
            self.top.attributes("-alpha", value)
        except tk.TclError:
            pass  # some Linux window managers don't support transparency: it just appears

    def _logo_image(self, px: int):
        px = max(8, px - px % 4)   # few different sizes -> faster
        if px not in self._frames:
            if self.logo is not None:
                img = self.logo.resize((px, px), Image.LANCZOS)
            else:
                img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
                ImageDraw.Draw(img).ellipse((0, 0, px - 1, px - 1), fill=self.accent)
            self._frames[px] = ImageTk.PhotoImage(img, master=self.top)
        return self._frames[px]

    def _draw(self, f: dict):
        c, cx = self.canvas, self.w / 2
        cy = self.h / 2 - 50
        c.delete("all")
        # soft glow behind the logo
        for k in range(6, 0, -1):
            r = self.LOGO_PX * f["scale"] * (0.55 + k * 0.09)
            c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="",
                          fill=_mix(self.bg, self.accent, 0.05 * (7 - k) * min(1.0, f["scale"])))
        c.create_image(cx, cy, image=self._logo_image(int(self.LOGO_PX * f["scale"])))
        # equalizer under the logo
        bar_w, gap, max_h = 14, 8, 70
        total = INTRO_BARS * bar_w + (INTRO_BARS - 1) * gap
        x0, base = cx - total / 2, cy + self.LOGO_PX * 0.62 + max_h
        for i, height in enumerate(f["bars"]):
            x = x0 + i * (bar_w + gap)
            top = base - max(3.0, height * max_h)
            c.create_rectangle(x, top, x + bar_w, base, outline="",
                               fill=_mix(self.bg, self.accent, 0.45 + 0.55 * height))
        if f["text"] > 0:
            c.create_text(cx, base + 34, text=self.text_shown, font=(self.app.f_title.cget("family"), 22, "bold"),
                          fill=_mix(self.bg, self.text, f["text"]))

    def _step(self):
        if self.top is None:
            return
        t = self._elapsed / INTRO_MS
        if t >= 1:
            self._finish()
            return
        if t >= SWAP_AT:
            self._swap()
        f = intro_frame(t)
        self._set_alpha(f["alpha"])
        try:
            self._draw(f)
        except tk.TclError:
            self._finish()
            return
        self._elapsed += INTRO_STEP_MS
        self.top.after(INTRO_STEP_MS, self._step)

    def _swap(self):
        if not self._swapped:
            self._swapped = True
            self.on_swap()
            if self.top is not None:
                self.top.lift()   # the new view must not end up on top of the animation

    def _finish(self):
        if self.top is None:
            return
        self._swap()              # skipped with a click: the view still changes
        top, self.top = self.top, None
        try:
            top.destroy()
        except tk.TclError:
            pass
        if self.on_done:
            self.on_done()




# ==============================================================================
# PLAYER VIEW (library)
# ==============================================================================
def load_cover(path):
    """The embedded cover as a PIL image (or None)."""
    try:
        data = tags.read_cover(path)
        return Image.open(BytesIO(data)).convert("RGB") if data else None
    except Exception:
        return None


class PlayerView(ctk.CTkFrame if ctk else object):
    """Library of the music folder, shown in the main window instead of the downloader.
    The app shows/hides it; songs play through the app's player bar (app._play_item)."""

    def __init__(self, app):
        from audyax.gui.style import C, icon
        super().__init__(app, fg_color="transparent")
        self.app, self.C = app, C
        self.lib = library.Library(self._folder())
        self.view = "Canciones"
        self.order = ("artist", False)
        self.shown = []                 # tracks in the song table, in the order shown
        self.group_items = {}           # group row id -> artist name / album key
        self.playing = None             # path of the library song that is playing
        self._thumbs = {}               # album key -> PhotoImage of its cover (Álbumes)
        self._thumbs_asked = set()
        self._thumb_queue = queue.Queue()
        self._events = queue.Queue()    # scan thread -> UI
        self._cancel_scan = threading.Event()
        self._scanning = False
        self._search_job = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_header(icon)
        self._build_body()
        self._apply_tree_style()
        if self.lib.load():
            self._refresh_all()
        self.after(100, self._poll)

    def on_show(self):
        """Each time the view appears: bring the library up to date (only changes are read)."""
        folder = self._folder()
        if folder != self.lib.folder:
            self._switch_folder(folder)
        else:
            self.rescan()

    def focus_search(self):
        self.e_search.focus_set()
        self.e_search._entry.select_range(0, "end")

    # ---------------------------------------------------------------- building
    def _folder(self) -> str:
        cfg = self.app.cfg
        folder = cfg.get("library_folder") or self.app.var_folder.get().strip() or cfg["folder"]
        return str(Path(folder).expanduser())

    def _build_header(self, icon):
        C, app = self.C, self.app
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="", image=icon("note", 30, C["accent"])).grid(row=0, column=0, rowspan=2,
                                                                         padx=(0, 10))
        ctk.CTkLabel(head, text="Biblioteca", font=app.f_title, anchor="w").grid(row=0, column=1, sticky="w")
        self.l_stats = ctk.CTkLabel(head, text="", font=app.f_small, text_color=C["muted"], anchor="w")
        self.l_stats.grid(row=1, column=1, sticky="w")

        # without textvariable: customtkinter only shows the placeholder text without one
        self.e_search = ctk.CTkEntry(head, width=260, height=34, corner_radius=10,
                                     font=app.f_normal, placeholder_text="Buscar título, artista o álbum…",
                                     fg_color=C["input"], border_color=C["border"], text_color=C["text"])
        self.e_search.grid(row=0, column=2, rowspan=2, padx=(10, 6))
        self.e_search.bind("<KeyRelease>", lambda _e: self._search_later())
        self.e_search.bind("<Escape>", lambda _e: self.set_query(""))
        self.btn_rescan = app._button(head, "Actualizar", self.rescan, icon("refresh", 16), height=34)
        self.btn_rescan.grid(row=0, column=3, rowspan=2, padx=6)
        app._button(head, "Carpeta…", self._choose_folder, icon("folder", 16), height=34).grid(
            row=0, column=4, rowspan=2, padx=(6, 0))

        # the section (Canciones / Artistas / Álbumes) is chosen in the sidebar; here, its title
        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.l_section = ctk.CTkLabel(nav, text=self.view, font=app.f_detail, anchor="w")
        self.l_section.pack(side="left")
        self.l_folder = ctk.CTkLabel(nav, text="", font=app.f_small, text_color=C["muted"], anchor="e")
        self.l_folder.pack(side="right")
        self.btn_clear_queue = app._button(nav, "Vaciar cola", app._queue_clear, icon("trash", 16), height=30)
        self.btn_save_queue = app._button(nav, "Guardar como lista", self._save_queue_as_playlist, height=30)
        self.btn_new_list = app._button(nav, "Nueva lista", lambda: self._new_playlist([]), height=30)
        self._section_buttons = {"Cola": (self.btn_clear_queue, self.btn_save_queue),
                                 "Listas": (self.btn_new_list,)}

    def _build_body(self):
        C = self.C
        body = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=14, border_width=1, border_color=C["border"])
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.body = body

        # left: artists or albums (hidden in "Canciones")
        self.group_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.group_frame.grid_rowconfigure(0, weight=1)
        self.group_frame.grid_columnconfigure(0, weight=1)
        self.groups = ttk.Treeview(self.group_frame, style="Library.Treeview", show="tree",
                                   selectmode="browse")
        self.groups.column("#0", width=320)
        self.groups.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sb = ctk.CTkScrollbar(self.group_frame, command=self.groups.yview)
        sb.grid(row=0, column=1, sticky="ns", pady=8)
        self.groups.configure(yscrollcommand=sb.set)
        self.groups.bind("<<TreeviewSelect>>", lambda _e: self._fill_songs())
        self.groups.bind("<Double-Button-1>", lambda _e: self._play_index(0))
        self.groups.bind("<Return>", lambda _e: self._play_index(0))
        for button in ("<Button-3>", "<Button-2>" if sys.platform == "darwin" else None):
            if button:
                self.groups.bind(button, self._group_menu)

        # right: songs
        songs = ctk.CTkFrame(body, fg_color="transparent")
        songs.grid(row=0, column=1, sticky="nsew")
        songs.grid_rowconfigure(0, weight=1)
        songs.grid_columnconfigure(0, weight=1)
        self.songs = ttk.Treeview(songs, style="Library.Treeview", columns=list(SONG_COLUMNS),
                                  show="headings", selectmode="extended")
        for col, (header, width, _field) in SONG_COLUMNS.items():
            self.songs.heading(col, text=header, command=lambda c=col: self._sort_by(c))
            self.songs.column(col, width=width, minwidth=50, stretch=col in ("title", "artist", "album"),
                              anchor="e" if col == "duration" else "w")
        self.songs.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sb2 = ctk.CTkScrollbar(songs, command=self.songs.yview)
        sb2.grid(row=0, column=1, sticky="ns", pady=8, padx=(0, 4))
        self.songs.configure(yscrollcommand=sb2.set)
        self.songs.bind("<Double-Button-1>", self._on_song_double_click)
        self.songs.bind("<Return>", lambda _e: self._play_selected())
        self.songs.bind("<Delete>", lambda _e: self._remove_from_queue())
        for button in ("<Button-3>", "<Button-2>" if sys.platform == "darwin" else None):
            if button:
                self.songs.bind(button, self._song_menu)

        self.l_empty = ctk.CTkLabel(body, text="", font=self.app.f_normal, text_color=C["muted"],
                                    justify="center")

    def _apply_tree_style(self):
        """ttk tables don't follow customtkinter's theme by themselves: colors are set here
        (and again when the theme changes)."""
        C = self.C
        i = 1 if ctk.get_appearance_mode() == "Dark" else 0
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        family = self.app.f_normal.cget("family")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")    # the only built-in theme that accepts all these colors
        except tk.TclError:
            pass
        style.configure("Library.Treeview", background=C["card"][i], fieldbackground=C["card"][i],
                        foreground=C["text"][i], borderwidth=0, rowheight=int(30 * scale),
                        font=(family, 10))
        style.configure("Albums.Treeview", background=C["card"][i], fieldbackground=C["card"][i],
                        foreground=C["text"][i], borderwidth=0, rowheight=int((THUMB + 10) * scale),
                        font=(family, 10))
        style.map("Albums.Treeview", background=[("selected", C["sel"][i])],
                  foreground=[("selected", C["text"][i])])
        style.layout("Albums.Treeview", [("Albums.Treeview.treearea", {"sticky": "nswe"})])
        style.map("Library.Treeview", background=[("selected", C["sel"][i])],
                  foreground=[("selected", C["text"][i])])
        style.configure("Library.Treeview.Heading", background=C["card2"][i], foreground=C["muted"][i],
                        relief="flat", borderwidth=0, font=(family, 10, "bold"), padding=(6, 6))
        style.map("Library.Treeview.Heading", background=[("active", C["hover"][i])])
        style.layout("Library.Treeview", [("Library.Treeview.treearea", {"sticky": "nswe"})])
        for tree in (self.songs, self.groups):
            tree.tag_configure("odd", background=C["row_b"][i])
            tree.tag_configure("playing", foreground=C["accent"][i])
            tree.tag_configure("played", foreground=C["muted"][i])
            tree.tag_configure("smart", foreground=C["accent"][i])

    def apply_theme(self):
        self._apply_tree_style()

    # ---------------------------------------------------------------- scan
    def rescan(self):
        if self._scanning:
            return
        self._scanning = True
        # each scan has its own cancel flag and events: an old scan (e.g. of the previous
        # folder) that is still finishing can't touch the new one
        self._cancel_scan = cancel = threading.Event()
        self._events = events = queue.Queue()
        self.btn_rescan.configure(state="disabled")
        if not self.lib.tracks:
            self._set_stats("Buscando canciones…")
        lib = self.lib

        def work():
            try:
                stats = lib.scan(progress=lambda d, t: events.put(("progress", d, t)), cancel=cancel.is_set)
                events.put(("done", stats))
            except Exception as e:   # never leave the view stuck in "scanning"
                events.put(("error", str(e)))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- album covers
    def _blank_thumb(self):
        if getattr(self, "_blank", None) is None:
            px = self._thumb_px()
            self._blank = ImageTk.PhotoImage(Image.new("RGBA", (px, px), (0, 0, 0, 0)), master=self)
        return self._blank

    def _thumb_px(self) -> int:
        return int(THUMB * ctk.ScalingTracker.get_widget_scaling(self))

    def _load_thumbs(self, missing):
        """Reads the album covers in the background; each one appears in the list when ready."""
        if not missing:
            return
        self._thumbs_asked.update(k for k, _ in missing)
        px, out = self._thumb_px(), self._thumb_queue

        def work():
            for key, path in missing:
                try:
                    img = load_cover(path)
                    if img is not None:
                        side = min(img.size)
                        img = img.crop(((img.width - side) // 2, (img.height - side) // 2,
                                        (img.width + side) // 2, (img.height + side) // 2)).resize((px, px), Image.LANCZOS)
                        mask = Image.new("L", (px, px), 0)
                        ImageDraw.Draw(mask).rounded_rectangle((0, 0, px - 1, px - 1), radius=max(3, px // 8), fill=255)
                        img = img.convert("RGBA")
                        img.putalpha(mask)
                        out.put((key, img))
                except Exception:
                    pass
        threading.Thread(target=work, daemon=True).start()

    def _show_thumbs(self):
        """UI thread: the covers read so far go into the album list."""
        try:
            while True:
                key, img = self._thumb_queue.get_nowait()
                self._thumbs[key] = ImageTk.PhotoImage(img, master=self)
                if self.view == "Álbumes":
                    for iid, k in self.group_items.items():
                        if k == key and self.groups.exists(iid):
                            self.groups.item(iid, image=self._thumbs[key])
        except queue.Empty:
            pass

    def _poll(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self._show_thumbs()
        last_progress = None
        try:
            while True:
                ev = self._events.get_nowait()
                if ev[0] == "progress":
                    last_progress = ev
                elif ev[0] in ("done", "error"):
                    self._scanning = False
                    self.btn_rescan.configure(state="normal")
                    if ev[0] == "done" and ev[1] is not None:
                        changed = ev[1]["added"] or ev[1]["updated"] or ev[1]["removed"]
                        if changed or not self.songs.get_children():
                            self._refresh_all()
                        else:
                            self._set_stats()
                    elif ev[0] == "error":
                        self._set_stats(f"No se pudo leer la carpeta: {ev[1]}")
                    last_progress = None
        except queue.Empty:
            pass
        if last_progress and not self.lib.tracks:
            self._set_stats(f"Buscando canciones… {last_progress[1]} de {last_progress[2]}")
        self.after(120, self._poll)

    def _choose_folder(self):
        chosen = filedialog.askdirectory(parent=self, initialdir=self.lib.folder or None,
                                         title="Carpeta de tu música")
        if chosen:
            self.app.cfg["library_folder"] = str(Path(chosen))
            self._switch_folder(self._folder())

    def _switch_folder(self, folder):
        self._cancel_scan.set()
        self.lib = library.Library(folder)
        self.lib.load()
        self._scanning = False
        self._refresh_all()
        self.rescan()

    def shutdown(self):
        self._cancel_scan.set()

    # ---------------------------------------------------------------- views
    def _set_stats(self, text=None):
        if text is None:
            n = len(self.lib.tracks)
            if n:
                total = self.lib.total_duration()
                time = f"{total / 3600:.1f} h".replace(".", ",") if total >= 3600 else format_duration(total)
                text = (f"{n} {'canción' if n == 1 else 'canciones'} · {len(self.lib.artists())} artistas · "
                        f"{len(self.lib.albums())} álbumes · {time}")
            else:
                text = "Sin canciones todavía"
        self.l_stats.configure(text=text)
        self.l_folder.configure(text=self.lib.folder)

    def _flash(self, text: str, ms: int = 4000):
        """Shows a short message where the library totals are, then the totals again."""
        self.l_stats.configure(text=text, text_color=self.C["accent"])
        if getattr(self, "_flash_job", None):
            self.after_cancel(self._flash_job)

        def back():
            self._flash_job = None
            self.l_stats.configure(text_color=self.C["muted"])
            self._set_stats()
        self._flash_job = self.after(ms, back)

    def _refresh_all(self):
        self._set_stats()
        self._fill_groups()
        self._fill_songs()

    def set_view(self, view):
        if view not in VIEWS or view == self.view:
            return
        self.view = view
        self.l_section.configure(text=view)
        for section, buttons in self._section_buttons.items():
            for b in buttons:
                if section == view:
                    b.pack(side="right", padx=(0, 10))
                else:
                    b.pack_forget()
        if view != "Cola":
            self.l_folder.configure(text=self.lib.folder)
        self._fill_groups()
        self._fill_songs()

    def query(self) -> str:
        return self.e_search.get()

    def set_query(self, text: str):
        self.e_search.delete(0, "end")
        if text:
            self.e_search.insert(0, text)
        self._on_search()

    def _search_later(self):
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(180, self._on_search)

    def _on_search(self):
        self._search_job = None
        self._fill_groups()
        self._fill_songs()

    def _fill_groups(self):
        g = self.groups
        selected = self.group_items.get(g.selection()[0]) if g.selection() else None
        g.delete(*g.get_children())
        self.group_items = {}
        g.configure(style="Albums.Treeview" if self.view == "Álbumes" else "Library.Treeview")
        if self.view in ("Canciones", "Cola"):
            self.group_frame.grid_forget()
            return
        self.group_frame.grid(row=0, column=0, sticky="nsw")
        query = self.query()
        matching = library.search(self.lib.tracks, query) if query.strip() else None
        keep_artists = {library.normalize(library.artist_of(t)) for t in matching} if matching is not None else None
        keep_albums = {library.album_key(t) for t in matching} if matching is not None else None
        n = 0
        if self.view == "Listas":
            for key, name in SMART_LISTS.items():          # automatic lists first
                n_songs = len(self._smart_tracks(key))
                iid = g.insert("", "end", text=f"  ★ {name}  ·  {n_songs} {'canción' if n_songs == 1 else 'canciones'}",
                               tags=("smart",) + (("odd",) if n % 2 else ()))
                self.group_items[iid] = key
                n += 1
            for name, path in playlists.list_playlists(self.lib.folder):
                n_songs = len(playlists.read_playlist(path))
                iid = g.insert("", "end", text=f"  {name}  ·  {n_songs} {'canción' if n_songs == 1 else 'canciones'}",
                               tags=("odd",) if n % 2 else ())
                self.group_items[iid] = str(path)
                n += 1
        elif self.view == "Artistas":
            for name, songs, albums in self.lib.artists():
                if keep_artists is not None and library.normalize(name) not in keep_artists:
                    continue
                detail = f"{songs} {'canción' if songs == 1 else 'canciones'}"
                if albums > 1:
                    detail += f" · {albums} álbumes"
                iid = g.insert("", "end", text=f"  {name}  ·  {detail}", tags=("odd",) if n % 2 else ())
                self.group_items[iid] = name
                n += 1
        else:
            missing = []
            for a in self.lib.albums():
                if keep_albums is not None and a["key"] not in keep_albums:
                    continue
                year = f" ({a['year']})" if a["year"] else ""
                thumb = self._thumbs.get(a["key"])
                iid = g.insert("", "end", text=f"  {a['album']}{year}  ·  {a['artist']}",
                               image=thumb or self._blank_thumb(), tags=("odd",) if n % 2 else ())
                self.group_items[iid] = a["key"]
                if thumb is None and a["key"] not in self._thumbs_asked:
                    missing.append((a["key"], a["first"]["path"]))
                n += 1
            self._load_thumbs(missing)
        children = g.get_children()
        if self.view == "Listas" and not children:
            self.group_frame.grid(row=0, column=0, sticky="nsw")
        if children:   # keep the selected artist/album/playlist if it's still there
            again = next((i for i in children if self.group_items[i] == selected), children[0])
            g.selection_set(again)
            g.focus(again)
            g.see(again)

    def _current_tracks(self):
        tracks = self.lib.tracks
        if self.view != "Canciones":
            sel = self.groups.selection()
            key = self.group_items.get(sel[0]) if sel else None
            if key is None:
                return []
            if self.view == "Listas":
                tracks = self._playlist_tracks(key)
            else:
                tracks = self.lib.tracks_of_artist(key) if self.view == "Artistas" else self.lib.tracks_of_album(key)
        return library.search(tracks, self.query())

    def _smart_tracks(self, key):
        """Songs of an automatic list ("smart:most", "smart:recent", "smart:added")."""
        by_path = {t["path"]: t for t in self.lib.tracks}
        hist = self.app.history
        if key == "smart:added":
            chosen = history.recently_added(self.lib.tracks, SMART_SIZE)
        elif key == "smart:most":
            chosen = [by_path[p] for p in hist.most_played(SMART_SIZE, existing=set(by_path))]
        else:
            chosen = [by_path[p] for p in hist.recent(SMART_SIZE, existing=set(by_path))]
        return [{**t, "pos": pos} for pos, t in enumerate(chosen)]

    def _playlist_tracks(self, playlist_path):
        """Songs of a playlist in its order; each one knows its position ("pos") so the same
        song can be in it twice. Songs not in the library are read from the file (or shown
        as missing)."""
        if str(playlist_path).startswith("smart:"):
            return self._smart_tracks(playlist_path)
        by_path = {t["path"]: t for t in self.lib.tracks}
        out = []
        for pos, path in enumerate(playlists.read_playlist(playlist_path)):
            t = by_path.get(path)
            if t is None:
                exists = Path(path).is_file()
                t = library.read_track(path) if exists else \
                    {"path": path, "title": Path(path).stem, "artist": "", "album": "", "year": "", "genre": "",
                     "duration": 0, "mtime": 0, "size": 0}
                t = {**t, "missing": not exists}
            out.append({**t, "pos": pos})
        return out

    def _selected_playlist(self):
        sel = self.groups.selection() if self.view == "Listas" else ()
        return self.group_items.get(sel[0]) if sel else None

    def _fill_songs(self):
        if self.view == "Cola":
            self._fill_queue()
            return
        tracks = self._current_tracks()
        column, reverse = self.order
        if self.view == "Listas":
            pass                                               # a playlist keeps its own order
        elif self.view == "Álbumes" and column == "artist":   # an album keeps its own order
            tracks = sorted(tracks, key=lambda t: Path(t["path"]).name.lower())
        else:
            tracks = library.sort_tracks(tracks, SONG_COLUMNS[column][2], reverse)
        self.shown = tracks
        s = self.songs
        s.delete(*s.get_children())
        for n, t in enumerate(tracks):
            is_it = t["path"] == self.playing
            row_tags = (("odd",) if n % 2 else ()) + (("playing",) if is_it else
                                                      ("played",) if t.get("missing") else ())
            title = t["title"] + ("  (no encontrada)" if t.get("missing") else "")
            s.insert("", "end", iid=self._row_id(t), tags=row_tags,
                     values=(("▶  " if is_it else "") + title, library.artist_of(t), library.album_of(t),
                             t.get("year") or "", format_duration(t.get("duration"))))
        for col, (header, _w, _f) in SONG_COLUMNS.items():
            arrow = (" ▼" if reverse else " ▲") if col == column and self.view != "Listas" else ""
            s.heading(col, text=header + arrow)
        self._show_empty_message(tracks)

    def _row_id(self, track) -> str:
        """Table row id: the file path, or its position in a playlist (it may repeat)."""
        return f"#{track['pos']}" if self.view == "Listas" else track["path"]

    def _track_of_row(self, row):
        return next((t for t in self.shown if self._row_id(t) == row), None)

    def _show_empty_message(self, tracks):
        if tracks or (self._scanning and not self.lib.tracks):
            self.l_empty.place_forget()
            return
        if self.view == "Listas":
            text = ("Todavía no tienes listas.\n\nCrea una con «Nueva lista», o clic derecho en una canción → "
                    "«Añadir a una lista»." if not self.group_items else
                    "Esta lista está vacía." if not self.query().strip() else
                    "Ninguna canción de la lista coincide con la búsqueda.")
            self.l_empty.configure(text=text)
            self.l_empty.place(relx=0.5, rely=0.5, anchor="center")
            return
        if not self.lib.tracks:
            text = (f"No hay música en\n{self.lib.folder}\n\nDescarga canciones o elige otra carpeta "
                    "con «Carpeta…».")
        else:
            text = "Ninguna canción coincide con la búsqueda."
        self.l_empty.configure(text=text)
        self.l_empty.place(relx=0.5, rely=0.5, anchor="center")

    def _fill_queue(self):
        """The "Cola" section: what was played (gray), what is playing (▶) and what's next,
        in play order. Each row's id is its position in the queue."""
        q, s = self.app.play_queue, self.songs
        s.delete(*s.get_children())
        query = library.normalize(self.query()).split()
        playing = self.app.player is not None and self.app.player.item is not None
        shown = 0
        for pos, item in enumerate(q.items):
            text = library.normalize(" ".join(str(item.get(k) or "") for k in ("title", "artist", "album")))
            if query and not all(w in text for w in query):
                continue
            is_current = pos == q.index and playing
            row_tags = (("odd",) if shown % 2 else ()) + (("playing",) if is_current else
                                                          ("played",) if pos < q.index else ())
            s.insert("", "end", iid=str(pos), tags=row_tags,
                     values=(("▶  " if is_current else "") + str(item.get("title") or Path(item["path"]).stem),
                             item.get("artist") or library.NO_ARTIST, item.get("album") or "—",
                             item.get("year") or "", format_duration(item.get("duration"))))
            shown += 1
        for col, (header, _w, _f) in SONG_COLUMNS.items():
            s.heading(col, text=header)
        if q.index >= 0 and s.exists(str(q.index)):
            s.see(str(q.index))
        if shown:
            self.l_empty.place_forget()
        else:
            self.l_empty.configure(text="La cola está vacía.\n\nReproduce una canción, o clic derecho → "
                                        "«Añadir a la cola»." if not query else
                                   "Ninguna canción de la cola coincide con la búsqueda.")
            self.l_empty.place(relx=0.5, rely=0.5, anchor="center")
        n = len(q.upcoming())
        self.l_folder.configure(text=f"{n} {'canción' if n == 1 else 'canciones'} por sonar")

    def queue_changed(self):
        if self.view == "Cola":
            self._fill_songs()

    def _remove_from_queue(self):
        if self.view == "Cola":
            self.app._queue_remove([int(i) for i in self.songs.selection()])
        elif self.view == "Listas":
            self._remove_from_playlist()

    def _song_menu(self, event):
        """Right click on a song: play it, play it next or add it to the queue
        (all the selected songs if the click is on one of them)."""
        row = self.songs.identify_row(event.y)
        if not row:
            return
        if row not in self.songs.selection():
            self.songs.selection_set(row)
        selected = list(self.songs.selection())
        app = self.app
        menu = app._new_menu()
        if self.view == "Cola":
            q = app.play_queue
            items = [q.items[int(r)] for r in selected if int(r) < len(q.items)]
            menu.add_command(label="Reproducir", command=lambda: app._queue_jump(int(row)))
            menu.add_command(label="Quitar de la cola", command=self._remove_from_queue)
        else:
            items = [self._item(t) for t in self.shown if self._row_id(t) in selected]
            menu.add_command(label="Reproducir", command=lambda: self._play_row(row))
            menu.add_command(label="Reproducir a continuación", command=lambda: app._queue_play_next(items))
            menu.add_command(label="Añadir a la cola", command=lambda: app._queue_add(items))
            selected_list = self._selected_playlist()
            if self.view == "Listas" and selected_list and not selected_list.startswith("smart:"):
                menu.add_command(label="Quitar de la lista", command=self._remove_from_playlist)
        menu.add_cascade(label="Añadir a una lista", menu=self._playlists_menu(menu, items))
        if len(items) == 1:
            self._tools_menu(menu, items[0])
        menu.tk_popup(event.x_root, event.y_root)

    # ---------------------------------------------------------------- tools (one song)
    def _tools_menu(self, menu, item):
        path = item["path"]
        if not Path(path).is_file():
            return
        menu.add_separator()
        url = tags.read_source_url(path)
        if url:
            menu.add_command(label="Copiar enlace de YouTube", command=lambda: self._copy_link(url))
        if Path(path).suffix.lower() in (".mp3", ".m4a", ".mp4", ".opus"):
            menu.add_command(label="Editar etiquetas…", command=lambda: self._edit_tags(path))
        menu.add_command(label="Recortar…", command=lambda: self._trim(path, item.get("title")))

    def _copy_link(self, url):
        self.clipboard_clear()
        self.clipboard_append(url)
        self._flash("Enlace copiado")

    def _edit_tags(self, path):
        from audyax.gui.dialogs import TagEditorWindow
        TagEditorWindow(self.app, None, path, on_saved=self._tags_saved)

    def _tags_saved(self, old_path, new_path, data):
        """The library reads the file again (only it: the rest is in the cache)."""
        self._thumbs.clear()            # the cover may have changed
        self._thumbs_asked.clear()
        self._flash("Etiquetas guardadas")
        bar = self.app.player
        if bar is not None and bar.item and bar.item["path"] == old_path:
            bar._show({**bar.item, "path": new_path, "id": new_path,
                       "title": data.get("title") or bar.item.get("title"),
                       "artist": data.get("artist") or "", "album": data.get("album") or "", "image": None})
        self.rescan()

    def _trim(self, path, title):
        from audyax.gui.trim_window import TrimWindow

        def done(out):
            self._flash(f"Recorte guardado: {Path(out).name}")
            self.rescan()
        TrimWindow(self.app, path, title or "", on_done=done)

    # ---------------------------------------------------------------- playlists
    def _playlists_menu(self, parent, items):
        sub = self.app._new_menu()
        for name, path in playlists.list_playlists(self.lib.folder):
            sub.add_command(label=name, command=lambda p=path: self._add_to_playlist(p, items))
        if sub.index("end") is not None:
            sub.add_separator()
        sub.add_command(label="Nueva lista…", command=lambda: self._new_playlist(items))
        return sub

    def _ask_name(self, title, text):
        dialog = ctk.CTkInputDialog(title=title, text=text, button_fg_color=self.C["accent"],
                                    button_hover_color=self.C["accent_hover"])
        name = dialog.get_input()
        return name.strip() if name else ""

    def _new_playlist(self, items):
        from tkinter import messagebox
        name = self._ask_name("Nueva lista", "Nombre de la lista:")
        if not name:
            return
        try:
            path = playlists.create_playlist(self.lib.folder, name, items)
        except (ValueError, OSError) as e:
            messagebox.showwarning("Nueva lista", str(e), parent=self)
            return
        self._show_playlist(path)

    def _show_playlist(self, path):
        """Goes to the Listas section with `path` selected."""
        self.app._player_section("Listas")
        self._fill_groups()
        for iid, p in self.group_items.items():
            if p == str(path):
                self.groups.selection_set(iid)
                self.groups.see(iid)
        self._fill_songs()

    def _add_to_playlist(self, path, items):
        """Adds the songs at the end of the playlist (the ones already in it are skipped)."""
        from tkinter import messagebox
        current = self._playlist_tracks(path)
        have = {t["path"] for t in current}
        new = [i for i in items if i["path"] not in have]
        try:
            playlists.write_playlist(path, current + new)
        except OSError as e:
            messagebox.showwarning("Añadir a una lista", f"No se pudo guardar la lista: {e}", parent=self)
            return
        skipped = len(items) - len(new)
        name = Path(path).stem
        text = (f"{len(new)} {'canción añadida' if len(new) == 1 else 'canciones añadidas'} a «{name}»"
                + (f" ({skipped} ya {'estaba' if skipped == 1 else 'estaban'})" if skipped else ""))
        self._flash(text)
        if self.view == "Listas":
            self._fill_groups()
            self._fill_songs()

    def _remove_from_playlist(self):
        path = self._selected_playlist()
        rows = set(self.songs.selection())
        if not path or not rows or path.startswith("smart:"):
            return
        keep = [t for t in self._playlist_tracks(path) if f"#{t['pos']}" not in rows]
        try:
            playlists.write_playlist(path, keep)
        except OSError:
            return
        self._fill_groups()
        self._fill_songs()

    def _group_menu(self, event):
        """Right click on a playlist: play, rename, delete."""
        if self.view != "Listas":
            return
        row = self.groups.identify_row(event.y)
        if not row:
            return
        self.groups.selection_set(row)
        path = self.group_items[row]
        menu = self.app._new_menu()
        menu.add_command(label="Reproducir", command=lambda: self._play_index(0))
        menu.add_command(label="Añadir a la cola",
                         command=lambda: self.app._queue_add([self._item(t) for t in self._playlist_tracks(path)]))
        if path.startswith("smart:"):
            menu.add_command(label="Guardar como lista…",
                             command=lambda: self._new_playlist([self._item(t) for t in self._playlist_tracks(path)]))
            menu.tk_popup(event.x_root, event.y_root)
            return
        menu.add_separator()
        menu.add_command(label="Renombrar…", command=lambda: self._rename_playlist(path))
        menu.add_command(label="Eliminar lista", command=lambda: self._delete_playlist(path))
        menu.tk_popup(event.x_root, event.y_root)

    def _rename_playlist(self, path):
        from tkinter import messagebox
        name = self._ask_name("Renombrar lista", f"Nuevo nombre para «{Path(path).stem}»:")
        if not name:
            return
        try:
            new = playlists.rename_playlist(self.lib.folder, path, name)
        except (ValueError, OSError) as e:
            messagebox.showwarning("Renombrar lista", str(e), parent=self)
            return
        self._show_playlist(new)

    def _delete_playlist(self, path):
        from tkinter import messagebox
        if messagebox.askyesno("Eliminar lista", f"¿Eliminar la lista «{Path(path).stem}»?\n\n"
                               "Solo se borra la lista; las canciones se quedan en tu carpeta.", parent=self):
            playlists.delete_playlist(path)
            self._fill_groups()
            self._fill_songs()

    def _save_queue_as_playlist(self):
        items = list(self.app.play_queue.items)
        if items:
            self._new_playlist(items)

    def _sort_by(self, column):
        if self.view in ("Cola", "Listas"):
            return   # the queue and the playlists keep their own order
        col, reverse = self.order
        self.order = (column, not reverse) if col == column else (column, False)
        self._fill_songs()

    # ---------------------------------------------------------------- playback
    def _item(self, track) -> dict:
        """Queue item of a library song (the cover is read only when it plays)."""
        return {"id": track["path"], "path": track["path"], "title": track["title"],
                "artist": track.get("artist") or "", "album": track.get("album") or "",
                "year": track.get("year") or "", "duration": track.get("duration") or 0,
                "source": "library"}

    def _on_song_double_click(self, event):
        if self.songs.identify_region(event.x, event.y) == "heading":
            return
        row = self.songs.identify_row(event.y)
        if row:
            self._play_row(row)

    def _play_selected(self):
        sel = self.songs.selection()
        if sel:
            self._play_row(sel[0])

    def _play_row(self, row):
        if self.view == "Cola":
            self.app._queue_jump(int(row))
        elif self.view == "Listas":
            track = self._track_of_row(row)
            if track is not None:
                self.app._play_queue([self._item(t) for t in self.shown], self.shown.index(track))
        else:
            self.play_path(row)

    def _play_index(self, i):
        if 0 <= i < len(self.shown):
            self._play_row(self._row_id(self.shown[i]))

    def play_path(self, path):
        """Plays a song; the queue becomes the table as it's shown (filtered and sorted)."""
        paths = [t["path"] for t in self.shown]
        if path in paths:
            self.app._play_queue([self._item(t) for t in self.shown], paths.index(path))
            return
        track = next((t for t in self.lib.tracks if t["path"] == path), None)
        if track is not None:
            self.app._play_queue([self._item(track)])

    def mark_playing(self, path):
        """Highlights the song that is playing (None = none of the library's)."""
        self.playing = path
        if self.view == "Cola":
            return   # the queue table is redrawn by queue_changed()
        if self.view == "Listas":
            self._fill_songs()   # rows are positions, not paths
            return
        for n, iid in enumerate(self.songs.get_children()):
            is_it = iid == path
            self.songs.item(iid, tags=(("odd",) if n % 2 else ()) + (("playing",) if is_it else ()))
            title = self.songs.set(iid, "title").removeprefix("▶  ")
            self.songs.set(iid, "title", ("▶  " if is_it else "") + title)
        if path and self.songs.exists(path):
            self.songs.see(path)
