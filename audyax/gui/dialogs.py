"""Secondary windows: search results, export and tag editor."""

import tkinter as tk
import webbrowser
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from audyax.core.tags import read_cover, read_tags, rename_file, valid_filename, write_tags
from audyax.gui.style import C, cover_jpeg, icon, shorten, square_image, tk_safe


# ==============================================================================
# SEARCH RESULTS
# ==============================================================================
class SearchResultsWindow(ctk.CTkToplevel):
    """List of results for 'artist song' to choose which ones to download."""

    def __init__(self, app, query: str, results: list):
        super().__init__(app)
        self.app = app
        self.title(f"Resultados: {tk_safe(query)}")
        self.geometry("760x580")
        self.minsize(560, 380)
        self.transient(app)
        self.configure(fg_color=C["bg"])
        self.results = results
        self.marks = {}
        self.thumbnails = {}

        ctk.CTkLabel(self, text=f"  Resultados para “{shorten(query, 50)}”", image=icon("search", 18),
                     compound="left", font=app.f_detail, anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        ctk.CTkLabel(self, text="Marca las canciones que quieres descargar.", font=app.f_normal,
                     text_color=C["muted"], anchor="w").pack(fill="x", padx=16, pady=(0, 8))

        results_frame = ctk.CTkScrollableFrame(self, fg_color=C["card"], corner_radius=14, border_width=1,
                                       border_color=C["border"])
        results_frame.pack(fill="both", expand=True, padx=16)
        for i, r in enumerate(results):
            self._row(results_frame, i, r)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=12)
        self.btn_download_selected = app._button(footer, "", self._on_download, icon("download", 18, C["on_accent"]),
                                    accent=True, height=36)
        self.btn_download_selected.pack(side="right")
        app._button(footer, "Cancelar", self.destroy, height=36).pack(side="right", padx=8)
        app._button(footer, "Abrir en el navegador", self._open, icon("external", 16),
                   height=36).pack(side="left")
        if results:
            self.marks[results[0]["id"]].set(True)  # the first one is usually the right one
        self._count()
        self.after(150, self.focus)

    def _row(self, parent, i, r):
        var = tk.BooleanVar(value=False)
        self.marks[r["id"]] = var
        row = ctk.CTkFrame(parent, fg_color=C["row_a"] if i % 2 == 0 else C["row_b"], corner_radius=8)
        row.pack(fill="x", pady=1)
        ctk.CTkCheckBox(row, text="", variable=var, width=24, checkbox_width=18, checkbox_height=18,
                        corner_radius=5, border_width=2, fg_color=C["accent"],
                        hover_color=C["accent_hover"], border_color=C["muted"],
                        checkmark_color=C["on_accent"], command=self._count).pack(
            side="left", padx=(10, 6), pady=6)
        thumb = ctk.CTkLabel(row, text="", image=self.app.img_empty, width=40, height=40)
        thumb.pack(side="left", padx=6, pady=6)
        self.thumbnails[r["id"]] = thumb
        texts = ctk.CTkFrame(row, fg_color="transparent")
        texts.pack(side="left", fill="x", expand=True, padx=6)
        ctk.CTkLabel(texts, text=shorten(r["title"], 70), font=self.app.f_bold, anchor="w").pack(anchor="w")
        sub = " • ".join(p for p in (r.get("canal"), r.get("duration")) if p)
        ctk.CTkLabel(texts, text=tk_safe(sub) or "—", font=self.app.f_small, text_color=C["muted"],
                     anchor="w").pack(anchor="w")

        def toggle(_e=None, v=var):
            v.set(not v.get())
            self._count()
        for w in (row, thumb, texts, *texts.winfo_children()):
            w.bind("<Button-1>", toggle, add=True)
        if r.get("thumbnail"):
            self.app.pool.submit(self.app._fetch_result_image, r["id"], r["thumbnail"])

    def set_image(self, vid, data):
        lbl = self.thumbnails.get(vid)
        if lbl is None:
            return
        try:
            img = Image.open(BytesIO(data))
            img.load()
            lbl.configure(image=square_image(img, 40, 8))
        except Exception:
            pass

    def _chosen(self):
        return [r for r in self.results if self.marks[r["id"]].get()]

    def _count(self):
        n = len(self._chosen())
        self.btn_download_selected.configure(text=f"Descargar ({n})" if n else "Descargar",
                                 state="normal" if n else "disabled")

    def _open(self):
        for r in self._chosen()[:5] or self.results[:1]:
            webbrowser.open(r["url"])

    def _on_download(self):
        for r in self._chosen():
            self.app._enqueue(r["url"])
        self.app.var_url.set("")
        self.destroy()


# ==============================================================================
# EXPORT
# ==============================================================================
class ExportWindow(ctk.CTkToplevel):
    """Choose what to export: .m3u playlist or .csv table."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Exportar")
        self.geometry("460x330")
        self.resizable(False, False)
        self.transient(app)
        self.configure(fg_color=C["bg"])
        view = app._rows_in_view()
        downloaded = sum(1 for v in view if app.rows[v]["color"] == "green" and app.rows[v]["path"]
                          and Path(app.rows[v]["path"]).exists())
        filtered = len(view) != len(app.rows)

        ctk.CTkLabel(self, text="  Exportar", image=icon("export", 18), compound="left",
                     font=app.f_detail, anchor="w").pack(fill="x", padx=18, pady=(14, 2))
        ctk.CTkLabel(self, text="Se exporta lo que se ve en la lista" + (" (con el filtro actual)." if filtered
                                                                          else "."),
                     font=app.f_small, text_color=C["muted"], anchor="w").pack(fill="x", padx=18, pady=(0, 10))
        self.btn_m3u = self._option(
            "Lista de reproducción (.m3u)",
            f"{downloaded} {'canción descargada' if downloaded == 1 else 'canciones descargadas'} · "
            "para abrir en VLC, foobar2000, Winamp…", app._export_m3u, downloaded > 0)
        self.btn_csv = self._option(
            "Tabla (.csv)", f"{len(view)} {'fila' if len(view) == 1 else 'filas'} · "
            "título, artista, álbum, estado… para Excel", app._export_csv, len(view) > 0)
        app._button(self, "Cerrar", self.destroy, width=100, height=34).pack(anchor="e", padx=18, pady=(4, 12))
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(150, self.focus)

    def _option(self, title, detail, action, active):
        container = self.app._card(self)
        container.pack(fill="x", padx=18, pady=5)

        def choose():
            self.destroy()       # this window closes first; then "Save as" appears
            self.app.after(50, action)
        button = self.app._button(container, title, choose, icon("export", 16, C["on_accent"]),
                                accent=True, height=36, width=300, state="normal" if active else "disabled")
        button.pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(container, text=detail, font=self.app.f_small, text_color=C["muted"], anchor="w",
                     justify="left", wraplength=400).pack(fill="x", padx=14, pady=(0, 10))
        return button


# ==============================================================================
# TAG EDITOR
# ==============================================================================
class TagEditorWindow(ctk.CTkToplevel):
    """Fixes the title, artist, album, year, genre and cover of a song file.
    From the download list (vid = its row) or from the player's library (vid=None and
    on_saved(old_path, new_path, data) to refresh it)."""

    FIELDS = [("title", "Título"), ("artist", "Artista"), ("album", "Álbum"),
              ("year", "Año"), ("genre", "Género")]

    def __init__(self, app, vid, path: str, on_saved=None):
        super().__init__(app)
        self.app, self.vid, self.path = app, vid, path
        self.on_saved = on_saved
        self.title("Editar etiquetas")
        self.geometry("660x470")
        self.resizable(False, False)
        self.transient(app)
        self.configure(fg_color=C["bg"])
        self.new_cover = None
        current_items = read_tags(path)

        ctk.CTkLabel(self, text="  Editar etiquetas", image=icon("edit", 18), compound="left",
                     font=app.f_detail, anchor="w").pack(fill="x", padx=18, pady=(14, 0))
        ctk.CTkLabel(self, text=shorten(Path(path).name, 80), font=app.f_small, text_color=C["muted"],
                     anchor="w").pack(fill="x", padx=18, pady=(0, 10))

        body = self.app._card(self)
        body.pack(fill="both", expand=True, padx=18)
        left = ctk.CTkFrame(body, fg_color="transparent")
        left.pack(side="left", padx=16, pady=16, anchor="n")
        self.l_cover = ctk.CTkLabel(left, text="", image=app.img_empty_cover, width=160, height=160)
        self.l_cover.pack()
        data = read_cover(path)
        if data:
            self._show_cover(data)
        app._button(left, "Cambiar portada…", self._choose_cover, icon("folder", 16),
                   height=32).pack(pady=(10, 0), fill="x")

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=(0, 16), pady=16)
        right.grid_columnconfigure(1, weight=1)
        self.vars = {}
        for i, (key, title) in enumerate(self.FIELDS):
            ctk.CTkLabel(right, text=title, font=app.f_bold, anchor="w").grid(
                row=i, column=0, sticky="w", padx=(0, 12), pady=5)
            var = tk.StringVar(value=tk_safe(current_items.get(key, "")))
            self.vars[key] = var
            ctk.CTkEntry(right, textvariable=var, height=34, corner_radius=10, font=app.f_normal,
                         fg_color=C["input"], border_color=C["border"], text_color=C["text"]).grid(
                row=i, column=1, sticky="ew", pady=5)

        # File name (without extension). If it currently matches the title, it follows the
        # title as it changes, until the user types it by hand.
        row = len(self.FIELDS)
        ctk.CTkLabel(right, text="Archivo", font=app.f_bold, anchor="w").grid(
            row=row, column=0, sticky="w", padx=(0, 12), pady=(14, 5))
        box = ctk.CTkFrame(right, fg_color="transparent")
        box.grid(row=row, column=1, sticky="ew", pady=(14, 5))
        box.grid_columnconfigure(0, weight=1)
        self.extension = Path(path).suffix
        self.var_filename = tk.StringVar(value=tk_safe(Path(path).stem))
        self.entry_file = ctk.CTkEntry(box, textvariable=self.var_filename, height=34, corner_radius=10,
                                      font=app.f_normal, fg_color=C["input"], border_color=C["border"],
                                      text_color=C["text"])
        self.entry_file.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(box, text=self.extension, font=app.f_normal, text_color=C["muted"]).grid(
            row=0, column=1, padx=(6, 0))
        self.l_name = ctk.CTkLabel(right, text="", font=app.f_small, text_color=C["muted"], anchor="w",
                                     justify="left", wraplength=330)
        self.l_name.grid(row=row + 1, column=1, sticky="w")
        self.follows_title = Path(path).stem == valid_filename(current_items.get("title", ""))
        self.entry_file._entry.bind("<Key>", lambda e: setattr(self, "follows_title", False), add=True)
        self.vars["title"].trace_add("write", self._on_title_change)
        self.var_filename.trace_add("write", lambda *a: self._show_final_name())
        self._show_final_name()

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=12)
        app._button(footer, "Guardar", self._save, accent=True, width=120, height=36).pack(side="right")
        app._button(footer, "Cancelar", self.destroy, width=100, height=36).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(150, self.focus)

    def _on_title_change(self, *_):
        if self.follows_title:
            self.var_filename.set(valid_filename(self.vars["title"].get()))

    def _show_final_name(self):
        """Warns if the name will be saved differently (forbidden characters) or is empty."""
        typed = self.var_filename.get().strip()
        final = valid_filename(typed, self.extension)
        if not final:
            text = "El nombre no puede quedar vacío."
        elif final != typed:
            text = f"Se guardará como: {shorten(final + self.extension, 60)}"
        else:
            text = ""
        self.l_name.configure(text=text, text_color=C["red"] if not final else C["muted"])

    def _show_cover(self, data: bytes):
        try:
            img = Image.open(BytesIO(data))
            img.load()
            self.l_cover.configure(image=square_image(img, 160, 14))
        except Exception:
            pass

    def _choose_cover(self):
        file = filedialog.askopenfilename(
            parent=self, title="Elige la imagen de portada",
            filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp *.bmp"), ("Todos", "*.*")])
        if not file:
            return
        try:
            with Image.open(file) as img:
                self.new_cover = cover_jpeg(img, square=bool(self.app.var_crop.get()))
        except Exception as e:
            messagebox.showerror("Imagen no válida", str(e), parent=self)
            return
        self._show_cover(self.new_cover)

    def _save(self):
        data = {k: v.get().strip() for k, v in self.vars.items()}
        name = valid_filename(self.var_filename.get(), self.extension)
        if not name:
            messagebox.showerror("Nombre no válido", "El nombre del archivo no puede quedar vacío.", parent=self)
            return
        try:
            write_tags(self.path, data, self.new_cover)
        except Exception as e:
            messagebox.showerror("No se pudieron guardar las etiquetas", str(e), parent=self)
            return
        # Tags first, then the name: if renaming fails, the rest is already saved
        try:
            new_path = rename_file(self.path, name)
        except (OSError, ValueError) as e:
            self._saved(data, self.path)
            self.new_cover = None
            detail = str(e) if isinstance(e, (FileExistsError, ValueError)) else (
                f"{e}\n\n¿Está abierto en un reproductor? Ciérralo e inténtalo de nuevo.")
            messagebox.showerror("No se pudo cambiar el nombre",
                                 f"Las etiquetas sí se guardaron, pero el archivo no se renombró.\n\n{detail}",
                                 parent=self)
            return
        self._saved(data, str(new_path))
        self.destroy()

    def _saved(self, data, new_path):
        if self.on_saved is not None:
            self.on_saved(self.path, new_path, data)
        elif new_path != self.path:
            self.app._on_tags_saved(self.vid, data, self.new_cover, new_path)
        else:
            self.app._on_tags_saved(self.vid, data, self.new_cover)
