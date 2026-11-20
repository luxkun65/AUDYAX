"""AUDYAX player: the "Reproduciendo" section (part 2, section 3). Big cover, the full
lyrics (synced ones follow the song and a click on a line jumps there) and what's next in
the queue. It reads the app's player bar and queue; it doesn't play anything by itself.

Immersive background (appearance package): the cover very blurred and darkened fills the
view (or an image of the user's), and panels and text take colors that go with it."""

import threading
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageTk

from audyax.core import colors, lyrics
from audyax.core.library import NO_ARTIST
from audyax.gui.player_view import load_cover

COVER_PX = 340
UP_NEXT = 5


class NowPlayingView(ctk.CTkFrame):
    def __init__(self, app):
        from audyax.gui.style import C
        super().__init__(app, fg_color="transparent")
        self.app, self.C = app, C
        self.path = None            # song shown
        self._lines, self._synced = [], False
        self._current = -2          # highlighted lyric line
        self._cover_img = None
        self._searching = False
        self._visible = False
        self._cover_src = None      # PIL cover of the song shown (for the background)
        self.surface = None         # colors over the immersive background, or None (normal)
        self._bg_key = None
        self._bg_photo = None
        self._bg_job = None

        # background image: under everything else
        self.bg_label = tk.Label(self, bd=0, highlightthickness=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_left()
        self._build_lyrics()
        self._apply_text_style()
        self.bind("<Configure>", lambda _e: self._background_later())
        self.after(200, self._tick)

    # ---------------------------------------------------------------- building
    def _build_left(self):
        C, app = self.C, self.app
        left = ctk.CTkFrame(self, fg_color="transparent", width=COVER_PX, corner_radius=16)
        left.grid(row=0, column=0, sticky="ns", padx=(14, 18), pady=14)
        self.left = left
        self.l_cover = ctk.CTkLabel(left, text="", width=COVER_PX, height=COVER_PX, fg_color=C["card"],
                                    corner_radius=18)
        self.l_cover.pack(pady=(4, 14))
        self.l_title = ctk.CTkLabel(left, text="", font=app.f_title, anchor="w", justify="left",
                                    wraplength=COVER_PX)
        self.l_title.pack(fill="x")
        self.l_artist = ctk.CTkLabel(left, text="", font=app.f_detail, text_color=C["accent"], anchor="w",
                                     justify="left", wraplength=COVER_PX)
        self.l_artist.pack(fill="x", pady=(2, 0))
        self.l_album = ctk.CTkLabel(left, text="", font=app.f_normal, text_color=C["muted"], anchor="w",
                                    justify="left", wraplength=COVER_PX)
        self.l_album.pack(fill="x", pady=(2, 16))
        self.l_next_title = ctk.CTkLabel(left, text="A continuación", font=app.f_bold, anchor="w")
        self.l_next_title.pack(fill="x")
        self.up_next = ctk.CTkFrame(left, fg_color="transparent")
        self.up_next.pack(fill="x", pady=(4, 0))

    def _build_lyrics(self):
        C, app = self.C, self.app
        box = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=14, border_width=1, border_color=C["border"])
        box.grid(row=0, column=1, sticky="nsew", padx=(0, 14), pady=14)
        self.box = box
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 4))
        self.l_head = ctk.CTkLabel(head, text="Letra", font=app.f_detail)
        self.l_head.pack(side="left")
        self.l_kind = ctk.CTkLabel(head, text="", font=app.f_small, text_color=C["muted"])
        self.l_kind.pack(side="left", padx=10)
        self.btn_find = app._button(head, "Buscar letra", self._find_lyrics, height=30)
        self.text = ctk.CTkTextbox(box, fg_color="transparent", wrap="word", activate_scrollbars=True,
                                   font=app.f_normal, cursor="arrow", border_spacing=8)
        self.text.grid(row=1, column=0, sticky="nsew", padx=(8, 4), pady=(0, 10))
        self.text.configure(state="disabled")
        self.l_empty = ctk.CTkLabel(box, text="", font=app.f_normal, text_color=C["muted"], justify="center")

    def _col(self, key):
        """A color of the view: the immersive one if there's a background, else the theme's."""
        if self.surface is not None:
            return self.surface[key]
        C = self.C
        return {"bg": "transparent", "left": "transparent", "panel": C["card"], "border": C["border"],
                "hover": C["hover"], "text": C["text"], "muted": C["muted"], "accent": C["accent"],
                "cover": C["card"]}[key]

    def _plain(self, key) -> str:
        """The color as one "#rrggbb" for the current light/dark mode (tk text tags need it)."""
        value = self._col(key)
        if isinstance(value, (tuple, list)):
            return value[1 if ctk.get_appearance_mode() == "Dark" else 0]
        return value

    def _apply_text_style(self):
        family = self.app.f_normal.cget("family")
        t = self.text._textbox
        t.tag_configure("line", font=(family, 14), foreground=self._plain("muted"), spacing1=4, spacing3=4,
                        justify="center")
        t.tag_configure("plain", font=(family, 14), foreground=self._plain("text"), spacing1=3, spacing3=3,
                        justify="center")
        t.tag_configure("current", font=(family, 19, "bold"), foreground=self._plain("accent"))
        t.tag_configure("past", foreground=self._plain("text"))
        t.tag_raise("current")

    def _apply_surface(self):
        """Paints panels and text with the colors of the current background (or the theme's)."""
        self.configure(fg_color=self._col("bg"))
        self.left.configure(fg_color=self._col("left"))
        self.l_cover.configure(fg_color=self._col("cover"))
        self.l_title.configure(text_color=self._col("text"))
        self.l_artist.configure(text_color=self._col("accent"))
        self.l_album.configure(text_color=self._col("muted"))
        self.l_next_title.configure(text_color=self._col("text"))
        self.box.configure(fg_color=self._col("panel"), border_color=self._col("border"))
        self.l_head.configure(text_color=self._col("text"))
        for label in (self.l_kind, self.l_empty):
            label.configure(text_color=self._col("muted"))
        self.text.configure(scrollbar_button_color=self._col("border"),
                            scrollbar_button_hover_color=self._col("muted"))
        self._apply_text_style()
        self._show_up_next()

    def apply_theme(self):
        self._bg_key = None
        self._render_background()
        self._apply_surface()

    # ---------------------------------------------------------------- immersive background
    def _background_source(self):
        """(image, blur, dim) for the background, or None for the normal look."""
        cfg = self.app.cfg
        mode = cfg.get("np_background", "cover")
        if mode == "image" and cfg.get("background_image"):
            try:
                return Image.open(cfg["background_image"]), 6, 0.5
            except OSError:
                return None
        if mode == "cover" and self._cover_src is not None:
            return self._cover_src, 40, 0.42
        return None

    def _background_later(self):
        if self._bg_job:
            self.after_cancel(self._bg_job)
        self._bg_job = self.after(150, self._render_background)

    def _render_background(self):
        self._bg_job = None
        if not self._visible:
            return
        src = self._background_source()
        w, h = self.winfo_width(), self.winfo_height()
        key = (id(src[0]) if src else None, self.app.cfg.get("np_background"), self.app.cfg.get("background_image"),
               self.app.cfg.get("dynamic_colors"), w, h, tuple(self.C["accent"]))
        if key == self._bg_key:
            return
        self._bg_key = key
        if src is None or w < 50 or h < 50:
            self.bg_label.place_forget()
            if self.surface is not None:
                self.surface = None
                self._apply_surface()
            return
        image, blur, dim = src
        bg = colors.blurred_background(image, (w, h), blur=blur, dim=dim)
        self._bg_photo = ImageTk.PhotoImage(bg, master=self)
        self.bg_label.configure(image=self._bg_photo)
        self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        self.bg_label.lower()
        mean = colors.mean_color(bg)
        hue = colors.cover_hue(self._cover_src) if self.app.cfg.get("dynamic_colors") else None
        accent = colors.readable_accent(hue) if hue is not None else self.C["accent"][1]
        self.surface = {"bg": mean, "left": mean, "panel": colors.lighten(mean, 0.06),
                        "border": colors.lighten(mean, 0.16), "hover": colors.lighten(mean, 0.14),
                        "cover": colors.lighten(mean, 0.08), "text": "#f6f3fb", "muted": "#c9c1d9",
                        "accent": accent}
        self._apply_surface()

    # ---------------------------------------------------------------- shown / hidden
    def on_show(self):
        self._visible = True
        self._load_song(force=True)
        self._background_later()

    def on_hide(self):
        self._visible = False

    # ---------------------------------------------------------------- song
    def _playing_item(self):
        bar = self.app.player
        return bar.item if bar is not None else None

    def song_changed(self):
        """The app calls this when another song starts (or playback stops)."""
        if self._visible:
            self._load_song()

    def queue_changed(self):
        if self._visible:
            self._show_up_next()

    def _load_song(self, force=False):
        item = self._playing_item()
        path = item["path"] if item else None
        if path == self.path and not force:
            return
        self.path = path
        if item is None:
            self.l_title.configure(text="Nada sonando")
            self.l_artist.configure(text="")
            self.l_album.configure(text="Reproduce algo desde Canciones, Artistas, Álbumes o tus listas.")
            self._set_cover(None)
            self._set_lyrics("")
            self._show_up_next()
            self._render_background()
            return
        self.l_title.configure(text=item.get("title") or Path(path).stem)
        self.l_artist.configure(text=item.get("artist") or NO_ARTIST)
        album = " · ".join(str(x) for x in (item.get("album"), item.get("year")) if x)
        self.l_album.configure(text=album)
        self._set_cover(item.get("image") or load_cover(path))
        self._set_lyrics(lyrics.read_lyrics(path))
        self._show_up_next()
        self._render_background()

    def _set_cover(self, image):
        self._cover_src = image
        if image is None:
            from audyax.gui.style import logo
            self.l_cover.configure(image=logo(COVER_PX // 2))
            return
        try:
            img = image.convert("RGB")
            side = min(img.size)   # square crop (YouTube thumbnails are 16:9)
            img = img.crop(((img.width - side) // 2, (img.height - side) // 2,
                            (img.width + side) // 2, (img.height + side) // 2))
            img = img.resize((COVER_PX * 2, COVER_PX * 2), Image.LANCZOS)
            self._cover_img = ctk.CTkImage(light_image=img, dark_image=img, size=(COVER_PX, COVER_PX))
            self.l_cover.configure(image=self._cover_img)
        except Exception:
            self.l_cover.configure(image=None)

    # ---------------------------------------------------------------- lyrics
    def _set_lyrics(self, text: str):
        self._lines, self._synced = lyrics.lyric_lines(text)
        self._current = -2
        t = self.text._textbox
        self.text.configure(state="normal")
        t.delete("1.0", "end")
        for i, (_sec, line) in enumerate(self._lines):
            tags = ("line", f"l{i}") if self._synced else ("plain",)
            t.insert("end", (line or "♪" if self._synced else line) + "\n", tags)
            if self._synced:
                t.tag_bind(f"l{i}", "<Button-1>", lambda _e, k=i: self._seek_line(k))
                t.tag_bind(f"l{i}", "<Enter>", lambda _e: t.configure(cursor="hand2"))
                t.tag_bind(f"l{i}", "<Leave>", lambda _e: t.configure(cursor="arrow"))
        self.text.configure(state="disabled")
        t.yview_moveto(0)
        if self._lines:
            self.l_empty.place_forget()
            self.l_kind.configure(text="sincronizada · clic en una línea para ir ahí" if self._synced
                                  else "sin sincronizar")
            self.btn_find.pack_forget()
        else:
            self.l_kind.configure(text="")
            if self.path:
                self.l_empty.configure(text="Esta canción no tiene letra guardada.")
                self.btn_find.pack(side="right")
            else:
                self.l_empty.configure(text="")
                self.btn_find.pack_forget()
            self.l_empty.place(relx=0.5, rely=0.5, anchor="center")

    def _seek_line(self, i):
        bar = self.app.player
        if bar is not None and bar.item and self._synced and 0 <= i < len(self._lines):
            bar.engine.seek(max(0.0, self._lines[i][0]))

    def _highlight(self, i):
        """Marks line i as the current one and keeps it in the middle of the box."""
        if i == self._current:
            return
        t = self.text._textbox
        t.tag_remove("current", "1.0", "end")
        t.tag_remove("past", "1.0", "end")
        self._current = i
        if i < 0:
            return
        t.tag_add("current", f"{i + 1}.0", f"{i + 1}.end")
        if i > 0:
            t.tag_add("past", "1.0", f"{i}.end")
        first, last = t.yview()
        n = max(1, len(self._lines))
        t.yview_moveto(max(0.0, (i + 0.5) / n - (last - first) / 2))

    def _find_lyrics(self):
        """Looks for the lyrics on LRCLIB and saves them as the settings say (.lrc if the
        settings have lyrics turned off, so the song file isn't touched)."""
        item = self._playing_item()
        if item is None or self._searching:
            return
        self._searching = True
        self.btn_find.configure(state="disabled", text="Buscando…")
        path, mode = item["path"], self.app.cfg.get("lyrics", "off")
        proxy = self.app.cfg.get("proxy", "")
        duration = self.app.player.engine.duration or None

        def work():
            result = lyrics.fetch_and_save(path, item.get("title") or "", item.get("artist") or "",
                                           item.get("album") or "", duration, mode, proxy)
            self.after(0, lambda: self._found(path, result))
        threading.Thread(target=work, daemon=True).start()

    def lyrics_found(self, path):
        """The automatic search found the lyrics of `path`."""
        if path == self.path and self._visible:
            self._set_lyrics(lyrics.read_lyrics(path))

    def _found(self, path, result):
        self._searching = False
        self.btn_find.configure(state="normal", text="Buscar letra")
        if path != self.path:
            return
        if result == "ok":
            self._set_lyrics(lyrics.read_lyrics(path))
            if self.app.player is not None:
                self.app.player.reload_lyrics()
        else:
            self.l_empty.configure(text="No se encontró la letra de esta canción." if result == "none"
                                   else "No se pudo buscar la letra (¿sin conexión?).")

    # ---------------------------------------------------------------- up next
    def _show_up_next(self):
        for w in self.up_next.winfo_children():
            w.destroy()
        q = self.app.play_queue
        upcoming = q.upcoming()[:UP_NEXT] if self._playing_item() else []
        if not upcoming:
            ctk.CTkLabel(self.up_next, text="Nada más en la cola", font=self.app.f_small,
                         text_color=self._col("muted"), anchor="w").pack(fill="x")
            return
        for k, item in enumerate(upcoming):
            row = ctk.CTkButton(self.up_next, anchor="w", height=30, corner_radius=8, fg_color="transparent",
                                hover_color=self._col("hover"), text_color=self._col("text"), font=self.app.f_small,
                                text=_short(f"{item.get('title') or Path(item['path']).stem}  ·  "
                                            f"{item.get('artist') or NO_ARTIST}", 48),
                                command=lambda pos=q.index + 1 + k: self.app._queue_jump(pos))
            row.pack(fill="x")
        rest = len(q.upcoming()) - len(upcoming)
        if rest > 0:
            ctk.CTkLabel(self.up_next, text=f"y {rest} más en la cola", font=self.app.f_small,
                         text_color=self._col("muted"), anchor="w").pack(fill="x", padx=12)

    # ---------------------------------------------------------------- refresh
    def _tick(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        if self._visible:
            item = self._playing_item()
            if (item["path"] if item else None) != self.path:
                self._load_song()
            if self._synced and item is not None:
                self._highlight(lyrics.current_line(self._lines, self.app.player.engine.position))
        self.after(200, self._tick)


def _short(text: str, n: int) -> str:
    text = "".join(c if ord(c) <= 0xFFFF else "□" for c in str(text or ""))
    return text if len(text) <= n else text[: n - 1] + "…"

