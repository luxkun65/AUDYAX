"""Small reusable widgets."""

import customtkinter as ctk

from audyax.gui.style import C, icon, tk_safe


# ==============================================================================
# NOTICE (strip inside the controls card: copied link, update…)
# ==============================================================================
class Notice:
    def __init__(self, app, parent, row: int, ico: str):
        self.app, self.row = app, row
        self.frame = ctk.CTkFrame(parent, fg_color=C["card2"], corner_radius=10)
        # Buttons first: that way the text never takes their space
        ctk.CTkButton(self.frame, text="✕", width=28, height=28, fg_color="transparent",
                      hover_color=C["hover"], text_color=C["muted"],
                      command=self.hide).pack(side="right", padx=(0, 6))
        self.buttons = ctk.CTkFrame(self.frame, fg_color="transparent")
        self.buttons.pack(side="right")
        self.lbl = ctk.CTkLabel(self.frame, text="", image=icon(ico, 16), compound="left",
                                font=app.f_normal, anchor="w")
        self.lbl.pack(side="left", fill="x", expand=True, padx=(10, 8), pady=6)
        self.visible = False

    def show(self, text: str, actions=()):
        """actions: list of (text, function, is_primary)."""
        for b in self.buttons.winfo_children():
            b.destroy()
        self.lbl.configure(text="  " + tk_safe(text))
        for btn_text, func, primary in actions:
            def action(f=func):
                self.hide()
                f()
            self.app._button(self.buttons, btn_text, action, accent=primary, height=28).pack(
                side="left", padx=(0, 6))
        self.frame.grid(row=self.row, column=0, columnspan=4, sticky="ew", padx=14, pady=(0, 10))
        self.visible = True

    def hide(self):
        self.frame.grid_remove()
        self.visible = False
