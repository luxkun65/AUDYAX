"""The song list: rows, filter, sort, selection and export."""

import sys
import tkinter as tk
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from audyax.downloader.export import export_csv, export_m3u
from audyax.gui.dialogs import ExportWindow
from audyax.gui.style import STATUS_COLORS, C, fetch_bytes, icon, release_variable, shorten, square_image

# List status filter: menu text -> row color (None = all)
STATUS_FILTERS = {"Todas": None, "Descargadas": "green", "En proceso": "yellow", "Con error": "red"}


STATUS_ORDER = {"red": 0, "yellow": 1, "green": 2}  # when sorting by status, errors first


# Width of each list column (0 = takes the remaining space)
COLUMN_WIDTHS = [34, 150, 52, 0, 170, 170, 60, 250]


def setup_columns(frame):
    for i, a in enumerate(COLUMN_WIDTHS):
        frame.grid_columnconfigure(i, minsize=a, weight=1 if a == 0 else 0)


# ==============================================================================
# LIST ROW
# ==============================================================================
class ListRow:
    """A song in the list: checkbox, status, thumbnail and data."""

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
            # Right click (Button-2 on macOS): menu with that song's actions
            for button in ("<Button-3>", "<Button-2>" if sys.platform == "darwin" else None):
                if button:
                    w.bind(button, lambda e: app._row_menu(vid, e), add=True)

        # Hovering the status of a queued/downloading song turns it into
        # "✕ Cancel"; clicking there cancels it (without selecting or checking it).
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
        # 19 characters fit in the column; longer text widens it and misaligns the row
        self._set("artist", self.l_artist, text=shorten(f["artist"] or "—", 19))
        self._set("album", self.l_album, text=shorten(f["album"] or "—", 19))
        self._set("year", self.l_year, text=f["year"] or "—")
        self._set("link", self.l_link, text="  " + shorten(f["url"], 32))
        if f["img_s"] is not None:
            self._set("thumb", self.l_thumb, image=f["img_s"])

    def paint(self, selected: bool):
        pos = self.app.rows[self.vid].get("pos", 0)  # position in the view (filtered/sorted)
        color = C["sel"] if selected else (C["row_a"] if pos % 2 == 0 else C["row_b"])
        if self._last.get("color") != color:
            self.frame.configure(fg_color=color)
            self._last["color"] = color

    def destroy(self):
        self.frame.destroy()


class SongListMixin:
    """Song list behavior. Mixed into App."""

    # ---------------------------------------------------------------- list
    def _row(self, vid: str, **fields):
        is_new = vid not in self.rows
        if is_new:
            self.rows[vid] = {
                "num": len(self.rows) + 1, "color": "yellow", "text": "En cola", "title": vid,
                "artist": "", "album": "", "year": "", "genre": "", "url": "", "path": "",
                "subfolder": "", "reason": "", "replaygain": "", "thumbnail": "", "img_requested": False,
                "img_s": None, "img_b": None, "img_pil": None, "lyrics": "", "musicbrainz": False, "data": None, "data_requested": False,
                "previous_text": "", "pos": len(self.rows), "check": tk.BooleanVar(value=False)}
            self.rows[vid]["w"] = ListRow(self, self.song_list, vid)
            if self._view_active():
                self.rows[vid]["w"].frame.pack_forget()  # the view decides whether and where it goes
        f = self.rows[vid]
        if self.pause.is_set() and fields.get("text") and f["color"] == "yellow" \
                and fields.get("color", "yellow") == "yellow" and f["text"] == "En pausa" \
                and fields["text"] != "En pausa":
            f["previous_text"] = fields.pop("text")  # while paused it keeps showing "En pausa"
        before = {k: f.get(k) for k in ("color", "title", "artist", "album", "year")}
        for k, v in fields.items():
            if v is not None and k in f and k not in ("w", "check"):
                f[k] = v
        if self._view_active():
            changed = any(f.get(k) != v for k, v in before.items())
            if is_new or (changed and (vid in self._visible) != self._matches(f)) or \
                    (changed and self.order[0] != "num"):
                self._schedule_view()
        elif is_new:
            self._visible.add(vid)
        if f["thumbnail"] and not f["img_requested"]:
            f["img_requested"] = True
            self.pool.submit(self._fetch_image, vid, f["thumbnail"])
        f["w"].refresh_row(f)
        f["w"].paint(vid == self.sel)
        if is_new and self.sel is None:
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

    # ---------------------------------------------------------------- filter / sort
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
        # Queue controls, on the right: Export | Pause | Cancel all
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
        """True if there is a filter or an order other than arrival (then the list is rebuilt)."""
        return (bool(self.var_filter.get().strip()) or STATUS_FILTERS.get(self.var_status.get())
                is not None or self.order[0] != "num")

    def _matches(self, f) -> bool:
        color = STATUS_FILTERS.get(self.var_status.get())
        if color and f["color"] != color:
            return False
        text = " ".join(str(f.get(k) or "") for k in ("title", "artist", "album")).casefold()
        return all(p in text for p in self.var_filter.get().casefold().split())

    def _rows_in_view(self) -> list:
        """Visible ids, in the order they are shown."""
        items = [(vid, f) for vid, f in self.rows.items() if self._matches(f)]
        col, desc = self.order
        if col == "num":
            items.sort(key=lambda it: it[1]["num"], reverse=desc)
        elif col == "status":
            items.sort(key=lambda it: (STATUS_ORDER.get(it[1]["color"], 9), it[1]["num"]), reverse=desc)
        else:
            # Rows without that value always go last, in arrival order
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
            self.order = ("num", False)  # third click: back to arrival order
        for c, (lbl, text) in self.headers.items():
            arrow = (" ▼" if self.order[1] else " ▲") if c == self.order[0] else ""
            lbl.configure(text=text + arrow)
        self._apply_view()

    # ---------------------------------------------------------------- export
    def _export_menu(self):
        """Export button: small window with the two options. (It used to be a Windows popup
        menu, which on some computers never appeared.)"""
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

    # ---------------------------------------------------------------- selection
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
        """Checks/unchecks only the visible rows (respects the filter)."""
        value = self.var_all.get()
        for vid in self._visible:
            if vid in self.rows:
                self.rows[vid]["check"].set(value)

    def _checked(self):
        return [vid for vid, f in self.rows.items() if f["check"].get()]

    def _clear_list(self):
        # Nothing checked: the visible rows are removed (e.g. filtering "Con error" clears the errors)
        self._remove_rows(self._checked() or [v for v in self.rows if v in self._visible])

    def _remove_rows(self, target):
        """Removes rows from the list (doesn't delete files). Queued or downloading ones
        are kept: they have to be cancelled first."""
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

    def _new_menu(self) -> tk.Menu:
        """Popup menu with the current theme's colors (light or dark)."""
        i = 1 if self.theme == "dark" else 0
        return tk.Menu(self, tearoff=0, bg=C["card"][i], fg=C["text"][i], activebackground=C["sel"][i],
                       activeforeground=C["text"][i], disabledforeground=C["muted"][i],
                       relief="flat", borderwidth=1, font=(self.f_normal.cget("family"), 11))

    def _row_menu(self, vid, event):
        """Right-click menu on a song in the list."""
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
            menu.add_command(label="Reproducir a continuación",
                             command=lambda: self._queue_play_next([self._player_item(vid)]))
            menu.add_command(label="Añadir a la cola", command=lambda: self._queue_add([self._player_item(vid)]))
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
