"""Connects the song list with the mini player (audyax.player)."""

from pathlib import Path
from tkinter import messagebox

from audyax.gui.style import C
from audyax.player.bar import PlayerBar


class PlayerMixin:
    """Mini player integration. Mixed into App."""

    # ---------------------------------------------------------------- mini player
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
                "image": f["img_pil"]}

    def _neighbor(self, item, step):
        """The previous/next downloaded song according to the order the list is shown in."""
        ids = [v for v in self._rows_in_view() if self._is_playable(self.rows[v])]
        if not ids:
            return None
        i = ids.index(item["id"]) if item["id"] in ids else (-1 if step > 0 else len(ids))
        j = i + step
        return self._player_item(ids[j]) if 0 <= j < len(ids) else None

    def _play(self, vid):
        if self.player is None:
            colors = {k: C[k] for k in ("card", "text", "muted", "accent", "accent_hover",
                                         "on_accent", "hover", "border")}
            fonts = {"bold": self.f_bold, "normal": self.f_normal, "small": self.f_small}
            self.player = PlayerBar(
                self.main, colors, fonts, neighbor=self._neighbor,
                on_volume_change=lambda v: self.cfg.__setitem__("volume", round(v, 2)),
                on_close=lambda: self.player.grid_remove(),
                volume=float(self.cfg.get("volume", 0.8)))
        try:
            self.player.play(self._player_item(vid))
        except RuntimeError as e:
            messagebox.showwarning("No se puede reproducir", f"No se pudo reproducir la canción: {e}.")
            return
        self.player.grid(row=5, column=0, sticky="ew", pady=(10, 0))
