"""Detail panel of the selected song and its actions (link, folder, tags)."""

import threading
import webbrowser
from pathlib import Path
from tkinter import messagebox

from audyax.core.tags import file_info, is_editable
from audyax.gui.dialogs import TagEditorWindow
from audyax.gui.style import C, open_path, shorten, tk_safe


class DetailMixin:
    """Detail panel. Mixed into App."""

    def _toggle_detail(self):
        if self.detail_body.winfo_manager():
            self.detail_body.pack_forget()
            self.btn_collapse.configure(text="▸")
        else:
            self.detail_body.pack(fill="x", padx=12, pady=(2, 0), after=self.detail_header)
            self.btn_collapse.configure(text="▾")

    # ---------------------------------------------------------------- detail
    def _show_detail(self):
        vid = self.sel if self.sel in self.rows else None
        if not vid:
            self.l_detail_title.configure(text="Selecciona una canción de la lista.")
            self.l_detail_artist.configure(text="")
            self.l_chip.pack_forget()
            self.l_detail_info.configure(text="")
            self.l_cover.configure(image=self.img_empty_big)
            for lbl in self.l_info.values():
                lbl.configure(text="—")
            return
        f = self.rows[vid]
        self.l_detail_title.configure(text=shorten(f["title"], 30))
        self.l_detail_artist.configure(text=shorten(f["artist"] or "—", 40))
        chip = " • ".join(p for p in (f["year"], f["genre"]) if p)
        if chip:
            self.l_chip.configure(text=f"  {tk_safe(chip)}  ")
            self.l_chip.pack(anchor="w", pady=4, before=self.l_detail_info)
        else:
            self.l_chip.pack_forget()
        path = f["path"]
        if f["color"] == "red":
            self.l_detail_info.configure(text=shorten(f["reason"] or f["text"], 60), text_color=C["red"])
        elif f["color"] == "green" and path:
            extra = [{"synced": "letra sincronizada", "text": "lyrics"}.get(f["lyrics"], "")]
            extra.append("álbum de MusicBrainz" if f["musicbrainz"] else "")
            text = "  ·  ".join([shorten(Path(path).name, 50)] + [e for e in extra if e])
            self.l_detail_info.configure(text=text, text_color=C["muted"])
        else:
            self.l_detail_info.configure(text=shorten(f["text"], 60), text_color=C["muted"])
        self.l_cover.configure(image=f["img_b"] or self.img_empty_big)

        d = f["data"] or {}
        rg = f["replaygain"].split(" · ")[0].replace("pista ", "") if f["replaygain"] else ""
        values = {k: d.get(k) or "—" for k in ("audio_format", "duration", "bitrate", "size", "sample_rate")}
        values["rg"] = rg or "—"
        for key, text in values.items():
            self.l_info[key].configure(text=text)
        if (f["data"] is None and f["color"] == "green" and path and not f["data_requested"]
                and Path(path).exists()):
            f["data_requested"] = True
            threading.Thread(target=self._read_file_info, args=(vid, path), daemon=True).start()

    def _read_file_info(self, vid, path):
        try:
            self.events.put(("data", vid, file_info(path)))
        except Exception:
            pass

    def _link_of(self, vid=None):
        vid = vid or self.sel
        return self.rows[vid]["url"] if vid in self.rows else ""

    def _copy_link(self):
        url = self._link_of()
        if url:
            self.clipboard_clear()
            self.clipboard_append(url)

    def _open_link(self, vid=None):
        url = self._link_of(vid)
        if url:
            webbrowser.open(url)

    def _open_song_folder(self):
        path = self.rows[self.sel]["path"] if self.sel in self.rows else ""
        if path and Path(path).exists():
            open_path(Path(path).parent)
        else:
            self._open_base_folder()

    def _open_base_folder(self):
        folder = Path(self.var_folder.get())
        if folder.exists():
            open_path(folder)
        else:
            messagebox.showinfo("Carpeta", "Esa carpeta todavía no existe.")

    def _edit_tags(self):
        f = self.rows.get(self.sel)
        path = f["path"] if f else ""
        if not f or f["color"] != "green" or not path or not Path(path).exists():
            messagebox.showinfo("Editar etiquetas", "Selecciona una canción ya descargada en esta sesión.")
            return
        if not is_editable(path):
            messagebox.showinfo("Editar etiquetas",
                                "Solo se pueden editar archivos MP3, M4A y Opus (hace falta mutagen).")
            return
        try:
            TagEditorWindow(self, self.sel, path)
        except Exception as e:
            messagebox.showerror("No se pudo leer el archivo", str(e))

    def _on_tags_saved(self, vid, data, cover, path=None):
        f = self.rows.get(vid)
        if not f:
            return
        f.update(data=None, data_requested=False)  # the file size changes
        self._row(vid, **data, **({"path": path} if path else {}))
        if cover:
            self._set_image(vid, cover)
