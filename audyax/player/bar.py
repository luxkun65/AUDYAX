"""
AUDYAX mini player bar (customtkinter): cover, title, synced lyrics line,
previous / play-pause / next, progress bar for seeking, volume.

It plays through AudioEngine (audyax/player/engine.py), which knows nothing about the UI.
Together they are the base of the project's "part 2" (a full music player).

    pip install sounddevice numpy      (on Linux also: sudo apt install libportaudio2)
"""

import threading
import time
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
    """Mini player bar. The window passes it:
      colors: dict with "card", "text", "muted", "accent", "accent_hover", "on_accent", "hover"
              (each one (light, dark), as customtkinter uses);
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
      on_mini() -> optional, shows a button for the mini player.
    With a queue, the next song is prepared in advance and follows without a gap (or with
    the engine's crossfade). The sleep timer (sleep_in / sleep_after_song) stops the music.
    An "item" is a dict with: id, path, title, artist and, optionally, image (PIL.Image)."""

    def __init__(self, master, colors, fonts, neighbor, on_volume_change=None, on_close=None,
                 volume=0.8, engine=None, on_track_change=None, queue=None, on_modes_change=None,
                 cover_loader=None, on_open=None, on_sound=None, on_mini=None):
        super().__init__(master, fg_color=colors["card"], corner_radius=14, border_width=1,
                         border_color=colors.get("border", colors["card"]))
        self.colors, self.fonts = colors, fonts
        self.neighbor, self.on_volume_change, self.on_close = neighbor, on_volume_change, on_close
        self.on_track_change = on_track_change
        self.queue, self.on_modes_change, self.cover_loader = queue, on_modes_change, cover_loader
        self.on_open, self.on_sound, self.on_mini = on_open, on_sound, on_mini
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
        self._dragging = False
        self._build()
        self.after(250, self._refresh)

    # ---------------------------------------------------------------- building
    def _button(self, parent, name, command, size=18, accent=False):
        c = self.colors
        button = ctk.CTkButton(parent, text="", width=size + 18, height=size + 14, corner_radius=(size + 14) // 2,
                             image=_icon(name, c["on_accent"] if accent else c["text"], size),
                             fg_color=c["accent"] if accent else "transparent",
                             hover_color=c["accent_hover"] if accent else c["hover"], command=command)
        self.__dict__.setdefault("_icon_buttons", []).append((button, name, size, accent))
        return button

    def _build(self):
        c, f = self.colors, self.fonts
        self.grid_columnconfigure(3, weight=1, minsize=PROGRESS_MIN)   # only the progress bar stretches
        self.l_cover = ctk.CTkLabel(self, text="", width=48, height=48, fg_color=c["hover"], corner_radius=8)
        self.l_cover.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=10)
        # title and artist in a box of fixed width: the buttons don't move when the song
        # changes (longer texts are cut with "…")
        info = ctk.CTkFrame(self, fg_color="transparent", width=INFO_WIDTH, height=48)
        info.grid(row=0, column=1, rowspan=2, sticky="w", pady=10)
        info.grid_propagate(False)
        info.grid_columnconfigure(0, weight=1)
        self.l_title = ctk.CTkLabel(info, text="", font=f["bold"], anchor="w", width=INFO_WIDTH, height=24)
        self.l_title.grid(row=0, column=0, sticky="sw")
        self.l_sub = ctk.CTkLabel(info, text="", font=f["small"], text_color=c["muted"], anchor="w",
                                  width=INFO_WIDTH, height=20)
        self.l_sub.grid(row=1, column=0, sticky="nw")
        if self.on_open:
            for w in (self.l_cover, self.l_title, self.l_sub):
                w.configure(cursor="hand2")
                w.bind("<Button-1>", lambda _e: self.on_open())

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=0, column=2, rowspan=2, padx=10)
        if self.queue is not None:
            self.btn_shuffle = self._button(buttons, "shuffle", self.toggle_shuffle, 14)
            self.btn_shuffle.pack(side="left", padx=(0, 4))
        self._button(buttons, "previous", lambda: self.jump(-1), 14).pack(side="left", padx=2)
        self.btn_play = self._button(buttons, "pause", self.toggle, 18, accent=True)
        self.btn_play.pack(side="left", padx=4)
        self._button(buttons, "next", lambda: self.jump(1), 14).pack(side="left", padx=2)
        if self.queue is not None:
            self.btn_repeat = self._button(buttons, "repeat", self.cycle_repeat, 14)
            self.btn_repeat.pack(side="left", padx=(4, 0))
            self._show_modes()
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
        self.l_volume_icon = ctk.CTkLabel(vol, text="", image=_icon("volume", c["muted"], 16))
        self.l_volume_icon.pack(side="left", padx=(0, 4))
        self.s_volume = ctk.CTkSlider(vol, from_=0, to=1, width=90, height=16, command=self._change_volume,
                                       progress_color=c["accent"], button_color=c["accent"],
                                       button_hover_color=c["accent_hover"])
        self.s_volume.set(self.engine.volume)
        self.s_volume.pack(side="left")
        # narrow window: the volume slider hides so the progress bar keeps its room;
        # a click on the speaker mutes / unmutes
        self.l_volume_icon.configure(cursor="hand2")
        self.l_volume_icon.bind("<Button-1>", lambda _e: self._toggle_mute())
        self._hidden = {}
        self.bind("<Configure>", self._on_resize, add="+")
        extra = self._extra = ctk.CTkFrame(self, fg_color="transparent")
        extra.grid(row=0, column=6, rowspan=2, padx=(2, 0))
        self._optional = []          # hidden when the window is narrow (they're in "Reproduciendo" too)
        if self.on_sound:
            self.btn_sound = self._button(extra, "sound", self.on_sound, 14)
            self.btn_sound.pack(side="left")
            self._optional.append(self.btn_sound)
        if self.on_mini:
            self.btn_mini = self._button(extra, "mini", self.on_mini, 14)
            self.btn_mini.pack(side="left")
            self._optional.append(self.btn_mini)
        self.btn_sleep = self._button(extra, "moon", self._sleep_menu, 14)
        self.btn_sleep.pack(side="left")
        self.l_sleep = ctk.CTkLabel(extra, text="", font=f["small"], text_color=c["accent"])
        self._button(self, "close", self.close, 12).grid(row=0, column=7, rowspan=2, padx=(4, 10))

    # ---------------------------------------------------------------- actions
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
        room = int(INFO_WIDTH * ctk.ScalingTracker.get_widget_scaling(self)) - 4   # real pixels of the box
        self._marquee_set(self.l_title, item.get("title") or Path(item["path"]).stem, self.fonts["bold"], room)
        self._artist = item.get("artist") or ""
        self._marquee_set(self.l_sub, self._artist, self.fonts["small"], room)
        self.l_sub.configure(text_color=self.colors["muted"])
        self.l_total.configure(text=_format_time(self.engine.duration))
        self.bar.configure(to=max(1.0, self.engine.duration))
        self._set_cover(item.get("image"))
        self.reload_lyrics()
        self.btn_play.configure(image=self.ico_pause)
        if self.on_track_change:
            self.on_track_change(item)

    # ---------------------------------------------------------------- scrolling text
    def _marquee_set(self, label, text, font, room):
        """Shows `text` in `label`; if it doesn't fit it scrolls sideways (with a pause at the
        start of each loop) instead of being cut."""
        text = _short(text, 300)
        marquee = self.__dict__.setdefault("_marquee", {})
        marquee[label] = {"text": text, "font": font, "room": room, "pos": 0, "wait": MARQUEE_PAUSE}
        label.configure(text=_clip(text, font, room))
        if not getattr(self, "_marquee_running", False):
            self._marquee_running = True
            self.after(MARQUEE_MS, self._marquee_tick)

    def _marquee_tick(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        for label, m in self.__dict__.get("_marquee", {}).items():
            text, font, room = m["text"], m["font"], m["room"]
            try:
                if font.measure(text) <= room:
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
            label.configure(text=_clip(loop[m["pos"]:] + loop[:m["pos"]], font, room))
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
        self._show_sleep()

    def sleep_at_song_end(self):
        self.sleep_deadline = None
        self.sleep_after_song = True
        self._show_sleep()

    def _sleep_menu(self, at=None):
        """Sleep timer options; `at` = (x, y) on the screen where the menu opens."""
        import tkinter as tk
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
        x, y = at or (self.btn_sleep.winfo_rootx(), self.btn_sleep.winfo_rooty() - 10)
        menu.tk_popup(x, y)

    def _show_sleep(self):
        c = self.colors
        active = bool(self.sleep_deadline or self.sleep_after_song)
        self.btn_sleep.configure(image=_icon("moon", c["accent"] if active else c["text"], 14))
        if self.sleep_after_song:
            self.l_sleep.configure(text="fin")
        elif self.sleep_deadline:
            left = max(0, self.sleep_deadline - time.monotonic())
            self.l_sleep.configure(text=f"{int(left // 60) + 1} min" if left >= 60 else f"{int(left)} s")
        if active:
            self.l_sleep.pack(side="left", padx=(0, 4))
        else:
            self.l_sleep.pack_forget()

    def _sleep_now(self):
        """The timer ran out: pause (it can be resumed) and turn the timer off."""
        self.sleep_deadline, self.sleep_after_song = None, False
        if self.engine.status == "playing":
            self.engine.pause_playback()
            self.btn_play.configure(image=self.ico_play)
        self._show_sleep()

    def set_colors(self, colors: dict):
        """Paints the whole bar with `colors` (same keys as at creation). Every widget gets its
        color from the key it uses, so the result never depends on the previous colors."""
        self.colors = c = {**self.colors, **colors}
        self.configure(fg_color=c["card"], border_color=c.get("border", c["card"]))
        self.l_cover.configure(fg_color=c["hover"])
        self.l_title.configure(text_color=c["text"])
        self.l_sub.configure(text_color=c["muted"])
        for label in (self.l_time, self.l_total):
            label.configure(text_color=c["muted"])
        self.l_sleep.configure(text_color=c["accent"])
        for slider in (self.bar, self.s_volume):
            slider.configure(progress_color=c["accent"], button_color=c["accent"],
                             button_hover_color=c["accent_hover"])
        for button, _name, _size, accent in getattr(self, "_icon_buttons", []):
            button.configure(fg_color=c["accent"] if accent else "transparent",
                             hover_color=c["accent_hover"] if accent else c["hover"])
        self.refresh_icons()

    def _on_resize(self, _event=None):
        """Narrow window: the progress bar keeps its room; volume slider, shuffle, repeat,
        sound, mini player and sleep timer hide (they're also in "Reproduciendo")."""
        wide = self.winfo_width() >= int(NARROW_BAR * ctk.ScalingTracker.get_widget_scaling(self))
        if wide == getattr(self, "_wide", True):
            return
        self._wide = wide
        widgets = [self.s_volume, self.btn_sleep] + self._optional
        if self.queue is not None:
            widgets += [self.btn_shuffle, self.btn_repeat]
        for w in widgets:
            if wide:
                info, before = self._hidden.pop(w, (None, None))
                if info is not None:
                    options = {k: v for k, v in info.items() if k != "in"}
                    if before is not None and before.winfo_manager() == "pack":
                        options["before"] = before
                    w.pack(**options)
            elif w.winfo_manager() == "pack":
                siblings = w.master.pack_slaves()
                after = siblings[siblings.index(w) + 1:] if w in siblings else []
                self.__dict__.setdefault("_hidden", {})[w] = (w.pack_info(), after[0] if after else None)
                w.pack_forget()
        if wide:
            self._extra.grid()
            self._show_sleep()
        else:
            self._extra.grid_remove()     # an empty frame keeps its width: take it out too

    def _toggle_mute(self):
        if self.engine.volume > 0:
            self._before_mute = self.engine.volume
            self._change_volume(0.0)
        else:
            self._change_volume(getattr(self, "_before_mute", 0.8) or 0.8)
        self.s_volume.set(self.engine.volume)

    def refresh_icons(self):
        """Draws the icons again with self.colors (after a color change)."""
        c = self.colors
        for button, name, size, accent in getattr(self, "_icon_buttons", []):
            try:
                button.configure(image=_icon(name, c["on_accent"] if accent else c["text"], size))
            except Exception:
                pass
        self.ico_play = _icon("play", c["on_accent"], 18)
        self.ico_pause = _icon("pause", c["on_accent"], 18)
        self.btn_play.configure(image=self.ico_pause if self.engine.status == "playing" else self.ico_play)
        self.l_volume_icon.configure(image=_icon("volume", c["muted"], 16))
        if self.queue is not None:
            self._show_modes()
        self._show_sleep()

    def toggle(self):
        if not self.item:
            return
        self.engine.toggle()
        self.btn_play.configure(image=self.ico_pause if self.engine.status == "playing" else self.ico_play)

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
                    self.btn_play.configure(image=self.ico_play)
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
        self._show_modes()
        if self.on_modes_change:
            self.on_modes_change(self.queue.shuffle, self.queue.repeat)

    def _show_modes(self):
        c, q = self.colors, self.queue
        self.btn_shuffle.configure(image=_icon("shuffle", c["accent"] if q.shuffle else c["muted"], 14))
        self.btn_repeat.configure(image=_icon("repeat_one" if q.repeat == "one" else "repeat",
                                              c["accent"] if q.repeat != "off" else c["muted"], 14))

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
        if self._advanced.is_set():      # the prepared song started: show it
            self._advanced.clear()
            current = self.queue.current if self.queue is not None else None
            if current is not None:
                self._show(current)
        if self._end_pending.is_set():   # the song ended by itself: play the next one
            self._end_pending.clear()
            if self.sleep_after_song:
                self._sleep_now()
                self.btn_play.configure(image=self.ico_play)
            else:
                self.jump(1, auto=True)
        if self.sleep_deadline:
            if time.monotonic() >= self.sleep_deadline:
                self._sleep_now()
            else:
                self._show_sleep()
        if self.item:
            pos = self.engine.position
            if not self._dragging:
                self.bar.set(min(pos, self.engine.duration or pos))
            self.l_time.configure(text=_format_time(self.bar.get() if self._dragging else pos))
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
