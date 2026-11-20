"""AUDYAX: the "Apariencia" window (appearance package). Color scheme of the whole app,
dynamic colors (taken from the cover that's playing) and the background of "Reproduciendo"
(blurred cover, an image of your own or plain). Every change applies right away."""

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from audyax.core import colors

BACKGROUNDS = {"cover": "Portada difuminada", "image": "Imagen propia", "plain": "Liso"}


class AppearanceWindow(ctk.CTkToplevel):
    def __init__(self, app):
        from audyax.gui.style import C
        super().__init__(app)
        self.app, self.C = app, C
        self.title("Apariencia")
        self.resizable(False, False)
        self.configure(fg_color=C["bg"])
        self.transient(app)
        self._build()
        self.after(10, self.lift)
        self.after(150, self.focus)

    def _card(self, title, subtitle=""):
        C = self.C
        card = ctk.CTkFrame(self, fg_color=C["card"], corner_radius=14, border_width=1, border_color=C["border"])
        card.pack(fill="x", padx=16, pady=(14, 0))
        ctk.CTkLabel(card, text=title, font=self.app.f_detail, anchor="w").pack(fill="x", padx=16, pady=(12, 0))
        if subtitle:
            ctk.CTkLabel(card, text=subtitle, font=self.app.f_small, text_color=C["muted"], anchor="w",
                         justify="left", wraplength=500).pack(fill="x", padx=16)
        return card

    def _build(self):
        C, app, cfg = self.C, self.app, self.app.cfg

        # ---- color scheme
        card = self._card("Esquema de color", "Cambia el color de toda la aplicación.")
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(padx=12, pady=(8, 14))
        self.swatches = {}
        for i, name in enumerate(colors.SCHEMES):
            accent = colors.scheme_palette({"accent": ("#7c3aed", "#a78bfa")}, name)["accent"]
            cell = ctk.CTkFrame(grid, fg_color="transparent")
            cell.grid(row=i // 4, column=i % 4, padx=8, pady=6)
            dot = ctk.CTkButton(cell, text="", width=44, height=44, corner_radius=22, fg_color=accent,
                                hover_color=accent, border_width=3, border_color=C["card"],
                                command=lambda n=name: self._set_scheme(n))
            dot.pack()
            ctk.CTkLabel(cell, text=name, font=app.f_small).pack()
            self.swatches[name] = dot
        self._mark_scheme()

        # ---- dynamic colors
        dyn = self._card("Colores dinámicos", "La barra de reproducción y «Reproduciendo» toman el color "
                                              "principal de la portada que suena.")
        self.sw_dynamic = ctk.CTkSwitch(dyn, text="Usar los colores de la portada", font=app.f_normal,
                                        command=self._on_dynamic, progress_color=C["accent"])
        if cfg.get("dynamic_colors"):
            self.sw_dynamic.select()
        self.sw_dynamic.pack(anchor="w", padx=16, pady=(8, 14))

        # ---- background of "Reproduciendo"
        bg = self._card("Fondo de «Reproduciendo»", "La portada muy difuminada detrás de la letra, una "
                                                   "imagen tuya, o el fondo normal de la aplicación.")
        row = ctk.CTkFrame(bg, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(8, 4))
        self.seg_bg = ctk.CTkSegmentedButton(row, values=list(BACKGROUNDS.values()), command=self._on_background,
                                             font=app.f_normal, selected_color=C["accent"],
                                             selected_hover_color=C["accent_hover"])
        self.seg_bg.set(BACKGROUNDS.get(cfg.get("np_background", "cover"), BACKGROUNDS["cover"]))
        self.seg_bg.pack(side="left")
        pick = ctk.CTkFrame(bg, fg_color="transparent")
        pick.pack(fill="x", padx=16, pady=(4, 14))
        self.btn_pick = app._button(pick, "Elegir imagen…", self._choose_image, height=30)
        self.btn_pick.pack(side="left")
        self.l_image = ctk.CTkLabel(pick, text="", font=app.f_small, text_color=C["muted"], anchor="w")
        self.l_image.pack(side="left", padx=10)
        self._show_image_name()

        app._button(self, "Cerrar", self.destroy, accent=True, height=34).pack(anchor="e", padx=16, pady=14)

    # ---------------------------------------------------------------- actions
    def _mark_scheme(self):
        current = self.app.cfg.get("color_scheme", colors.DEFAULT_SCHEME)
        for name, dot in self.swatches.items():
            dot.configure(border_color=self.C["text"] if name == current else self.C["card"])

    def _set_scheme(self, name):
        self.app.set_color_scheme(name)
        self._mark_scheme()

    def _on_dynamic(self):
        self.app.cfg["dynamic_colors"] = bool(self.sw_dynamic.get())
        self.app._apply_dynamic_colors(from_settings=True)

    def _on_background(self, label):
        mode = next(k for k, v in BACKGROUNDS.items() if v == label)
        if mode == "image" and not self.app.cfg.get("background_image"):
            if not self._choose_image():
                self.seg_bg.set(BACKGROUNDS[self.app.cfg.get("np_background", "cover")])
                return
        self.app.cfg["np_background"] = mode
        self._show_image_name()
        self.app._refresh_now_playing_background()

    def _choose_image(self) -> bool:
        path = filedialog.askopenfilename(parent=self, title="Imagen de fondo", filetypes=[
            ("Imágenes", "*.png *.jpg *.jpeg *.webp *.bmp"), ("Todos los archivos", "*.*")])
        if not path:
            return False
        try:
            from PIL import Image
            Image.open(path).verify()
        except Exception:
            from tkinter import messagebox
            messagebox.showwarning("Imagen de fondo", "Ese archivo no es una imagen que se pueda abrir.", parent=self)
            return False
        self.app.cfg["background_image"] = str(Path(path))
        self.app.cfg["np_background"] = "image"
        self.seg_bg.set(BACKGROUNDS["image"])
        self._show_image_name()
        self.app._refresh_now_playing_background()
        return True

    def _show_image_name(self):
        path = self.app.cfg.get("background_image") or ""
        image_mode = self.app.cfg.get("np_background") == "image"
        self.l_image.configure(text=Path(path).name if path else "Ninguna imagen elegida")
        self.btn_pick.configure(state="normal")
        self.l_image.configure(text_color=self.C["text"] if image_mode and path else self.C["muted"])
