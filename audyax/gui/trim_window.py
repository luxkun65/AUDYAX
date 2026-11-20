"""AUDYAX player: the "Recortar" window (library and tools package). Chooses the part of a
song to keep (start and end), optional fade in / fade out, listens to it, and saves it as a
copy next to the original or replacing it. The cutting itself is in core/trim.py."""

import threading
from pathlib import Path

import customtkinter as ctk

from audyax.core import trim
from audyax.player.engine import audio_duration


def fmt(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    return f"{int(seconds // 60)}:{seconds % 60:05.2f}"


def parse(text: str):
    """"1:23.5" / "83.5" -> 83.5 seconds; None if it isn't a time."""
    text = text.strip().replace(",", ".")
    try:
        if ":" in text:
            minutes, seconds = text.split(":", 1)
            return int(minutes) * 60 + float(seconds)
        return float(text)
    except ValueError:
        return None


class TrimWindow(ctk.CTkToplevel):
    def __init__(self, app, path: str, title: str = "", on_done=None):
        from audyax.gui.style import C
        super().__init__(app)
        self.app, self.C, self.path, self.on_done = app, C, str(path), on_done
        self.duration = audio_duration(path) or 0.0
        self.title("Recortar")
        self.resizable(False, False)
        self.configure(fg_color=C["bg"])
        self.transient(app)
        self._preview_job = None
        self._busy = False
        self._build(title or Path(path).stem)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(10, self.lift)

    def _build(self, title):
        C, app = self.C, self.app
        ctk.CTkLabel(self, text="Recortar", font=app.f_detail, anchor="w").pack(fill="x", padx=18, pady=(14, 0))
        ctk.CTkLabel(self, text=title, font=app.f_small, text_color=C["muted"], anchor="w").pack(fill="x", padx=18)
        card = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=14, border_width=1, border_color=C["border"])
        card.pack(fill="x", padx=18, pady=12)
        self.vars, self.sliders = {}, {}
        for row, (key, label, value) in enumerate((("start", "Inicio", 0.0), ("end", "Final", self.duration))):
            ctk.CTkLabel(card, text=label, font=app.f_normal, width=60, anchor="w").grid(
                row=row, column=0, padx=(16, 6), pady=(14 if row == 0 else 6, 6), sticky="w")
            slider = ctk.CTkSlider(card, from_=0, to=max(0.1, self.duration), width=360,
                                   progress_color=C["accent"], button_color=C["accent"],
                                   button_hover_color=C["accent_hover"],
                                   command=lambda v, k=key: self._on_slider(k, v))
            slider.set(value)
            slider.grid(row=row, column=1, padx=6, pady=(14 if row == 0 else 6, 6))
            var = ctk.StringVar(value=fmt(value))
            entry = ctk.CTkEntry(card, textvariable=var, width=80, font=app.f_normal, justify="center",
                                 fg_color=C["input"], border_color=C["border"])
            entry.grid(row=row, column=2, padx=(6, 16), pady=(14 if row == 0 else 6, 6))
            entry.bind("<Return>", lambda _e, k=key: self._on_entry(k))
            entry.bind("<FocusOut>", lambda _e, k=key: self._on_entry(k))
            self.vars[key], self.sliders[key] = var, slider
        self.l_length = ctk.CTkLabel(card, text="", font=app.f_small, text_color=C["muted"])
        self.l_length.grid(row=2, column=1, sticky="w", padx=6)

        fades = ctk.CTkFrame(card, fg_color="transparent")
        fades.grid(row=3, column=0, columnspan=3, sticky="w", padx=16, pady=(8, 14))
        self.fade = {}
        for key, label in (("in", "Aparecer gradualmente"), ("out", "Desvanecer al final")):
            ctk.CTkLabel(fades, text=label, font=app.f_small).pack(side="left", padx=(0, 6))
            menu = ctk.CTkOptionMenu(fades, values=["No", "1 s", "2 s", "3 s", "5 s"], width=70,
                                     font=app.f_small, fg_color=C["input"], button_color=C["accent"],
                                     button_hover_color=C["accent_hover"], text_color=C["text"])
            menu.set("No")
            menu.pack(side="left", padx=(0, 18))
            self.fade[key] = menu

        self.replace = ctk.CTkSegmentedButton(self, values=["Guardar como copia", "Reemplazar el original"],
                                              font=app.f_normal, selected_color=C["accent"],
                                              selected_hover_color=C["accent_hover"])
        self.replace.set("Guardar como copia")
        self.replace.pack(anchor="w", padx=18)
        self.l_status = ctk.CTkLabel(self, text="", font=app.f_small, text_color=C["muted"], anchor="w")
        self.l_status.pack(fill="x", padx=18, pady=(6, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=18, pady=14)
        app._button(buttons, "Escuchar el recorte", self._preview, height=34).pack(side="left")
        self.btn_cut = app._button(buttons, "Recortar", self._cut, accent=True, height=34)
        self.btn_cut.pack(side="right")
        app._button(buttons, "Cancelar", self.close, height=34).pack(side="right", padx=8)
        self._show_length()

    # ---------------------------------------------------------------- choosing
    def _values(self):
        return float(self.sliders["start"].get()), float(self.sliders["end"].get())

    def _on_slider(self, key, value):
        start, end = self._values()
        if start > end - 0.5:                          # the start never passes the end
            if key == "start":
                start = max(0.0, end - 0.5)
                self.sliders["start"].set(start)
            else:
                end = min(self.duration, start + 0.5)
                self.sliders["end"].set(end)
        self.vars["start"].set(fmt(start))
        self.vars["end"].set(fmt(end))
        self._show_length()

    def _on_entry(self, key):
        value = parse(self.vars[key].get())
        if value is None:
            self.vars[key].set(fmt(self.sliders[key].get()))
            return
        self.sliders[key].set(max(0.0, min(self.duration, value)))
        self._on_slider(key, value)

    def _fade(self, key) -> float:
        value = self.fade[key].get()
        return 0.0 if value == "No" else float(value.split()[0])

    def _show_length(self):
        start, end = self._values()
        self.l_length.configure(text=f"Duración del recorte: {fmt(end - start)}  (de {fmt(self.duration)})")

    # ---------------------------------------------------------------- listening
    def _preview(self):
        """Plays the chosen part in the player bar and pauses at its end."""
        start, end = self._values()
        app = self.app
        item = {"id": self.path, "path": self.path, "title": Path(self.path).stem, "artist": "",
                "source": "library"}
        app._play_queue([item])
        if app.player is None or not app.player.item:
            return
        app.player.engine.seek(start)
        if self._preview_job:
            self.after_cancel(self._preview_job)

        def watch():
            bar = app.player
            if bar is None or not bar.item or bar.item["path"] != self.path:
                return
            if bar.engine.position >= end:
                bar.engine.pause_playback()
                bar.btn_play.configure(image=bar.ico_play)
                return
            self._preview_job = self.after(100, watch)
        self._preview_job = self.after(300, watch)

    # ---------------------------------------------------------------- cutting
    def _cut(self):
        if self._busy:
            return
        start, end = self._values()
        if end - start < 0.5:
            self.l_status.configure(text="El recorte tiene que durar al menos medio segundo.")
            return
        replace = self.replace.get() == "Reemplazar el original"
        bar = self.app.player
        if replace and bar is not None and bar.item and bar.item["path"] == self.path:
            bar.close()     # the file can't be replaced while it's being played (Windows)
        self._busy = True
        self.btn_cut.configure(state="disabled", text="Recortando…")
        self.l_status.configure(text="")
        fade_in, fade_out = self._fade("in"), self._fade("out")

        def work():
            try:
                out = trim.trim(self.path, start, end, fade_in, fade_out, replace=replace)
                self.after(0, lambda: self._done(out, None))
            except Exception as e:
                error = str(e)
                self.after(0, lambda: self._done(None, error))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, out, error):
        self._busy = False
        self.btn_cut.configure(state="normal", text="Recortar")
        if error:
            self.l_status.configure(text=f"No se pudo recortar: {error}", text_color=self.C["red"])
            return
        if self.on_done:
            self.on_done(str(out))
        self.close()

    def close(self):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self.destroy()
