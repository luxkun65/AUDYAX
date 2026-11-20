"""AUDYAX mini player (library and tools package): a small window always on top of the
others, with the cover, the song, previous / play-pause / next and the progress. The main
window hides while it's open; "expand" brings it back. It can be dragged anywhere and
remembers where it was. It controls the app's player bar; it doesn't play anything itself."""

import tkinter as tk

import customtkinter as ctk
from PIL import Image

WIDTH, HEIGHT = 380, 92
COVER = 64


class MiniPlayer(ctk.CTkToplevel):
    def __init__(self, app):
        from audyax.gui.style import C
        super().__init__(app)
        self.app, self.C = app, C
        self.overrideredirect(True)          # no title bar: it looks like a widget
        self.attributes("-topmost", True)
        self.configure(fg_color=C["border"])
        self._cover_for = None
        self._cover_img = None
        self._drag = None
        self._build()
        self._place()
        self.after(50, self._tick)

    # ---------------------------------------------------------------- building
    def _build(self):
        C, app = self.C, self.app
        from audyax.player.bar import _icon
        body = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=0)
        body.pack(fill="both", expand=True, padx=1, pady=1)
        self.body = body
        self.l_cover = ctk.CTkLabel(body, text="", width=COVER, height=COVER, fg_color=C["hover"], corner_radius=8)
        self.l_cover.place(x=12, y=12)
        self.l_title = ctk.CTkLabel(body, text="", font=app.f_bold, anchor="w", width=190, height=22)
        self.l_title.place(x=COVER + 22, y=10)
        self.l_artist = ctk.CTkLabel(body, text="", font=app.f_small, text_color=C["muted"], anchor="w",
                                     width=190, height=18)
        self.l_artist.place(x=COVER + 22, y=32)

        def button(name, command, x, accent=False, size=16):
            b = ctk.CTkButton(body, text="", image=_icon(name, C["on_accent"] if accent else C["text"], size),
                              width=30, height=30, corner_radius=15,
                              fg_color=C["accent"] if accent else "transparent",
                              hover_color=C["accent_hover"] if accent else C["hover"], command=command)
            b.place(x=x, y=52)
            return b
        bar = self.app.player
        self.ico_play = _icon("play", C["on_accent"], 16)
        self.ico_pause = _icon("pause", C["on_accent"], 16)
        button("previous", lambda: bar.jump(-1), COVER + 22)
        self.btn_play = button("pause", self._toggle, COVER + 60, accent=True)
        button("next", lambda: bar.jump(1), COVER + 98)
        # top-right: back to the big window / close the mini player
        expand = ctk.CTkButton(body, text="⤢", width=26, height=26, corner_radius=8, font=app.f_bold,
                               fg_color="transparent", hover_color=C["hover"], text_color=C["muted"],
                               command=self.app._close_mini_player)
        expand.place(x=WIDTH - 36, y=8)
        self.progress = tk.Canvas(body, height=3, highlightthickness=0, bd=0)
        self.progress.place(x=0, y=HEIGHT - 5, relwidth=1)
        for w in (body, self.l_title, self.l_artist, self.l_cover):
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<ButtonRelease-1>", self._end_drag)
            w.bind("<Double-Button-1>", lambda _e: self.app._close_mini_player())

    def _place(self):
        x, y = self.app.cfg.get("mini_pos") or (None, None)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if x is None or not (0 <= x <= sw - 40 and 0 <= y <= sh - 40):
            x, y = sw - WIDTH - 24, sh - HEIGHT - 72     # bottom right, above the taskbar
        self.geometry(f"{WIDTH}x{HEIGHT}+{int(x)}+{int(y)}")

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
            self._tick(once=True)

    def _tick(self, once=False):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        bar = self.app.player
        item = bar.item if bar is not None else None
        from audyax.player.bar import _fit
        if item is None:
            self.l_title.configure(text="Nada sonando")
            self.l_artist.configure(text="")
        else:
            room = int(190 * ctk.ScalingTracker.get_widget_scaling(self)) - 4
            self.l_title.configure(text=_fit(item.get("title") or "", self.app.f_bold, room))
            self.l_artist.configure(text=_fit(item.get("artist") or "", self.app.f_small, room))
            if item["path"] != self._cover_for:
                self._cover_for = item["path"]
                self._set_cover(item.get("image"))
            playing = bar.engine.status == "playing"
            self.btn_play.configure(image=self.ico_pause if playing else self.ico_play)
            self._draw_progress(bar.engine.position, bar.engine.duration)
        if not once:
            self.after(300, self._tick)

    def _set_cover(self, image):
        if image is None:
            self.l_cover.configure(image=None)
            return
        img = image.convert("RGB")
        side = min(img.size)
        img = img.crop(((img.width - side) // 2, (img.height - side) // 2,
                        (img.width + side) // 2, (img.height + side) // 2)).resize((COVER * 2, COVER * 2), Image.LANCZOS)
        self._cover_img = ctk.CTkImage(light_image=img, dark_image=img, size=(COVER, COVER))
        self.l_cover.configure(image=self._cover_img)

    def _draw_progress(self, position, duration):
        c = self.progress
        i = 1 if ctk.get_appearance_mode() == "Dark" else 0
        w = c.winfo_width()
        c.configure(bg=self.C["hover"][i])
        c.delete("all")
        if duration:
            c.create_rectangle(0, 0, w * max(0.0, min(1.0, position / duration)), 3, fill=self.C["accent"][i], width=0)
