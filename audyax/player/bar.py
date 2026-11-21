"""
AUDYAX mini player bar: cover, title and artist, previous / play-pause / next, progress
bar for seeking, volume; drawn on a canvas over the player's background.

It plays through AudioEngine (audyax/player/engine.py), which knows nothing about the UI.
Together they are the base of the project's "part 2" (a full music player).

    pip install sounddevice numpy      (on Linux also: sudo apt install libportaudio2)
"""

import threading
import time
from pathlib import Path

try:
    import tkinter as tk

    import customtkinter as ctk
    from PIL import Image, ImageDraw, ImageTk
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
    elif name in ("repeat", "repeat_one"):
        w = max(2, n // 11)
        d.arc([p(.14, .22), p(.86, .78)], 200, 340, fill=color, width=w)
        d.arc([p(.14, .22), p(.86, .78)], 20, 160, fill=color, width=w)
        d.polygon([p(.74, .12), p(.92, .3), p(.7, .36)], fill=color)
        d.polygon([p(.26, .88), p(.08, .7), p(.3, .64)], fill=color)
        if name == "repeat_one":
            d.ellipse([p(.36, .32), p(.64, .68)], fill=color)
    elif name == "shuffle":
        w = max(2, n // 11)
        d.line([p(.08, .28), p(.3, .28), p(.62, .72), p(.8, .72)], fill=color, width=w, joint="curve")
        d.line([p(.08, .72), p(.3, .72), p(.62, .28), p(.8, .28)], fill=color, width=w, joint="curve")
        d.polygon([p(.78, .14), p(.96, .28), p(.78, .42)], fill=color)
        d.polygon([p(.78, .58), p(.96, .72), p(.78, .86)], fill=color)
    elif name == "mini":      # small window in the corner of a big one
        w = max(2, n // 12)
        d.rounded_rectangle([p(.06, .14), p(.94, .86)], radius=max(2, n // 10), outline=color, width=w)
        d.rounded_rectangle([p(.5, .5), p(.86, .78)], radius=max(1, n // 16), fill=color)
    elif name == "moon":
        d.ellipse([p(.14, .14), p(.86, .86)], fill=color)
        d.ellipse([p(.36, .02), p(1.02, .7)], fill=(0, 0, 0, 0))
    elif name == "sound":     # equalizer sliders
        w = max(2, n // 12)
        for x, knob in ((.22, .62), (.5, .32), (.78, .52)):
            d.line([p(x, .12), p(x, .88)], fill=color, width=w)
            d.ellipse([p(x - .1, knob - .1), p(x + .1, knob + .1)], fill=color)
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


INFO_WIDTH = 180   # px for the title and the artist (long ones scroll, like on a phone)
NARROW_BAR = 1000  # below this width (px) the volume slider hides
PROGRESS_MIN = 220  # px the progress bar always keeps, even in a narrow window
MARQUEE_MS = 140    # speed of the scrolling text
MARQUEE_PAUSE = 14  # ticks it stays still at the start of each loop
LIGHT_TEXT, LIGHT_MUTED = "#f6f3fb", "#cfc8dc"   # text over an image background


def _fit(text: str, font, width: int = INFO_WIDTH) -> str:
    """Cuts `text` with "…" so it fits in `width` pixels with `font` (a CTkFont)."""
    text = _short(text, 200)
    try:
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text.rstrip() + "…"
    except Exception:
        return _short(text, 36)


class PlayerBar(ctk.CTkFrame if ctk else object):
    """Mini player bar, drawn on a canvas so the player's background (the blurred cover, an
    image of your own or the plain color) shows behind everything, as in "Reproduciendo" and
    the floating mini player. The window passes it:
      colors: dict with "card", "text", "muted", "accent", "accent_hover", "on_accent", "hover",
              "border", "bg" (each one (light, dark), as customtkinter uses);
      fonts: dict with "bold", "normal", "small" (CTkFont);
      neighbor(item, step) -> the next (step=1) or previous (step=-1) item in the list, or None;
      on_volume_change(v) -> to remember the volume;
      on_close() -> the window hides the bar;
      on_track_change(item) -> optional, called each time another song starts (e.g. to highlight it);
      queue -> optional PlayQueue (audyax/player/queue.py): previous/next follow it instead of
               neighbor(), and the shuffle and repeat buttons appear;
      on_modes_change(shuffle, repeat) -> optional, to remember the shuffle/repeat buttons;
      cover_loader(item) -> optional, gives the cover of an item that doesn't bring one;
      on_open() -> optional, called when the cover or the title is clicked;
      on_sound() -> optional, shows a "Sonido" button (equalizer, transitions) that calls it;
      on_mini() -> optional, shows a button for the mini player;
      background(cover, w, h) -> optional, (PIL image, on_image) for the background.
    With a queue, the next song is prepared in advance and follows without a gap (or with
    the engine's crossfade). The sleep timer (sleep_in / sleep_after_song) stops the music.
    An "item" is a dict with: id, path, title, artist and, optionally, image (PIL.Image)."""

    HEIGHT = 72

    def __init__(self, master, colors, fonts, neighbor, on_volume_change=None, on_close=None,
                 volume=0.8, engine=None, on_track_change=None, queue=None, on_modes_change=None,
                 cover_loader=None, on_open=None, on_sound=None, on_mini=None, background=None):
        super().__init__(master, fg_color="transparent", corner_radius=0, height=self.HEIGHT)
        self.colors, self.fonts = colors, fonts
        self.neighbor, self.on_volume_change, self.on_close = neighbor, on_volume_change, on_close
        self.on_track_change = on_track_change
        self.queue, self.on_modes_change, self.cover_loader = queue, on_modes_change, cover_loader
        self.on_open, self.on_sound, self.on_mini = on_open, on_sound, on_mini
        self.background = background
        self._end_pending = threading.Event()
        self._advanced = threading.Event()      # the prepared next song started (audio thread)
        self.engine = engine or AudioEngine(on_finish=self._end_pending.set)
        self.engine.volume = volume
        if queue is not None:
            self.engine.peek_next = self._peek_next
            self.engine.on_advance = self._on_engine_advance
        self.sleep_deadline = None              # time.monotonic() when the music stops, or None
        self.sleep_after_song = False           # stop when the current song ends
        self.item = None
        self._lines = []
        self._dragging = None                   # "progress" | "volume" while dragging
        self._drag_pos = 0.0
        self._ids, self._bar_photos, self._marquee = {}, [], {}
        self._paint_job = None
        self.canvas = tk.Canvas(self, height=self.HEIGHT, highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._paint_later())
        self.after(250, self._refresh)
        self.after(MARQUEE_MS, self._marquee_tick)

    # ---------------------------------------------------------------- drawing
    def _paint_later(self, ms=60):
        if self._paint_job:
            self.after_cancel(self._paint_job)
        self._paint_job = self.after(ms, self._paint)

    def redraw(self):
        """Draws the bar again (another song, background, colors or size)."""
        self._paint()

    def _palette(self, on_image):
        dark = ctk.get_appearance_mode() == "Dark"
        i = 1 if (dark or on_image) else 0
        c = {k: (v[i] if isinstance(v, (tuple, list)) else v) for k, v in self.colors.items()}
        if on_image:
            c.update(text=LIGHT_TEXT, muted=LIGHT_MUTED, track="#8a8399", hover="#ffffff")
        else:
            c["track"] = c["hover"]
        return c

    def _paint(self):
        self._paint_job = None
        cv = self.canvas
        w, h = cv.winfo_width(), self.HEIGHT
        if w < 50:
            return
        scale = ctk.ScalingTracker.get_widget_scaling(self)
        wide = w >= int(NARROW_BAR * scale)
        cover = self.item.get("image") if self.item else None
        if self.background is not None and self.item:
            bg, on_image = self.background(cover, w, h)
        else:
            bg, on_image = None, False
        c = self._c = self._palette(on_image)
        i = 1 if ctk.get_appearance_mode() == "Dark" else 0
        under = self.colors.get("bg", self.colors["card"])
        under = under[i] if isinstance(under, (tuple, list)) else under
        if bg is None:
            bg = Image.new("RGB", (w, h), c["card"])
        # rounded corners over the window color, and a thin border
        frame = Image.new("RGB", (w, h), under)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=14, fill=255)
        frame.paste(bg.convert("RGB"), (0, 0), mask)
        if not on_image:
            ImageDraw.Draw(frame).rounded_rectangle((0, 0, w - 1, h - 1), radius=14,
                                                    outline=c.get("border", c["hover"]), width=1)
        self._bar_photos = []
        cv.delete("all")
        cv.configure(bg=under)
        ids = self._ids = {"wide": wide}
        cv.create_image(0, 0, image=self._bar_photo(frame), anchor="nw")

        mid = h // 2
        # cover
        if cover is not None:
            thumb = cover.convert("RGB")
            side = min(thumb.size)
            thumb = thumb.crop(((thumb.width - side) // 2, (thumb.height - side) // 2,
                                (thumb.width + side) // 2, (thumb.height + side) // 2)).resize((48, 48), Image.LANCZOS)
            m = Image.new("L", (48, 48), 0)
            ImageDraw.Draw(m).rounded_rectangle((0, 0, 47, 47), radius=8, fill=255)
            thumb.putalpha(m)
            cv.create_image(12 + 24, mid, image=self._bar_photo(thumb), tags=("open",))
        else:
            cv.create_rectangle(12, mid - 24, 60, mid + 24, fill=c["hover"], outline="", tags=("open",))
        # title and artist (they scroll if they don't fit)
        info_w = int((INFO_WIDTH if wide else INFO_WIDTH - 30) * scale)
        x = 72
        ids["title"] = cv.create_text(x, mid - 10, anchor="w", text="", fill=c["text"], font=self.fonts["bold"],
                                      tags=("open",))
        ids["artist"] = cv.create_text(x, mid + 11, anchor="w", text="", fill=c["muted"], font=self.fonts["small"],
                                       tags=("open",))
        if self.item:
            self._marquee_set("title", self.item.get("title") or Path(self.item["path"]).stem,
                              self.fonts["bold"], info_w)
            self._marquee_set("artist", self.item.get("artist") or "", self.fonts["small"], info_w)
        if self.on_open:
            self._bind_tag("open", self.on_open)

        # buttons
        bx = x + info_w + 24
        order = []
        if self.queue is not None and wide:
            order.append(("shuffle", self.toggle_shuffle))
        order += [("previous", lambda: self.jump(-1)), ("play", self.toggle), ("next", lambda: self.jump(1))]
        if self.queue is not None and wide:
            order.append(("repeat", self.cycle_repeat))
        for name, command in order:
            if name == "play":
                bx += 22
                cv.create_oval(bx - 20, mid - 20, bx + 20, mid + 20, fill=c["accent"], outline="", tags=("b_play",))
                ids["play"] = cv.create_image(bx, mid, tags=("b_play",))
                self._bind_tag("b_play", command)
                bx += 26
            else:
                bx += 18
                ids[name] = cv.create_image(bx, mid, tags=(f"b_{name}",))
                self._bind_tag(f"b_{name}", command)
                bx += 20
        left_end = bx + 16

        # right side: close, extras, volume
        rx = w - 22
        cv.create_text(rx, mid, text="✕", fill=c["muted"], font=self.fonts["small"], tags=("b_close",))
        self._bind_tag("b_close", self.close)
        rx -= 32
        if wide:
            extras = [("moon", lambda: self._sleep_menu(at=(cv.winfo_rootx() + int(ids["moon_x"]) - 40,
                                                            cv.winfo_rooty() - 150)))]
            if self.on_mini:
                extras.append(("mini", self.on_mini))
            if self.on_sound:
                extras.append(("sound", self.on_sound))
            for name, command in extras:
                ids[name] = cv.create_image(rx, mid, tags=(f"b_{name}",))
                if name == "moon":
                    ids["moon_x"] = rx
                    ids["sleep"] = cv.create_text(rx - 14, mid, anchor="e", text="", fill=c["accent"],
                                                  font=self.fonts["small"])
                self._bind_tag(f"b_{name}", command)
                rx -= 34
            rx -= 24      # room for the sleep text
            vx1 = rx
            vx0 = vx1 - int(90 * scale)
            ids["vol"] = (vx0, vx1, mid)
            ids["vol_track"] = cv.create_line(vx0, mid, vx1, mid, fill=c["track"], width=4, capstyle="round",
                                              tags=("vol",))
            ids["vol_fill"] = cv.create_line(vx0, mid, vx0, mid, fill=c["accent"], width=4, capstyle="round",
                                             tags=("vol",))
            ids["vol_knob"] = cv.create_oval(0, 0, 0, 0, fill=c["accent"], outline="", tags=("vol",))
            cv.create_rectangle(vx0 - 6, mid - 12, vx1 + 6, mid + 12, outline="", fill="", tags=("vol",))
            cv.tag_bind("vol", "<Button-1>", self._on_volume_drag)
            cv.tag_bind("vol", "<B1-Motion>", self._on_volume_drag)
            rx = vx0 - 16
        ids["speaker"] = cv.create_image(rx, mid, tags=("b_speaker",))
        self._bind_tag("b_speaker", self._toggle_mute)
        right_start = rx - 22

        # progress with times
        px0, px1 = left_end + 44, right_start - 44
        if px1 - px0 < 60:
            px1 = px0 + 60
        ids["prog"] = (px0, px1, mid)
        ids["elapsed"] = cv.create_text(px0 - 8, mid, anchor="e", text="0:00", fill=c["muted"], font=self.fonts["small"])
        ids["total"] = cv.create_text(px1 + 8, mid, anchor="w", text="", fill=c["muted"], font=self.fonts["small"])
        ids["track"] = cv.create_line(px0, mid, px1, mid, fill=c["track"], width=4, capstyle="round", tags=("prog",))
        ids["fill"] = cv.create_line(px0, mid, px0, mid, fill=c["accent"], width=4, capstyle="round", tags=("prog",))
        ids["knob"] = cv.create_oval(0, 0, 0, 0, fill=c["accent"], outline="", tags=("prog",))
        cv.create_rectangle(px0 - 6, mid - 12, px1 + 6, mid + 12, outline="", fill="", tags=("prog",))
        cv.tag_bind("prog", "<Button-1>", self._on_progress_drag)
        cv.tag_bind("prog", "<B1-Motion>", self._on_progress_drag)
        cv.tag_bind("prog", "<ButtonRelease-1>", self._on_progress_release)
        self._icons = {}
        self._sync()

    def _bar_photo(self, img):
        photo = ImageTk.PhotoImage(img, master=self)
        self._bar_photos.append(photo)
        return photo

    def _icon_img(self, name, color, px):
        key = (name, color, px)
        if key not in self._icons:
            self._icons[key] = self._bar_photo(_draw(name, color, px * 3).resize((px, px), Image.LANCZOS))
        return self._icons[key]

    def _bind_tag(self, tag, command):
        cv = self.canvas
        cv.tag_bind(tag, "<Button-1>", lambda _e: (command(), self._sync()))
        cv.tag_bind(tag, "<Enter>", lambda _e: cv.configure(cursor="hand2"))
        cv.tag_bind(tag, "<Leave>", lambda _e: cv.configure(cursor=""))

    def _sync(self):
        """Moves progress and volume, and shows play/pause, shuffle, repeat and the timer as they are."""
        ids, cv = self._ids, self.canvas
        if "prog" not in ids:
            return
        c = self._c
        px0, px1, y = ids["prog"]
        duration = self.engine.duration or 0
        pos = self._drag_pos if self._dragging == "progress" else self.engine.position
        frac = max(0.0, min(1.0, pos / duration)) if duration else 0.0
        x = px0 + (px1 - px0) * frac
        cv.coords(ids["fill"], px0, y, x, y)
        cv.coords(ids["knob"], x - 6, y - 6, x + 6, y + 6)
        cv.itemconfigure(ids["elapsed"], text=_format_time(pos))
        cv.itemconfigure(ids["total"], text=_format_time(duration) if duration else "")
        playing = self.engine.status == "playing"
        on_accent = c["on_accent"]
        cv.itemconfigure(ids["play"], image=self._icon_img("pause" if playing else "play", on_accent, 18))
        for name in ("previous", "next"):
            cv.itemconfigure(ids[name], image=self._icon_img(name, c["text"], 16))
        if self.queue is not None and "shuffle" in ids:
            q = self.queue
            cv.itemconfigure(ids["shuffle"], image=self._icon_img("shuffle", c["accent"] if q.shuffle else c["muted"], 15))
            name = "repeat_one" if q.repeat == "one" else "repeat"
            cv.itemconfigure(ids["repeat"], image=self._icon_img(name, c["accent"] if q.repeat != "off" else c["muted"], 15))
        for name in ("sound", "mini"):
            if name in ids:
                cv.itemconfigure(ids[name], image=self._icon_img(name, c["text"], 15))
        if "moon" in ids:
            active = bool(self.sleep_deadline or self.sleep_after_song)
            cv.itemconfigure(ids["moon"], image=self._icon_img("moon", c["accent"] if active else c["text"], 15))
            cv.itemconfigure(ids["sleep"], text=self.sleep_text())
        muted = self.engine.volume <= 0
        cv.itemconfigure(ids["speaker"], image=self._icon_img("volume", c["accent"] if muted else c["muted"], 16))
        if "vol" in ids:
            vx0, vx1, vy = ids["vol"]
            vx = vx0 + (vx1 - vx0) * self.engine.volume
            cv.coords(ids["vol_fill"], vx0, vy, vx, vy)
            cv.coords(ids["vol_knob"], vx - 6, vy - 6, vx + 6, vy + 6)

    # ---------------------------------------------------------------- dragging
    def _on_progress_drag(self, event):
        if not self.item or "prog" not in self._ids:
            return
        px0, px1, _y = self._ids["prog"]
        frac = max(0.0, min(1.0, (event.x - px0) / max(1, px1 - px0)))
        self._dragging, self._drag_pos = "progress", frac * (self.engine.duration or 0)
        self._sync()

    def _on_progress_release(self, _event):
        if self._dragging == "progress":
            self._dragging = None
            if self.item:
                self.engine.seek(self._drag_pos)
            self._sync()

    def _on_volume_drag(self, event):
        vx0, vx1, _y = self._ids["vol"]
        self.set_volume(max(0.0, min(1.0, (event.x - vx0) / max(1, vx1 - vx0))))

    def set_volume(self, value):
        self._change_volume(value)
        self._sync()

    # ---------------------------------------------------------------- playing
    def play(self, item: dict):
        """Starts playing `item`. Raises RuntimeError with the reason if it can't."""
        available, reason = audio_available() if self.engine._factory is SounddeviceOutput else (True, "")
        if not available:
            raise RuntimeError(reason)
        if not item.get("path") or not Path(item["path"]).exists():
            raise RuntimeError("el archivo ya no está en su carpeta")
        self._end_pending.clear()
        self._advanced.clear()
        self.engine.play(item["path"])
        self._show(item)

    def _show(self, item: dict):
        """Shows `item` as the song playing (title, cover, lyrics…) without touching the audio."""
        if item.get("image") is None and self.cover_loader:
            try:   # a copy: the queue doesn't keep a cover in memory for every song
                item = {**item, "image": self.cover_loader(item)}
            except Exception:
                pass
        self.item = item
        self.reload_lyrics()
        self._paint()
        if self.on_track_change:
            self.on_track_change(item)

    # ---------------------------------------------------------------- scrolling text
    def _marquee_set(self, key, text, font, room):
        """Shows `text`; if it doesn't fit in `room` pixels it scrolls sideways (with a pause
        at the start of each loop) instead of being cut."""
        text = _short(text, 300)
        self._marquee[key] = {"text": text, "font": font, "room": room, "pos": 0, "wait": MARQUEE_PAUSE}
        if key in self._ids:
            self.canvas.itemconfigure(self._ids[key], text=_clip(text, font, room))

    def _marquee_tick(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        for key, m in self._marquee.items():
            text, font, room = m["text"], m["font"], m["room"]
            try:
                if font.measure(text) <= room or key not in self._ids:
                    continue
            except Exception:
                continue
            if m["wait"] > 0:
                m["wait"] -= 1
                continue
            loop = text + "      "
            m["pos"] = (m["pos"] + 1) % len(loop)
            if m["pos"] == 0:
                m["wait"] = MARQUEE_PAUSE
            self.canvas.itemconfigure(self._ids[key], text=_clip(loop[m["pos"]:] + loop[:m["pos"]], font, room))
        self.after(MARQUEE_MS, self._marquee_tick)

    def reload_lyrics(self):
        """Reads the song's lyrics again (e.g. after they were just found and saved)."""
        self._lines = []
        if self.item:
            try:
                self._lines = lyrics.parse_lrc(lyrics.read_lyrics(self.item["path"]))
            except Exception:
                pass

    # ---------------------------------------------------------------- gapless (audio thread)
    def _peek_next(self, ahead: int):
        if self.sleep_after_song:
            return None
        try:
            item = self.queue.peek_next(ahead)
        except Exception:
            return None
        if item is None or not item.get("path") or not Path(item["path"]).is_file():
            return None   # a missing file: the song ends and jump() skips it
        return item["path"]

    def _on_engine_advance(self, path):
        """The prepared song started sounding. Normally it's the queue's next one; if the queue
        changed after it was prepared, the queue moves to the song that is actually heard."""
        try:   # right away (audio thread): the next peek counts from this song
            q = self.queue
            expected = q.peek_next(0)
            if expected is not None and expected.get("path") == path:
                q.advance()
            else:
                later = [k for k in range(q.index + 1, len(q.items)) if q.items[k].get("path") == path]
                if later:
                    q.jump(later[0])
                else:
                    q.advance()
        except Exception:
            pass
        self._advanced.set()

    # ---------------------------------------------------------------- sleep timer
    def sleep_in(self, minutes):
        """Stops (pauses) the music in `minutes`; None cancels the timer."""
        self.sleep_after_song = False
        self.sleep_deadline = time.monotonic() + minutes * 60 if minutes else None
        self._sync()

    def sleep_at_song_end(self):
        self.sleep_deadline = None
        self.sleep_after_song = True
        self._sync()

    def sleep_text(self) -> str:
        """"23 min", "40 s", "fin" (at the end of the song) or "" if the timer is off."""
        if self.sleep_after_song:
            return "fin"
        if self.sleep_deadline:
            left = max(0, self.sleep_deadline - time.monotonic())
            return f"{int(left // 60) + 1} min" if left >= 60 else f"{int(left)} s"
        return ""

    def _sleep_menu(self, at=None):
        """Sleep timer options; `at` = (x, y) on the screen where the menu opens."""
        i = 1 if ctk.get_appearance_mode() == "Dark" else 0
        c = {k: (v[i] if isinstance(v, (tuple, list)) else v) for k, v in self.colors.items()}
        menu = tk.Menu(self, tearoff=0, bg=c["card"], fg=c["text"], activebackground=c["hover"],
                       activeforeground=c["text"], relief="flat", borderwidth=1)
        for minutes in (15, 30, 45, 60, 90):
            menu.add_command(label=f"Detener en {minutes} min", command=lambda m=minutes: self.sleep_in(m))
        menu.add_command(label="Detener al terminar la canción", command=self.sleep_at_song_end)
        if self.sleep_deadline or self.sleep_after_song:
            menu.add_separator()
            menu.add_command(label="Cancelar temporizador", command=lambda: self.sleep_in(None))
        x, y = at or (self.winfo_rootx() + self.winfo_width() - 160, self.winfo_rooty() - 150)
        menu.tk_popup(x, y)

    def _sleep_now(self):
        """The timer ran out: pause (it can be resumed) and turn the timer off."""
        self.sleep_deadline, self.sleep_after_song = None, False
        if self.engine.status == "playing":
            self.engine.pause_playback()
        self._sync()

    # ---------------------------------------------------------------- colors
    def set_colors(self, colors: dict):
        """Paints the bar with other colors (color scheme, colors of the cover)."""
        self.colors = {**self.colors, **colors}
        self._paint()

    def refresh_icons(self):
        self._paint()

    def _toggle_mute(self):
        if self.engine.volume > 0:
            self._before_mute = self.engine.volume
            self._change_volume(0.0)
        else:
            self._change_volume(getattr(self, "_before_mute", 0.8) or 0.8)

    # ---------------------------------------------------------------- controls
    def toggle(self):
        if not self.item:
            return
        self.engine.toggle()
        self._sync()

    def show_state(self):
        """Shows play/pause as the engine is (after something paused it from outside)."""
        self._sync()

    def jump(self, step: int, auto: bool = False):
        """Previous (-1) or next (1). "Previous" after 3 s goes back to the start of the song.
        auto=True: the song ended by itself (with repeat "one" it plays again)."""
        if not self.item:
            return
        if step < 0 and self.engine.position > 3:
            self.engine.seek(0)
            return
        tries = (len(self.queue) if self.queue is not None else 0) + 50
        while tries > 0:
            tries -= 1
            another = self._neighbor(step, auto)
            if another is None:
                if step > 0:
                    self.engine.stop()
                    self._sync()
                return
            try:
                self.play(another)
                return
            except RuntimeError:
                self.item, auto = another, False  # that file is gone: move on to the next one

    def _neighbor(self, step, auto):
        if self.queue is not None:
            return self.queue.next(auto=auto) if step > 0 else self.queue.previous()
        return self.neighbor(self.item, step)

    def toggle_shuffle(self):
        self.queue.set_shuffle(not self.queue.shuffle)
        self._modes_changed()

    def cycle_repeat(self):
        self.queue.cycle_repeat()
        self._modes_changed()

    def _modes_changed(self):
        self._sync()
        if self.on_modes_change:
            self.on_modes_change(self.queue.shuffle, self.queue.repeat)

    def close(self):
        self.engine.stop()
        self.item = None
        if self.on_close:
            self.on_close()

    def _change_volume(self, value):
        self.engine.volume = float(value)
        if self.on_volume_change:
            self.on_volume_change(float(value))
        self._sync()

    # ---------------------------------------------------------------- refresh
    def _refresh(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self._advanced.is_set():      # the prepared song started: show it
            self._advanced.clear()
            current = self.queue.current if self.queue is not None else None
            if current is not None:
                self._show(current)
        if self._end_pending.is_set():   # the song ended by itself: play the next one
            self._end_pending.clear()
            if self.sleep_after_song:
                self._sleep_now()
            else:
                self.jump(1, auto=True)
        if self.sleep_deadline and time.monotonic() >= self.sleep_deadline:
            self._sleep_now()
        if self.item:
            self._sync()
        self.after(250, self._refresh)

    def destroy(self):
        self.engine.stop()
        super().destroy()


def tk_text(text) -> str:
    """Text that Tk can draw. "Decorative" letters (𝓣𝓲𝓽𝓪𝓷, 𝗕𝗼𝗹𝗱, ｆｕｌｌｗｉｄｔｈ…) become normal
    ones; what's left outside the basic plane (emojis) is removed, since Tk shows it as boxes."""
    import unicodedata
    out = []
    for ch in str(text or ""):
        if ord(ch) > 0xFFFF or 0xFF01 <= ord(ch) <= 0xFF5E:
            ch = unicodedata.normalize("NFKC", ch)
            if any(ord(c) > 0xFFFF for c in ch):
                continue
        out.append(ch)
    return " ".join("".join(out).split()) if out else ""


def _short(text: str, n: int) -> str:
    text = tk_text(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _clip(text: str, font, width: int) -> str:
    """The start of `text` that fits in `width` pixels (no "…": used by the scrolling text)."""
    try:
        if font.measure(text) <= width:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(text[:mid]) <= width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo]
    except Exception:
        return text[:30]
