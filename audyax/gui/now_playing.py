"""AUDYAX player: the "Reproduciendo" section. Big cover, the full lyrics (synced ones follow
the song, scroll smoothly and a click on a line jumps there) and what's next in the queue.

Everything is drawn on a canvas on top of the background (the blurred cover, an image of
your own, or the plain app color), so the background shows behind the text instead of being
covered by solid panels. Layouts (np_layout.py): balanced, big cover, big lyrics, no lyrics;
and full screen. It reads the app's player bar and queue; it doesn't play anything itself."""

import threading
import tkinter as tk
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from audyax.core import colors, lyrics
from audyax.core.library import NO_ARTIST
from audyax.gui import np_layout
from audyax.gui.player_view import load_cover

UP_NEXT = 5
LIGHT_TEXT, LIGHT_MUTED = "#f6f3fb", "#cfc8dc"


def _rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def _square(img):
    img = img.convert("RGB")
    side = min(img.size)   # square crop (YouTube thumbnails are 16:9)
    return img.crop(((img.width - side) // 2, (img.height - side) // 2,
                     (img.width + side) // 2, (img.height + side) // 2))


class NowPlayingView(ctk.CTkFrame):
    def __init__(self, app):
        from audyax.gui.style import C
        super().__init__(app, fg_color="transparent")
        self.app, self.C = app, C
        self.path = None                 # song shown
        self.item = None
        self._cover_src = None           # PIL cover of the song shown
        self._lines, self._synced = [], False
        self._current = -2               # highlighted lyric line
        self._offset = 0.0               # scroll of the lyrics (pixels)
        self._line_items, self._line_centers = [], []
        self._content_height = 0.0
        self._fonts = ("", 14, 19)
        self._searching = False
        self._visible = False
        self._render_job = self._anim_job = None
        self._photos = []                # PhotoImages alive while shown
        self._colors = {}
        self._lyrics_rect = None
        self.surface = None              # the colors used on an image background (None = plain)

        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.lyr = tk.Canvas(self.canvas, highlightthickness=0, bd=0)
        self.lyr.bind("<MouseWheel>", self._on_wheel)
        self.lyr.bind("<Button-4>", lambda _e: self._on_wheel(None, 120))
        self.lyr.bind("<Button-5>", lambda _e: self._on_wheel(None, -120))
        self._build_controls()
        self.bind("<Configure>", lambda _e: self._render_later())
        self.after(200, self._tick)

    # ---------------------------------------------------------------- controls (small widgets on top)
    def _build_controls(self):
        C, app = self.C, self.app
        self.controls = ctk.CTkFrame(self.canvas, fg_color=C["card"], corner_radius=12)
        self.seg = ctk.CTkSegmentedButton(self.controls, values=list(np_layout.LAYOUTS.values()),
                                          command=self._on_layout, font=app.f_small, height=28,
                                          selected_color=C["accent"], selected_hover_color=C["accent_hover"])
        self.seg.set(np_layout.LAYOUTS.get(self._layout(), np_layout.LAYOUTS[np_layout.DEFAULT_LAYOUT]))
        self.seg.pack(side="left", padx=(6, 4), pady=6)
        self.btn_full = ctk.CTkButton(self.controls, text="Pantalla completa", width=130, height=28,
                                      font=app.f_small, corner_radius=8, fg_color="transparent",
                                      border_width=1, border_color=C["border"], text_color=C["text"],
                                      hover_color=C["hover"], command=self.app._toggle_np_fullscreen)
        self.btn_full.pack(side="left", padx=(4, 6), pady=6)
        self.btn_find = ctk.CTkButton(self.lyr, text="Buscar letra", width=120, height=32, corner_radius=10,
                                      font=app.f_normal, fg_color=C["accent"], hover_color=C["accent_hover"],
                                      text_color=C["on_accent"], command=self._find_lyrics)

    def _layout(self):
        return self.app.cfg.get("np_layout", np_layout.DEFAULT_LAYOUT)

    def _on_layout(self, label):
        self.app.cfg["np_layout"] = next(k for k, v in np_layout.LAYOUTS.items() if v == label)
        self._render()

    def fullscreen_changed(self, on: bool):
        self.btn_full.configure(text="Salir de pantalla completa" if on else "Pantalla completa",
                                width=180 if on else 130)
        self._render_later()

    # ---------------------------------------------------------------- shown / hidden
    def on_show(self):
        self._visible = True
        self._load_song(force=True)

    def on_hide(self):
        self._visible = False

    def apply_theme(self):
        C = self.C
        self.controls.configure(fg_color=C["card"])
        self.seg.configure(selected_color=C["accent"], selected_hover_color=C["accent_hover"])
        self.btn_full.configure(border_color=C["border"], text_color=C["text"], hover_color=C["hover"])
        self.btn_find.configure(fg_color=C["accent"], hover_color=C["accent_hover"], text_color=C["on_accent"])
        self._render()

    # ---------------------------------------------------------------- song
    def _playing_item(self):
        bar = self.app.player
        return bar.item if bar is not None else None

    def song_changed(self):
        if self._visible:
            self._load_song()

    def queue_changed(self):
        if self._visible:
            self._render_later()

    def _load_song(self, force=False):
        item = self._playing_item()
        path = item["path"] if item else None
        if path == self.path and not force:
            return
        self.path, self.item = path, item
        self._cover_src = (item.get("image") or load_cover(path)) if item else None
        self._lines, self._synced = lyrics.lyric_lines(lyrics.read_lyrics(path)) if path else ([], False)
        self._current, self._offset = -2, 0.0
        self._render()

    def lyrics_found(self, path):
        if path == self.path and self._visible:
            self._lines, self._synced = lyrics.lyric_lines(lyrics.read_lyrics(path))
            self._current, self._offset = -2, 0.0
            self._render()

    # ---------------------------------------------------------------- colors and background
    def _background(self, w, h):
        """(PIL image of w × h, on_image) — on_image=False for the plain app color."""
        cfg = self.app.cfg
        mode = cfg.get("np_background", "cover")
        src = None
        if mode == "image" and cfg.get("background_image"):
            try:
                src = (Image.open(cfg["background_image"]), 0, 0.62)   # your picture, sharp
            except OSError:
                src = None
        elif mode == "cover" and self._cover_src is not None:
            src = (self._cover_src, 40, 0.42)
        if src is None:
            i = 1 if ctk.get_appearance_mode() == "Dark" else 0
            return Image.new("RGB", (w, h), self.C["bg"][i]), False
        image, blur, dim = src
        return colors.blurred_background(image, (w, h), blur=blur, dim=dim), True

    def _palette(self, on_image: bool) -> dict:
        """Text colors: light on images (they're darkened), the theme's on the plain color.
        The accent is the same one the player bar uses (dynamic colors or scheme)."""
        dark = ctk.get_appearance_mode() == "Dark"
        accent = self.app.player_palette()["accent"] if hasattr(self.app, "player_palette") else self.C["accent"]
        if on_image:
            return {"text": LIGHT_TEXT, "muted": LIGHT_MUTED, "accent": accent[1], "panel": (0, 0, 0, 96)}
        i = 1 if dark else 0
        C = self.C
        card = C["card"][i].lstrip("#")
        return {"text": C["text"][i], "muted": C["muted"][i], "accent": accent[i],
                "panel": tuple(int(card[k:k + 2], 16) for k in (0, 2, 4)) + (255,)}

    # ---------------------------------------------------------------- drawing
    def _render_later(self):
        if self._render_job:
            self.after_cancel(self._render_job)
        self._render_job = self.after(120, self._render)

    def _render(self):
        self._render_job = None
        if not self._visible:
            return
        cv = self.canvas
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 50 or h < 50:
            self._render_later()
            return
        q = self.app.play_queue
        upcoming = q.upcoming()[:UP_NEXT] if self.item else []
        r = np_layout.compute(self._layout(), w, h, has_next=bool(upcoming))
        bg, on_image = self._background(w, h)
        col = self._colors = self._palette(on_image)
        self.surface = col if on_image else None
        family = self.app.f_normal.cget("family")

        # panel behind the lyrics, baked into the background (translucent on images)
        if r["lyrics"]:
            overlay = Image.new("RGBA", bg.size, (0, 0, 0, 0))
            ImageDraw.Draw(overlay).rounded_rectangle(r["lyrics"], radius=18, fill=col["panel"])
            bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

        self._photos = []
        cv.delete("all")
        photo = ImageTk.PhotoImage(bg, master=self)
        self._photos.append(photo)
        cv.create_image(0, 0, image=photo, anchor="nw")

        # cover
        x, y, size = r["cover"]
        if self._cover_src is not None:
            cover = _rounded(_square(self._cover_src).resize((size, size), Image.LANCZOS), max(8, size // 18))
        else:
            from audyax.gui.style import resource_path
            try:
                cover = Image.open(resource_path("audyax.png")).convert("RGBA").resize((size // 2, size // 2))
            except OSError:
                cover = Image.new("RGBA", (size // 2, size // 2), (0, 0, 0, 0))
        cphoto = ImageTk.PhotoImage(cover, master=self)
        self._photos.append(cphoto)
        cv.create_image(x + size // 2, y + size // 2, image=cphoto)

        # song data
        f = r["fonts"]
        ix, iy, iw, anchor = r["info"]
        justify = "center" if anchor == "n" else "left"
        if self.item:
            title = self.item.get("title") or Path(self.path).stem
            artist = self.item.get("artist") or NO_ARTIST
            album = " · ".join(str(v) for v in (self.item.get("album"), self.item.get("year")) if v)
        else:
            title, artist = "Nada sonando", ""
            album = "Reproduce algo desde Canciones, Artistas, Álbumes o tus listas."
        t1 = cv.create_text(ix, iy, text=title, anchor=anchor, width=iw, justify=justify, fill=col["text"],
                            font=(family, f["title"], "bold"))
        y_next = cv.bbox(t1)[3] + 4
        if artist:
            t2 = cv.create_text(ix, y_next, text=artist, anchor=anchor, width=iw, justify=justify,
                                fill=col["accent"], font=(family, f["artist"]))
            y_next = cv.bbox(t2)[3] + 2
        if album:
            cv.create_text(ix, y_next, text=album, anchor=anchor, width=iw, justify=justify, fill=col["muted"],
                           font=(family, f["album"]))

        # up next
        if r["next"] and upcoming:
            nx, ny, nw, rows = r["next"]
            cv.create_text(nx, ny, text="A continuación", anchor="nw", fill=col["text"],
                           font=(family, f["small"] + 2, "bold"))
            for k, item in enumerate(upcoming[:rows]):
                tag = f"next{k}"
                label = f"{item.get('title') or Path(item['path']).stem}  ·  {item.get('artist') or NO_ARTIST}"
                cv.create_text(nx + 2, ny + 26 + k * 30, text=_short(label, 46), anchor="nw", width=nw,
                               fill=col["muted"], font=(family, f["small"] + 1), tags=(tag,))
                cv.tag_bind(tag, "<Button-1>", lambda _e, pos=q.index + 1 + k: self.app._queue_jump(pos))
                cv.tag_bind(tag, "<Enter>", lambda _e, t=tag: (cv.itemconfigure(t, fill=col["text"]),
                                                              cv.configure(cursor="hand2")))
                cv.tag_bind(tag, "<Leave>", lambda _e, t=tag: (cv.itemconfigure(t, fill=col["muted"]),
                                                              cv.configure(cursor="")))

        # layout buttons (top right)
        cv.create_window(w - np_layout.PAD, 10, window=self.controls, anchor="ne")

        # lyrics
        self._draw_lyrics(r, bg, family)

    def _draw_lyrics(self, r, bg, family):
        cv, lyr, col = self.canvas, self.lyr, self._colors
        self._line_items, self._line_centers = [], []
        self._lyrics_rect = r["lyrics"]
        lyr.delete("all")
        self.btn_find.place_forget()
        if not r["lyrics"]:
            return
        x0, y0, x1, y1 = r["lyrics"]
        lw, lh = x1 - x0, y1 - y0
        # the lyrics canvas shows its piece of the same background, so it looks see-through
        piece = ImageTk.PhotoImage(bg.crop((x0, y0, x1, y1)), master=self)
        self._photos.append(piece)
        lyr.configure(width=lw, height=lh)
        lyr.create_image(0, 0, image=piece, anchor="nw", tags=("bg",))
        cv.create_window(x0, y0, window=lyr, anchor="nw", width=lw, height=lh)
        f = r["fonts"]
        self._fonts = (family, f["lyric"], f["current"])
        if not self._lines:
            text = "Esta canción no tiene letra guardada." if self.item else ""
            lyr.create_text(lw // 2, lh // 2 - 24, text=text, fill=col["muted"], font=(family, f["lyric"]),
                            width=lw - 40, justify="center")
            if self.item:
                self.btn_find.place(x=lw // 2, y=lh // 2 + 16, anchor="center")
            return
        for i, (_sec, line) in enumerate(self._lines):
            item = lyr.create_text(lw // 2, 0, text=(line or "♪") if self._synced else line, width=lw - 48,
                                   justify="center", anchor="n", fill=col["muted"] if self._synced else col["text"],
                                   font=(family, f["lyric"]), tags=("line", f"l{i}"))
            self._line_items.append(item)
            if self._synced:
                lyr.tag_bind(f"l{i}", "<Button-1>", lambda _e, k=i: self._seek_line(k))
                lyr.tag_bind(f"l{i}", "<Enter>", lambda _e: lyr.configure(cursor="hand2"))
                lyr.tag_bind(f"l{i}", "<Leave>", lambda _e: lyr.configure(cursor=""))
        kind = "Letra sincronizada · clic en una línea para ir ahí" if self._synced else "Letra"
        cv.create_text(x0 + 18, y0 - 8, text=kind, anchor="sw", fill=col["muted"], font=(family, f["small"]))
        self._current, self._offset = -2, 0.0
        self._reflow()
        if self._synced:
            position = self.app.player.engine.position if self.app.player is not None else 0
            self._highlight(lyrics.current_line(self._lines, position), animate=False)

    def _reflow(self):
        """Stacks the lines one under the other (their heights depend on the font)."""
        lyr = self.lyr
        y = 24.0
        self._line_centers = []
        for item in self._line_items:
            lyr.coords(item, lyr.coords(item)[0], y + self._offset)
            box = lyr.bbox(item)
            height = (box[3] - box[1]) if box else 20
            self._line_centers.append(y + height / 2)
            y += height + 10
        self._content_height = y

    # ---------------------------------------------------------------- lyrics behaviour
    def _seek_line(self, i):
        bar = self.app.player
        if bar is not None and bar.item and self._synced and 0 <= i < len(self._lines):
            bar.engine.seek(max(0.0, self._lines[i][0]))

    def _highlight(self, i, animate=True):
        if i == self._current or not self._line_items:
            return
        lyr, col = self.lyr, self._colors
        family, size, big = self._fonts
        self._current = i
        for k, item in enumerate(self._line_items):
            if k == i:
                lyr.itemconfigure(item, fill=col["accent"], font=(family, big, "bold"))
            else:
                lyr.itemconfigure(item, fill=col["text"] if k < i else col["muted"], font=(family, size))
        self._reflow()
        if i < 0:
            return
        if animate:
            self._animate()
        else:
            self._scroll_to(np_layout.lyric_scroll(self._target(), self._line_centers[i], self._offset, ease=1))

    def _target(self):
        x0, y0, x1, y1 = self._lyrics_rect
        return (y1 - y0) * 0.42

    def _scroll_to(self, offset):
        delta = offset - self._offset
        if abs(delta) >= 0.5:
            self.lyr.move("line", 0, delta)
            self._offset = offset

    def _animate(self):
        """Smooth scroll towards the current line (a part of the way every 16 ms)."""
        if self._anim_job:
            self.after_cancel(self._anim_job)
            self._anim_job = None
        if not self._line_items or self._current < 0 or not self._lyrics_rect:
            return
        self._scroll_to(np_layout.lyric_scroll(self._target(), self._line_centers[self._current], self._offset))
        goal = self._target() - self._line_centers[self._current]
        if abs(goal - self._offset) >= 0.5:
            self._anim_job = self.after(16, self._animate)

    def _on_wheel(self, event, delta=None):
        """Plain lyrics: the mouse wheel scrolls them."""
        if self._synced or not self._line_items or not self._lyrics_rect:
            return
        d = delta if delta is not None else event.delta
        step = 40 if d > 0 else -40
        x0, y0, x1, y1 = self._lyrics_rect
        lowest = min(0.0, (y1 - y0) - self._content_height - 24)
        self._scroll_to(max(lowest, min(0.0, self._offset + step)))

    def _find_lyrics(self):
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

    def _found(self, path, result):
        self._searching = False
        self.btn_find.configure(state="normal", text="Buscar letra")
        if path != self.path:
            return
        if result == "ok":
            self.lyrics_found(path)
            if self.app.player is not None:
                self.app.player.reload_lyrics()
        else:
            from tkinter import messagebox
            messagebox.showinfo("Buscar letra", "No se encontró la letra de esta canción." if result == "none"
                                else "No se pudo buscar la letra (¿sin conexión?).", parent=self)

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
            elif self._synced and item is not None and self._line_items:
                self._highlight(lyrics.current_line(self._lines, self.app.player.engine.position))
        self.after(200, self._tick)


def _short(text: str, n: int) -> str:
    text = "".join(c if ord(c) <= 0xFFFF else "□" for c in str(text or ""))
    return text if len(text) <= n else text[: n - 1] + "…"
