"""
AUDYAX mini player bar (customtkinter): cover, title, synced lyrics line,
previous / play-pause / next, progress bar for seeking, volume.

It plays through AudioEngine (audyax/player/engine.py), which knows nothing about the UI.
Together they are the base of the project's "part 2" (a full music player).

    pip install sounddevice numpy      (on Linux also: sudo apt install libportaudio2)
"""

import threading
from pathlib import Path

try:
    import customtkinter as ctk
    from PIL import Image, ImageDraw
except ImportError:  # the helpers can be imported (and tested) without a UI
    ctk = None

from audyax.core import lyrics
from audyax.player.engine import AudioEngine, SounddeviceOutput, audio_available


def _format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _draw(name: str, color: str, n: int = 64) -> "Image.Image":
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def p(x, y):
        return (x * n, y * n)
    if name == "play":
        d.polygon([p(.28, .16), p(.84, .5), p(.28, .84)], fill=color)
    elif name == "pause":
        d.rounded_rectangle([p(.24, .16), p(.42, .84)], radius=n * .05, fill=color)
        d.rounded_rectangle([p(.58, .16), p(.76, .84)], radius=n * .05, fill=color)
    elif name in ("previous", "next"):
        if name == "next":
            d.polygon([p(.18, .2), p(.66, .5), p(.18, .8)], fill=color)
            d.rectangle([p(.68, .2), p(.8, .8)], fill=color)
        else:
            d.polygon([p(.82, .2), p(.34, .5), p(.82, .8)], fill=color)
            d.rectangle([p(.2, .2), p(.32, .8)], fill=color)
    elif name == "volume":
        d.polygon([p(.12, .38), p(.32, .38), p(.54, .18), p(.54, .82), p(.32, .62), p(.12, .62)], fill=color)
        d.arc([p(.46, .3), p(.76, .7)], -55, 55, fill=color, width=max(2, n // 14))
        d.arc([p(.46, .16), p(.92, .84)], -55, 55, fill=color, width=max(2, n // 14))
    elif name == "close":
        w = max(2, n // 10)
        d.line([p(.25, .25), p(.75, .75)], fill=color, width=w)
        d.line([p(.75, .25), p(.25, .75)], fill=color, width=w)
    return im


def _icon(name: str, colors, size: int):
    """Icon with a light and a dark version (colors = (light, dark))."""
    light, dark = colors if isinstance(colors, (tuple, list)) else (colors, colors)
    return ctk.CTkImage(light_image=_draw(name, light).resize((size * 2, size * 2), Image.LANCZOS),
                        dark_image=_draw(name, dark).resize((size * 2, size * 2), Image.LANCZOS),
                        size=(size, size))


class PlayerBar(ctk.CTkFrame if ctk else object):
    """Mini player bar. The window passes it:
      colors: dict with "card", "text", "muted", "accent", "accent_hover", "on_accent", "hover"
              (each one (light, dark), as customtkinter uses);
      fonts: dict with "bold", "normal", "small" (CTkFont);
      neighbor(item, step) -> the next (step=1) or previous (step=-1) item in the list, or None;
      on_volume_change(v) -> to remember the volume;
      on_close() -> the window hides the bar;
      on_track_change(item) -> optional, called each time another song starts (e.g. to highlight it).
    An "item" is a dict with: id, path, title, artist and, optionally, image (PIL.Image)."""

    def __init__(self, master, colors, fonts, neighbor, on_volume_change=None, on_close=None,
                 volume=0.8, engine=None, on_track_change=None):
        super().__init__(master, fg_color=colors["card"], corner_radius=14, border_width=1,
                         border_color=colors.get("border", colors["card"]))
        self.colors, self.fonts = colors, fonts
        self.neighbor, self.on_volume_change, self.on_close = neighbor, on_volume_change, on_close
        self.on_track_change = on_track_change
        self._end_pending = threading.Event()
        self.engine = engine or AudioEngine(on_finish=self._end_pending.set)
        self.engine.volume = volume
        self.item = None
        self._lines = []
        self._dragging = False
        self._build()
        self.after(250, self._refresh)

    # ---------------------------------------------------------------- building
    def _button(self, parent, name, command, size=18, accent=False):
        c = self.colors
        return ctk.CTkButton(parent, text="", width=size + 18, height=size + 14, corner_radius=(size + 14) // 2,
                             image=_icon(name, c["on_accent"] if accent else c["text"], size),
                             fg_color=c["accent"] if accent else "transparent",
                             hover_color=c["accent_hover"] if accent else c["hover"], command=command)

    def _build(self):
        c, f = self.colors, self.fonts
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(3, weight=2)
        self.l_cover = ctk.CTkLabel(self, text="", width=48, height=48, fg_color=c["hover"], corner_radius=8)
        self.l_cover.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=10)
        self.l_title = ctk.CTkLabel(self, text="", font=f["bold"], anchor="w")
        self.l_title.grid(row=0, column=1, sticky="sw", pady=(10, 0))
        self.l_sub = ctk.CTkLabel(self, text="", font=f["small"], text_color=c["muted"], anchor="w")
        self.l_sub.grid(row=1, column=1, sticky="nw", pady=(0, 10))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=0, column=2, rowspan=2, padx=10)
        self._button(buttons, "previous", lambda: self.jump(-1), 14).pack(side="left", padx=2)
        self.btn_play = self._button(buttons, "pause", self.toggle, 18, accent=True)
        self.btn_play.pack(side="left", padx=4)
        self._button(buttons, "next", lambda: self.jump(1), 14).pack(side="left", padx=2)
        self.ico_play = _icon("play", c["on_accent"], 18)
        self.ico_pause = _icon("pause", c["on_accent"], 18)

        progress = ctk.CTkFrame(self, fg_color="transparent")
        progress.grid(row=0, column=3, rowspan=2, sticky="ew", padx=6)
        progress.grid_columnconfigure(1, weight=1)
        self.l_time = ctk.CTkLabel(progress, text="0:00", width=40, font=f["small"], text_color=c["muted"])
        self.l_time.grid(row=0, column=0)
        self.bar = ctk.CTkSlider(progress, from_=0, to=1, number_of_steps=None, height=16,
                                   progress_color=c["accent"], button_color=c["accent"],
                                   button_hover_color=c["accent_hover"])
        self.bar.set(0)
        self.bar.grid(row=0, column=1, sticky="ew", padx=6)
        self.bar.bind("<Button-1>", lambda e: setattr(self, "_dragging", True), add=True)
        self.bar.bind("<ButtonRelease-1>", self._release_bar, add=True)
        self.l_total = ctk.CTkLabel(progress, text="0:00", width=40, font=f["small"], text_color=c["muted"])
        self.l_total.grid(row=0, column=2)

        vol = ctk.CTkFrame(self, fg_color="transparent")
        vol.grid(row=0, column=4, rowspan=2, padx=6)
        ctk.CTkLabel(vol, text="", image=_icon("volume", c["muted"], 16)).pack(side="left", padx=(0, 4))
        self.s_volume = ctk.CTkSlider(vol, from_=0, to=1, width=90, height=16, command=self._change_volume,
                                       progress_color=c["accent"], button_color=c["accent"],
                                       button_hover_color=c["accent_hover"])
        self.s_volume.set(self.engine.volume)
        self.s_volume.pack(side="left")
        self._button(self, "close", self.close, 12).grid(row=0, column=5, rowspan=2, padx=(4, 10))

    # ---------------------------------------------------------------- actions
    def play(self, item: dict):
        """Starts playing `item`. Raises RuntimeError with the reason if it can't."""
        available, reason = audio_available() if self.engine._factory is SounddeviceOutput else (True, "")
        if not available:
            raise RuntimeError(reason)
        if not item.get("path") or not Path(item["path"]).exists():
            raise RuntimeError("el archivo ya no está en su carpeta")
        self.item = item
        self._end_pending.clear()
        self.engine.play(item["path"])
        self.l_title.configure(text=_short(item.get("title") or Path(item["path"]).stem, 42))
        self._artist = _short(item.get("artist") or "", 42)
        self.l_sub.configure(text=self._artist, text_color=self.colors["muted"])
        self.l_total.configure(text=_format_time(self.engine.duration))
        self.bar.configure(to=max(1.0, self.engine.duration))
        self._set_cover(item.get("image"))
        self._lines = []
        try:
            self._lines = lyrics.parse_lrc(lyrics.read_lyrics(item["path"]))
        except Exception:
            pass
        self.btn_play.configure(image=self.ico_pause)
        if self.on_track_change:
            self.on_track_change(item)

    def toggle(self):
        if not self.item:
            return
        self.engine.toggle()
        self.btn_play.configure(image=self.ico_pause if self.engine.status == "playing" else self.ico_play)

    def jump(self, step: int):
        """Previous (-1) or next (1). "Previous" after 3 s goes back to the start of the song."""
        if not self.item:
            return
        if step < 0 and self.engine.position > 3:
            self.engine.seek(0)
            return
        another = self.neighbor(self.item, step)
        if another is None:
            if step > 0:
                self.engine.stop()
                self.btn_play.configure(image=self.ico_play)
            return
        try:
            self.play(another)
        except RuntimeError:
            self.item = another
            self.jump(step)  # if that file no longer exists, move on to the next one

    def close(self):
        self.engine.stop()
        self.item = None
        if self.on_close:
            self.on_close()

    # ---------------------------------------------------------------- refresh
    def _set_cover(self, image):
        if image is None:
            self.l_cover.configure(image=None)
            return
        try:
            img = image.convert("RGB").resize((96, 96), Image.LANCZOS)
            self._cover_img = ctk.CTkImage(light_image=img, dark_image=img, size=(48, 48))
            self.l_cover.configure(image=self._cover_img)
        except Exception:
            pass

    def _change_volume(self, value):
        self.engine.volume = float(value)
        if self.on_volume_change:
            self.on_volume_change(float(value))

    def _release_bar(self, _event=None):
        self._dragging = False
        if self.item:
            self.engine.seek(float(self.bar.get()))

    def _refresh(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self._end_pending.is_set():   # the song ended by itself: play the next one
            self._end_pending.clear()
            self.jump(1)
        if self.item:
            pos = self.engine.position
            if not self._dragging:
                self.bar.set(min(pos, self.engine.duration or pos))
            self.l_time.configure(text=_format_time(self.bar.get() if self._dragging else pos))
            if self._lines:
                i = lyrics.current_line(self._lines, pos)
                text = self._lines[i][1] if i >= 0 and self._lines[i][1] else "♪"
                self.l_sub.configure(text=_short(text, 60), text_color=self.colors["accent"])
        self.after(250, self._refresh)

    def destroy(self):
        self.engine.stop()
        super().destroy()


def _short(text: str, n: int) -> str:
    text = "".join(c if ord(c) <= 0xFFFF else "□" for c in str(text or ""))  # Tk doesn't show emojis
    return text if len(text) <= n else text[: n - 1] + "…"
