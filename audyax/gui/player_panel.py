"""Playback in the main window: one player bar for the whole window, used by the download
list and by the player view (audyax/gui/player_view.py). Double-clicking the sidebar logo
switches between the downloader and the player, with an animation."""

from pathlib import Path
from tkinter import messagebox

from audyax.gui.style import C
from audyax.player.bar import PlayerBar

DOWNLOADS, PLAYER = "downloads", "player"


class PlayerMixin:
    """Player bar and downloader/player switching. Mixed into App."""

    # ---------------------------------------------------------------- download list
    def _is_playable(self, f) -> bool:
        return f["color"] == "green" and bool(f["path"]) and Path(f["path"]).exists()

    def _on_double_click(self, vid):
        """Double click: if it's downloaded, it plays; otherwise its link opens."""
        f = self.rows.get(vid)
        if f and self._is_playable(f):
            self._play(vid)
        else:
            self._open_link(vid)

    def _player_item(self, vid) -> dict:
        f = self.rows[vid]
        return {"id": vid, "path": f["path"], "title": f["title"], "artist": f["artist"],
                "album": f["album"], "year": f["year"], "image": f["img_pil"], "source": "list"}

    def _playable_in_view(self):
        return [v for v in self._rows_in_view() if self._is_playable(self.rows[v])]

    def _play(self, vid):
        """Plays a downloaded song; the queue becomes the downloaded songs of the list as shown."""
        ids = self._playable_in_view()
        if vid not in ids:
            ids = [vid]
        self._play_queue([self._player_item(v) for v in ids], ids.index(vid))

    # ---------------------------------------------------------------- queue (shared)
    def _play_queue(self, items, start: int = 0):
        """Replaces the queue with `items` and plays items[start]."""
        item = self.play_queue.set(items, start)
        if item is not None:
            self._play_item(item)
        self._queue_changed()

    def _queue_play_next(self, items):
        self.play_queue.play_next(items)
        self._start_if_idle()
        self._queue_changed()

    def _queue_add(self, items):
        self.play_queue.add(items)
        self._start_if_idle()
        self._queue_changed()

    def _start_if_idle(self):
        """Adding to an empty player starts it."""
        if self.player is None or self.player.item is None:
            item = self.play_queue.next()
            if item is not None:
                self._play_item(item)

    def _queue_jump(self, position: int):
        item = self.play_queue.jump(position)
        if item is not None:
            self._play_item(item)

    def _queue_remove(self, positions):
        for pos in sorted(positions, reverse=True):
            self.play_queue.remove(pos)
        self._queue_changed()

    def _queue_clear(self):
        self.play_queue.clear_upcoming()
        self._queue_changed()

    def _queue_changed(self):
        if self.player_view is not None:
            self.player_view.queue_changed()
        if self.now_playing is not None:
            self.now_playing.queue_changed()

    # ---------------------------------------------------------------- player bar (shared)
    def _ensure_player(self):
        if self.player is not None:
            return
        from audyax.gui.player_view import load_cover
        colors = {k: C[k] for k in ("card", "text", "muted", "accent", "accent_hover",
                                     "on_accent", "hover", "border")}
        fonts = {"bold": self.f_bold, "normal": self.f_normal, "small": self.f_small}
        self.player = PlayerBar(
            self, colors, fonts, neighbor=lambda _item, _step: None, queue=self.play_queue,
            on_volume_change=lambda v: self.cfg.__setitem__("volume", round(v, 2)),
            on_close=self._on_player_closed, on_track_change=self._on_track_change,
            on_modes_change=self._on_modes_change, cover_loader=lambda item: load_cover(item["path"]),
            on_open=self._open_now_playing, on_sound=self._open_sound,
            volume=float(self.cfg.get("volume", 0.8)))
        self._apply_sound_settings()

    def _play_item(self, item: dict):
        """Plays one song. The bar sits below both views (downloader and player), so the
        music goes on when switching between them."""
        self._ensure_player()
        try:
            self.player.play(item)
        except RuntimeError as e:
            messagebox.showwarning("No se puede reproducir", f"No se pudo reproducir la canción: {e}.")
            return
        self.player.grid(row=1, column=1, sticky="ew", padx=(14, 16), pady=(0, 12))

    # ---------------------------------------------------------------- sound settings
    def _apply_sound_settings(self):
        """Equalizer and transition between songs, from the settings, to the engine."""
        if self.player is None:
            return
        engine = self.player.engine
        engine.crossfade = float(self.cfg.get("crossfade", 0) or 0)
        engine.set_equalizer(self.cfg.get("eq_gains") if self.cfg.get("eq_enabled") else None)

    def _open_sound(self):
        from audyax.gui.sound_window import SoundWindow
        w = getattr(self, "sound_window", None)
        if w is not None and w.winfo_exists():
            w.lift()
            w.focus()
            return
        self.sound_window = SoundWindow(self)

    # ---------------------------------------------------------------- automatic lyrics
    def _auto_lyrics(self, item):
        """A song without lyrics starts: look them up in the background (once per session)."""
        path = item.get("path")
        tried = self.__dict__.setdefault("_lyrics_tried", set())
        if not path or path in tried or not self.cfg.get("auto_lyrics", False):
            return
        tried.add(path)
        import threading
        from audyax.core import lyrics
        if lyrics.read_lyrics(path):
            return
        title, artist, album = item.get("title") or "", item.get("artist") or "", item.get("album") or ""
        duration = self.player.engine.duration if self.player is not None else None
        mode, proxy = self.cfg.get("lyrics", "off"), self.cfg.get("proxy", "")

        def work():
            result = lyrics.fetch_and_save(path, title, artist, album, duration or None, mode, proxy)
            if result == "ok":
                self.after(0, lambda: self._lyrics_found(path))
        threading.Thread(target=work, daemon=True).start()

    def _lyrics_found(self, path):
        if self.player is not None and self.player.item and self.player.item["path"] == path:
            self.player.reload_lyrics()
        if self.now_playing is not None:
            self.now_playing.lyrics_found(path)

    # ---------------------------------------------------------------- dynamic colors
    def _apply_dynamic_colors(self, from_settings: bool = False):
        """Paints the player bar: with the hue of the cover playing (dynamic colors) or with
        the color scheme. Always from scratch, so it can't get out of step."""
        if self.player is None:
            return
        palette = self.player_palette()
        self.player.set_colors({k: palette[k] for k in self.player.colors if k in palette})
        if from_settings and self.now_playing is not None:
            self.now_playing.apply_theme()   # on a song change it updates by itself

    def player_palette(self) -> dict:
        """Colors of the player right now: the cover's (dynamic colors) or the scheme's.
        "Reproduciendo" uses the same ones, so the bar and the view always match."""
        from audyax.core import colors
        from audyax.gui import theming
        hue = None
        if self.cfg.get("dynamic_colors") and self.player is not None and self.player.item:
            hue = colors.cover_hue(self.player.item.get("image"))
        return theming.palette_for(hue)

    def _on_track_change(self, item):
        self._apply_dynamic_colors()
        self._auto_lyrics(item)
        if self.player_view is not None:
            self.player_view.mark_playing(item["path"] if item.get("source") == "library" else None)
        if self.now_playing is not None:
            self.now_playing.song_changed()
        self._queue_changed()

    def _on_modes_change(self, shuffle: bool, repeat: str):
        self.cfg["shuffle"], self.cfg["repeat"] = shuffle, repeat
        self._queue_changed()

    def _on_player_closed(self):
        self.player.grid_remove()
        if self.player_view is not None:
            self.player_view.mark_playing(None)
        if self.now_playing is not None:
            self.now_playing.song_changed()
        self._queue_changed()

    # ---------------------------------------------------------------- downloader <-> player
    def _on_logo_double_click(self, _event=None):
        """Double click on the sidebar logo: animation, and halfway through (while the window
        is covered) the downloader and the player swap places. Works both ways."""
        if getattr(self, "_switching", False):
            return
        from audyax.gui.player_view import IntroAnimation
        target = DOWNLOADS if self.view_mode == PLAYER else PLAYER
        self._switching = True

        def done():
            self._switching = False
        try:
            IntroAnimation(self, "Reproductor" if target == PLAYER else "Descargas",
                           on_swap=lambda: self._show_view(target), on_done=done)
        except Exception:   # the animation is decoration: if it fails, switch anyway
            self._show_view(target)
            done()

    def _show_view(self, mode: str):
        if mode == self.view_mode:
            return
        self._set_np_fullscreen(False)
        self.view_mode = mode
        if mode == PLAYER:
            if self.player_view is None:
                from audyax.gui.player_view import PlayerView
                self.player_view = PlayerView(self)
            self.main.grid_remove()
            self.player_view.on_show()     # brings the library up to date
            self._show_player_section()
        else:
            for w in (self.player_view, self.now_playing):
                if w is not None:
                    w.grid_remove()
            if self.now_playing is not None:
                self.now_playing.on_hide()
            self.main.grid()
        self._mark_nav()

    def _show_player_section(self):
        """Shows "Reproduciendo" or the library (Canciones, Artistas, Álbumes, Listas, Cola)."""
        place = dict(row=0, column=1, sticky="nsew", padx=(14, 16), pady=(12, 10))
        if self.player_section == "Reproduciendo":
            if self.now_playing is None:
                from audyax.gui.now_playing import NowPlayingView
                self.now_playing = NowPlayingView(self)
            self.player_view.grid_remove()
            self.now_playing.grid(**place)
            self.now_playing.on_show()
        else:
            self._set_np_fullscreen(False)
            if self.now_playing is not None:
                self.now_playing.grid_remove()
                self.now_playing.on_hide()
            self.player_view.set_view(self.player_section)
            self.player_view.grid(**place)

    # ---------------------------------------------------------------- full screen
    def _toggle_np_fullscreen(self):
        self._set_np_fullscreen(not getattr(self, "_np_fullscreen", False))

    def _set_np_fullscreen(self, on: bool):
        """"Reproduciendo" in full screen: the window fills the screen and the sidebar hides
        (the player bar stays, to control the music). Esc goes back."""
        if on == getattr(self, "_np_fullscreen", False):
            return
        self._np_fullscreen = on
        try:
            self.attributes("-fullscreen", on)
        except Exception:
            pass
        if on:
            self.sidebar.grid_remove()
            if not getattr(self, "_esc_bound", False):
                self.bind("<Escape>", lambda _e: self._set_np_fullscreen(False), add="+")
                self._esc_bound = True
        else:
            self.sidebar.grid()
        if self.now_playing is not None:
            self.now_playing.fullscreen_changed(on)

    def _player_section(self, section: str):
        """A section of the player's sidebar."""
        self.player_section = section
        if self.view_mode == PLAYER:
            self._show_player_section()
        self._mark_nav()

    def _open_now_playing(self):
        """Click on the cover or title of the player bar: "Reproduciendo"."""
        self.player_section = "Reproduciendo"
        if self.view_mode == PLAYER:
            self._show_player_section()
            self._mark_nav()
        else:
            self._on_logo_double_click()

    def _go_home(self):
        """"Inicio" in the sidebar: back to the downloader (with the animation if needed)."""
        if self.view_mode == PLAYER:
            self._on_logo_double_click()
