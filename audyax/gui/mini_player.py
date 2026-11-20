"""AUDYAX mini player (library and tools package): a small window always on top of the
others, with the cover, the song, previous / play-pause / next and the progress. The main
window hides while it's open; "expand" brings it back. It can be dragged anywhere and
remembers where it was. It controls the app's player bar; it doesn't play anything itself.

It's drawn on a canvas with the same background as "Reproduciendo" (the blurred cover, your
own image or the plain color, as chosen in Apariencia), so both always look alike."""

import tkinter as tk
import tkinter.font as tkfont

import customtkinter as ctk
from PIL import Image, ImageTk

from audyax.player.bar import _fit, tk_text

WIDTH, HEIGHT = 380, 92
COVER = 64
LIGHT_TEXT, LIGHT_MUTED = "#f6f3fb", "#cfc8dc"


class MiniPlayer(ctk.CTkToplevel):
    def __init__(self, app):
        from audyax.gui.style import C
        super().__init__(app)
        self.app, self.C = app, C
        self.overrideredirect(True)          # no title bar: it looks like a widget
        self.attributes("-topmost", True)
        self._drag = None
        self._shown_key = None
        self._photos = []
        self.canvas = tk.Canvas(self, width=WIDTH, height=HEIGHT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        for event, handler in (("<ButtonPress-1>", self._start_drag), ("<B1-Motion>", self._on_drag),
                               ("<ButtonRelease-1>", self._end_drag)):
            self.canvas.tag_bind("drag", event, handler)
        self.canvas.tag_bind("drag", "<Double-Button-1>", lambda _e: self.app._close_mini_player())
        self._place()
        self.after(30, self._tick)

    def _place(self):
        x, y = self.app.cfg.get("mini_pos") or (None, None)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if x is None or not (0 <= x <= sw - 40 and 0 <= y <= sh - 40):
            x, y = sw - WIDTH - 24, sh - HEIGHT - 72     # bottom right, above the taskbar
        self.geometry(f"{WIDTH}x{HEIGHT}+{int(x)}+{int(y)}")

    # ---------------------------------------------------------------- drawing
    def refresh(self):
        """Draws everything again (another song, another background or color scheme)."""
        self._shown_key = None
        self._tick(once=True)

    def _draw(self, item):
        from audyax.gui.now_playing import _rounded, _square, background_for
        from audyax.player.bar import _draw as draw_icon
        cv, C = self.canvas, self.C
        cover = item.get("image") if item else None
        bg, on_image = background_for(self.app, cover, WIDTH, HEIGHT)
        dark = ctk.get_appearance_mode() == "Dark"
        i = 1 if (on_image or dark) else 0
        accent = self.app.player_palette()["accent"][i] if hasattr(self.app, "player_palette") else C["accent"][i]
        text = LIGHT_TEXT if on_image else C["text"][i]
        muted = LIGHT_MUTED if on_image else C["muted"][i]
        self._colors = {"text": text, "muted": muted, "accent": accent, "track": "#8a8399" if on_image else C["hover"][i]}
        self._photos = []

        def photo(img):
            p = ImageTk.PhotoImage(img, master=self)
            self._photos.append(p)
            return p

        def icon(name, color, px):
            return photo(draw_icon(name, color, px * 3).resize((px, px), Image.LANCZOS))

        cv.delete("all")
        cv.create_image(0, 0, image=photo(bg), anchor="nw", tags=("drag",))
        if cover is not None:
            cv.create_image(12 + COVER // 2, 12 + COVER // 2, tags=("drag",),
                            image=photo(_rounded(_square(cover).resize((COVER, COVER), Image.LANCZOS), 8)))
        family = self.app.f_normal.cget("family")
        title_font = tkfont.Font(family=family, size=11, weight="bold")
        small_font = tkfont.Font(family=family, size=9)
        x = COVER + 24
        room = WIDTH - x - 44
        title = tk_text(item.get("title") or "") if item else "Nada sonando"
        artist = tk_text(item.get("artist") or "") if item else ""
        cv.create_text(x, 12, anchor="nw", text=_fit(title, title_font, room), fill=text, font=title_font,
                       tags=("drag",))
        cv.create_text(x, 32, anchor="nw", text=_fit(artist, small_font, room), fill=muted, font=small_font,
                       tags=("drag",))

        # buttons
        bar = self.app.player
        by = 66
        self._play_icons = {k: icon(k, "#1a1325" if on_image or dark else "#ffffff", 14) for k in ("play", "pause")}
        play_bg = text if on_image else accent
        cv.create_oval(x + 44 - 15, by - 15, x + 44 + 15, by + 15, fill=play_bg, outline="", tags=("b_play",))
        self._play_item = cv.create_image(x + 44, by, image=self._play_icons["pause"], tags=("b_play",))
        cv.create_image(x + 8, by, image=icon("previous", text, 16), tags=("b_prev",))
        cv.create_image(x + 80, by, image=icon("next", text, 16), tags=("b_next",))
        cv.create_text(WIDTH - 18, 18, text="⤢", fill=muted, font=(family, 14, "bold"), tags=("b_expand",))
        actions = {"b_play": self._toggle, "b_prev": lambda: bar and bar.jump(-1),
                   "b_next": lambda: bar and bar.jump(1), "b_expand": self.app._close_mini_player}
        for tag, command in actions.items():
            cv.tag_bind(tag, "<Button-1>", lambda _e, c=command: (c(), self._tick(once=True)))
            cv.tag_bind(tag, "<Enter>", lambda _e: cv.configure(cursor="hand2"))
            cv.tag_bind(tag, "<Leave>", lambda _e: cv.configure(cursor=""))

        # progress (bottom edge)
        cv.create_line(0, HEIGHT - 2, WIDTH, HEIGHT - 2, fill=self._colors["track"], width=3)
        self._progress = cv.create_line(0, HEIGHT - 2, 0, HEIGHT - 2, fill=accent, width=3)

    # ---------------------------------------------------------------- moving it
    def _start_drag(self, event):
        self._drag = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _on_drag(self, event):
        if self._drag:
            dx, dy = self._drag
            self.geometry(f"+{event.x_root - dx}+{event.y_root - dy}")

    def _end_drag(self, _event):
        if self._drag:
            self._drag = None
            self.app.cfg["mini_pos"] = [self.winfo_x(), self.winfo_y()]

    # ---------------------------------------------------------------- state
    def _toggle(self):
        if self.app.player is not None:
            self.app.player.toggle()

    def _tick(self, once=False):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        bar = self.app.player
        item = bar.item if bar is not None else None
        cfg = self.app.cfg
        key = (item["path"] if item else None, id(item.get("image")) if item else None,
               cfg.get("np_background"), cfg.get("background_image"),
               tuple(self.C["accent"]), ctk.get_appearance_mode())
        if key != self._shown_key:
            self._shown_key = key
            self._draw(item)
        if item is not None:
            playing = bar.engine.status == "playing"
            self.canvas.itemconfigure(self._play_item, image=self._play_icons["pause" if playing else "play"])
            duration = bar.engine.duration
            frac = max(0.0, min(1.0, bar.engine.position / duration)) if duration else 0.0
            self.canvas.coords(self._progress, 0, HEIGHT - 2, WIDTH * frac, HEIGHT - 2)
        if not once:
            self.after(300, self._tick)
