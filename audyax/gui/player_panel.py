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
                "image": f["img_pil"], "source": "list"}

    def _neighbor(self, item, step):
        """The previous/next downloaded song according to the order the list is shown in."""
        ids = [v for v in self._rows_in_view() if self._is_playable(self.rows[v])]
        if not ids:
            return None
        i = ids.index(item["id"]) if item["id"] in ids else (-1 if step > 0 else len(ids))
        j = i + step
        return self._player_item(ids[j]) if 0 <= j < len(ids) else None

    def _play(self, vid):
        self._play_item(self._player_item(vid))

    # ---------------------------------------------------------------- player bar (shared)
    def _ensure_player(self):
        if self.player is not None:
            return
        colors = {k: C[k] for k in ("card", "text", "muted", "accent", "accent_hover",
                                     "on_accent", "hover", "border")}
        fonts = {"bold": self.f_bold, "normal": self.f_normal, "small": self.f_small}
        self.player = PlayerBar(
            self, colors, fonts, neighbor=self._player_neighbor,
            on_volume_change=lambda v: self.cfg.__setitem__("volume", round(v, 2)),
            on_close=self._on_player_closed, on_track_change=self._on_track_change,
            volume=float(self.cfg.get("volume", 0.8)))

    def _play_item(self, item: dict):
        """Plays a song from the download list (source "list") or the library ("library").
        The bar sits below both views, so the music goes on when switching between them."""
        self._ensure_player()
        try:
            self.player.play(item)
        except RuntimeError as e:
            messagebox.showwarning("No se puede reproducir", f"No se pudo reproducir la canción: {e}.")
            return
        self.player.grid(row=1, column=1, sticky="ew", padx=(14, 16), pady=(0, 12))

    def _player_neighbor(self, item, step):
        """Previous/next follows the place the song was started from."""
        if item.get("source") == "library":
            return self.player_view.neighbor(item, step) if self.player_view is not None else None
        return self._neighbor(item, step)

    def _on_track_change(self, item):
        if self.player_view is not None:
            self.player_view.mark_playing(item["path"] if item.get("source") == "library" else None)

    def _on_player_closed(self):
        self.player.grid_remove()
        if self.player_view is not None:
            self.player_view.mark_playing(None)

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
        self.view_mode = mode
        if mode == PLAYER:
            if self.player_view is None:
                from audyax.gui.player_view import PlayerView
                self.player_view = PlayerView(self)
            self.main.grid_remove()
            self.player_view.grid(row=0, column=1, sticky="nsew", padx=(14, 16), pady=(12, 10))
            self.player_view.on_show()
        else:
            if self.player_view is not None:
                self.player_view.grid_remove()
            self.main.grid()
        self._mark_nav()

    def _player_section(self, view: str):
        """Canciones / Artistas / Álbumes in the player's sidebar."""
        if self.player_view is not None:
            self.player_view.set_view(view)
        self._mark_nav()

    def _go_home(self):
        """"Inicio" in the sidebar: back to the downloader (with the animation if needed)."""
        if self.view_mode == PLAYER:
            self._on_logo_double_click()
