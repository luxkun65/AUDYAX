"""AUDYAX player: the "Sonido" window (part 2, sound package). Equalizer with presets,
transition between songs (no gap / crossfade) and automatic lyrics. Every change applies
right away and is remembered in the settings."""

import customtkinter as ctk

from audyax.player.engine import EQ_BANDS, EQ_LIMIT, EQ_PRESETS

CUSTOM = "Personalizado"


def band_label(freq: int) -> str:
    return f"{freq // 1000}k" if freq >= 1000 else str(freq)


def preset_of(gains) -> str:
    """The name of the preset these gains match, or "Personalizado"."""
    gains = [round(float(g), 1) for g in gains]
    return next((name for name, values in EQ_PRESETS.items() if list(values) == gains), CUSTOM)


class SoundWindow(ctk.CTkToplevel):
    def __init__(self, app):
        from audyax.gui.style import C
        super().__init__(app)
        self.app, self.C = app, C
        self.title("Sonido")
        self.resizable(False, False)
        self.configure(fg_color=C["bg"])
        self.transient(app)
        cfg = app.cfg
        self._gains = [float(g) for g in (cfg.get("eq_gains") or [0] * len(EQ_BANDS))][:len(EQ_BANDS)]
        self._gains += [0.0] * (len(EQ_BANDS) - len(self._gains))
        self._apply_job = None
        self._build()
        self.after(10, self.lift)
        self.after(150, self.focus)

    # ---------------------------------------------------------------- building
    def _card(self, title):
        C = self.C
        card = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=14, border_width=1, border_color=C["border"])
        card.pack(fill="x", padx=16, pady=(14, 0))
        ctk.CTkLabel(card, text=title, font=self.app.f_detail, anchor="w").pack(fill="x", padx=16, pady=(12, 4))
        return card

    def _build(self):
        C, app, cfg = self.C, self.app, self.app.cfg

        # ---- equalizer
        eq = self._card("Ecualizador")
        top = ctk.CTkFrame(eq, fg_color="transparent")
        top.pack(fill="x", padx=16)
        self.sw_eq = ctk.CTkSwitch(top, text="Activado", font=app.f_normal, command=self._on_toggle,
                                   progress_color=C["accent"])
        self.sw_eq.pack(side="left")
        if cfg.get("eq_enabled"):
            self.sw_eq.select()
        self.preset = ctk.CTkOptionMenu(top, values=list(EQ_PRESETS) + [CUSTOM], command=self._on_preset,
                                        font=app.f_normal, fg_color=C["input"], button_color=C["accent"],
                                        button_hover_color=C["accent_hover"], text_color=C["text"],
                                        dropdown_font=app.f_normal, width=170)
        self.preset.set(preset_of(self._gains))
        self.preset.pack(side="right")
        app._button(top, "Restablecer", lambda: self._on_preset("Plano"), height=30).pack(side="right", padx=8)

        bands = ctk.CTkFrame(eq, fg_color="transparent")
        bands.pack(padx=12, pady=(10, 14))
        self.sliders, self.values = [], []
        for i, freq in enumerate(EQ_BANDS):
            col = ctk.CTkFrame(bands, fg_color="transparent")
            col.grid(row=0, column=i, padx=5)
            value = ctk.CTkLabel(col, text="", font=app.f_small, text_color=C["muted"], width=38)
            value.pack()
            slider = ctk.CTkSlider(col, from_=-EQ_LIMIT, to=EQ_LIMIT, number_of_steps=int(EQ_LIMIT * 4),
                                   orientation="vertical", height=170, width=18,
                                   progress_color=C["accent"], button_color=C["accent"],
                                   button_hover_color=C["accent_hover"],
                                   command=lambda v, k=i: self._on_band(k, v))
            slider.set(self._gains[i])
            slider.pack(pady=4)
            ctk.CTkLabel(col, text=band_label(freq), font=app.f_small).pack()
            self.sliders.append(slider)
            self.values.append(value)
        self._show_values()

        # ---- transitions
        tr = self._card("Entre canciones")
        row = ctk.CTkFrame(tr, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 4))
        self.seg = ctk.CTkSegmentedButton(row, values=["Sin pausa", "Fundido"], command=self._on_transition,
                                          font=app.f_normal, selected_color=C["accent"],
                                          selected_hover_color=C["accent_hover"])
        crossfade = float(cfg.get("crossfade", 0) or 0)
        self.seg.set("Fundido" if crossfade > 0 else "Sin pausa")
        self.seg.pack(side="left")
        self.s_fade = ctk.CTkSlider(row, from_=1, to=12, number_of_steps=11, width=180,
                                    progress_color=C["accent"], button_color=C["accent"],
                                    button_hover_color=C["accent_hover"], command=self._on_fade)
        self.s_fade.set(crossfade or 4)
        self.l_fade = ctk.CTkLabel(row, text="", font=app.f_normal, width=50)
        ctk.CTkLabel(tr, text="«Sin pausa»: la siguiente canción empieza justo cuando termina la anterior. "
                              "«Fundido»: el final de una se mezcla con el comienzo de la otra.",
                     font=app.f_small, text_color=C["muted"], wraplength=520, justify="left",
                     anchor="w").pack(fill="x", padx=16, pady=(4, 14))
        self._show_fade()

        # ---- lyrics
        ly = self._card("Letras")
        self.sw_lyrics = ctk.CTkSwitch(ly, text="Buscar la letra automáticamente si la canción no la tiene",
                                       font=app.f_normal, command=self._on_lyrics, progress_color=C["accent"])
        if cfg.get("auto_lyrics", True):
            self.sw_lyrics.select()
        self.sw_lyrics.pack(anchor="w", padx=16)
        ctk.CTkLabel(ly, text="Se buscan en LRCLIB al reproducir y se guardan como dice Configuración → Letras "
                              "(como archivo .lrc si allí están desactivadas).",
                     font=app.f_small, text_color=C["muted"], wraplength=520, justify="left",
                     anchor="w").pack(fill="x", padx=16, pady=(4, 14))

        app._button(self, "Cerrar", self.destroy, accent=True, height=34).pack(anchor="e", padx=16, pady=14)

    # ---------------------------------------------------------------- equalizer
    def _show_values(self):
        on = bool(self.sw_eq.get())
        for value, g in zip(self.values, self._gains):
            value.configure(text=f"{g:+.0f}" if abs(g) >= 0.5 else "0",
                            text_color=self.C["text"] if on else self.C["muted"])

    def _on_band(self, i, v):
        self._gains[i] = round(float(v) * 2) / 2
        if not self.sw_eq.get():
            self.sw_eq.select()          # moving a band turns the equalizer on
        self.preset.set(preset_of(self._gains))
        self._show_values()
        self._apply_later()

    def _on_preset(self, name):
        if name == CUSTOM:
            return
        self._gains = [float(g) for g in EQ_PRESETS[name]]
        for slider, g in zip(self.sliders, self._gains):
            slider.set(g)
        self.preset.set(name)
        if name != "Plano" and not self.sw_eq.get():
            self.sw_eq.select()
        self._show_values()
        self._apply_later(0)

    def _on_toggle(self):
        self._show_values()
        self._apply_later(0)

    def _apply_later(self, ms=350):
        """Changing the sound restarts the decoder: wait until the slider stops moving."""
        if self._apply_job:
            self.after_cancel(self._apply_job)
        self._apply_job = self.after(ms, self._apply)

    def _apply(self):
        self._apply_job = None
        cfg = self.app.cfg
        cfg["eq_enabled"] = bool(self.sw_eq.get())
        cfg["eq_gains"] = list(self._gains)
        self.app._apply_sound_settings()

    # ---------------------------------------------------------------- transitions / lyrics
    def _show_fade(self):
        fading = self.seg.get() == "Fundido"
        if fading:
            self.s_fade.pack(side="left", padx=(16, 6))
            self.l_fade.pack(side="left")
            self.l_fade.configure(text=f"{int(self.s_fade.get())} s")
        else:
            self.s_fade.pack_forget()
            self.l_fade.pack_forget()

    def _on_transition(self, _value):
        self._show_fade()
        self._on_fade(self.s_fade.get())

    def _on_fade(self, value):
        self.l_fade.configure(text=f"{int(value)} s")
        self.app.cfg["crossfade"] = int(value) if self.seg.get() == "Fundido" else 0
        self.app._apply_sound_settings()

    def _on_lyrics(self):
        self.app.cfg["auto_lyrics"] = bool(self.sw_lyrics.get())
