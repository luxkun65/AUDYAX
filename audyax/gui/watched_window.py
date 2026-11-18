"""Watched playlists window and its logic."""

import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from audyax.core.config import save_config
from audyax.downloader import watched
from audyax.downloader.network import network_options
from audyax.gui.style import C, icon, shorten, tk_safe


# ==============================================================================
# MAIN WINDOW
# ==============================================================================
# ==============================================================================
# WATCHED PLAYLISTS
# ==============================================================================
class WatchedWindow(ctk.CTkToplevel):
    """Saved playlists: AUDYAX checks whether they have new songs and downloads only those."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Playlists vigiladas")
        self.geometry("640x520")
        self.minsize(560, 380)
        self.transient(app)
        self.configure(fg_color=C["bg"])

        ctk.CTkLabel(self, text="Playlists vigiladas", font=app.f_detail, anchor="w").pack(
            anchor="w", padx=16, pady=(14, 0))
        ctk.CTkLabel(self, text="AUDYAX revisa si tienen canciones nuevas y baja solo esas, a la "
                                "carpeta donde guardas tu música.", font=app.f_small,
                     text_color=C["muted"], anchor="w", wraplength=600, justify="left").pack(
            anchor="w", padx=16, pady=(0, 8))

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 8))
        self.var_url = tk.StringVar(value=self._suggested())
        e = ctk.CTkEntry(row, textvariable=self.var_url, height=34, corner_radius=10, font=app.f_normal,
                         placeholder_text="Enlace de una playlist o un álbum…", fg_color=C["input"],
                         border_color=C["border"], text_color=C["text"])
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda _e: self._add())
        app._button(row, "Vigilar", self._add, icon("eye", 16, C["on_accent"]),
                   accent=True, width=110, height=34).pack(side="left", padx=(8, 0))

        self.items_frame = ctk.CTkScrollableFrame(self, fg_color=C["card"], corner_radius=14, border_width=1,
                                            border_color=C["border"])
        self.items_frame.pack(fill="both", expand=True, padx=16)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=12)
        self.btn_check_all = app._button(footer, "Revisar todas", app._check_all_watched, icon("refresh", 16),
                                    height=34)
        self.btn_check_all.pack(side="left")
        app._button(footer, "Cerrar", self.destroy, height=34, width=90).pack(side="right")
        self.refresh()
        self.after(150, self.focus)

    def _suggested(self) -> str:
        """The copied link or the last downloaded playlist, if it's a playlist not watched yet."""
        candidates = [getattr(self.app, "last_playlist", "")]
        try:
            candidates.insert(0, self.app.clipboard_get().strip())
        except tk.TclError:
            pass
        for url in candidates:
            if url and watched.is_playlist(url):
                canonical = watched.playlist_url(url)
                if not any(v["url"] == canonical for v in self.app.watched):
                    return canonical
        return ""

    def _add(self):
        url = self.var_url.get().strip()
        if url and self.app._watch(url):
            self.var_url.set("")

    def refresh(self):
        app = self.app
        for w in self.items_frame.winfo_children():
            w.destroy()
        self.btn_check_all.configure(state="normal" if app.watched else "disabled")
        if not app.watched:
            ctk.CTkLabel(self.items_frame, text="Todavía no vigilas ninguna playlist.\nPega arriba el enlace de "
                                          "una y dale «Vigilar».", font=app.f_normal, text_color=C["muted"],
                         justify="center").pack(pady=40)
            return
        for i, v in enumerate(app.watched):
            if i:
                ctk.CTkFrame(self.items_frame, height=1, fg_color=C["border"]).pack(fill="x", padx=8)
            self._row(v)

    def _row(self, v):
        app = self.app
        container = ctk.CTkFrame(self.items_frame, fg_color="transparent")
        container.pack(fill="x", padx=4, pady=6)
        buttons = ctk.CTkFrame(container, fg_color="transparent")
        buttons.pack(side="right")  # first: that way the text never takes their space
        text = ctk.CTkFrame(container, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True, padx=(6, 8))
        ctk.CTkLabel(text, text=shorten(tk_safe(v.get("name") or v["url"]), 40), font=app.f_bold,
                     anchor="w").pack(anchor="w")
        checking = v["url"] in app.checking
        status = "Revisando…" if checking else watched.check_summary(v)
        ctk.CTkLabel(text, text=shorten(tk_safe(status), 110), font=app.f_small, anchor="w",
                     wraplength=300, justify="left", text_color=C["red"] if v.get("error") and not checking else C["muted"]).pack(anchor="w")
        ctk.CTkLabel(text, text=shorten(v["folder"], 60), font=app.f_small, text_color=C["muted"],
                     anchor="w", image=icon("folder", 12, C["muted"]), compound="left").pack(anchor="w")

        n = len(app.watched_new.get(v["url"]) or [])
        b = app._button(buttons, f"Bajar {n} nueva{'s' if n != 1 else ''}" if n else "Bajar nuevas",
                       lambda u=v["url"]: app._download_new(u), accent=bool(n), height=30)
        b.configure(state="normal" if n and not checking else "disabled")
        b.pack(side="left", padx=(0, 6))
        r = app._button(buttons, "", lambda x=v: app._check_watched(x), icon("refresh", 16),
                       width=34, height=30)
        r.configure(state="disabled" if checking else "normal")
        r.pack(side="left", padx=(0, 6))
        app._button(buttons, "", lambda u=v["url"]: self._remove(u), icon("trash", 16),
                   width=34, height=30).pack(side="left")

    def _remove(self, url):
        v = next((x for x in self.app.watched if x["url"] == url), None)
        name = (v or {}).get("name") or url
        if messagebox.askyesno("Dejar de vigilar", f"¿Dejar de vigilar «{name}»?\n\n"
                                                   "Las canciones que ya bajaste no se borran.", parent=self):
            self.app._unwatch(url)


class WatchedMixin:
    """Watched playlists. Mixed into App."""

    # ---------------------------------------------------------------- watched playlists
    def _open_watched(self):
        v = self.watched_window
        if v is not None and v.winfo_exists():
            v.focus()
            return
        self.watched_window = WatchedWindow(self)

    def _refresh_watched(self):
        v = self.watched_window
        if v is not None and v.winfo_exists():
            v.refresh()

    def _watch(self, url: str, name: str = "") -> bool:
        folder = self.var_folder.get().strip()
        if not folder:
            messagebox.showwarning("Falta la carpeta", "Elige primero dónde guardar las canciones.")
            return False
        try:
            v = watched.add(self.watched, url, folder, name)
        except ValueError:
            messagebox.showwarning("No es una playlist", "Pega el enlace de una playlist o un álbum de "
                                                         "YouTube o YouTube Music.")
            return False
        save_config(self._current_cfg())
        self._refresh_watched()
        self._check_watched(v)
        return True

    def _unwatch(self, url: str):
        watched.remove(self.watched, url)
        self.watched_new.pop(url, None)
        save_config(self._current_cfg())
        self._refresh_watched()

    def _check_watched(self, v, on_open=False):
        if v["url"] in self.checking:
            return
        self.checking.add(v["url"])
        snapshot, net = dict(v), network_options(self._current_cfg())

        def job():
            try:
                new_songs = watched.check(snapshot, net)
            except Exception as e:  # shouldn't happen: check already catches errors
                snapshot["error"], new_songs = str(e), []
            self.events.put(("watched_checked", v["url"], snapshot, new_songs, on_open))
        threading.Thread(target=job, daemon=True).start()
        self._refresh_watched()

    def _check_all_watched(self, on_open=False):
        for v in list(self.watched):
            self._check_watched(v, on_open)

    def _on_watched_checked(self, url, snapshot, new_songs, on_open):
        self.checking.discard(url)
        for v in self.watched:
            if v["url"] == url:
                v.update(snapshot)
                break
        else:
            return  # it was removed while being checked
        self.watched_new[url] = new_songs
        save_config(self._current_cfg())
        self._refresh_watched()
        if on_open and not self.checking:  # all checks finished: a single notice
            with_new = [v for v in self.watched if self.watched_new.get(v["url"])]
            if not with_new:
                return
            if self.cfg.get("auto_download_new"):
                for v in with_new:
                    self._download_new(v["url"])
                return
            total = sum(len(self.watched_new[v["url"]]) for v in with_new)
            where = (f"«{with_new[0].get('name') or 'tu playlist'}»" if len(with_new) == 1
                     else f"{len(with_new)} playlists vigiladas")
            self.watched_notice.show(
                f"Hay {total} canción{'es' if total != 1 else ''} nueva{'s' if total != 1 else ''} en {where}.",
                [("Ver", self._open_watched, False),
                 ("Bajar nuevas", lambda: [self._download_new(v["url"]) for v in with_new], True)])

    def _download_new(self, url: str):
        v = next((x for x in self.watched if x["url"] == url), None)
        if v is None:
            return
        if self._enqueue(v["url"], overrides={"folder": v["folder"], "only_new": True}):
            self.watched_new[url] = []
            v["new_count"] = 0
            save_config(self._current_cfg())
            self._refresh_watched()

    def _on_playlist_downloaded(self, url, title):
        """A playlist was downloaded: offer to watch it (once per session and if not watched yet)."""
        self.last_playlist = url
        canonical = watched.playlist_url(url) if watched.is_playlist(url) else ""
        if not canonical or canonical in self._offered_watch or any(v["url"] == canonical for v in self.watched):
            return
        self._offered_watch.add(canonical)
        name = title or "esta playlist"
        self.watched_notice.show(f"¿Vigilar «{name}»? AUDYAX te avisará cuando tenga canciones nuevas.",
                               [("No", lambda: None, False),
                                ("Vigilar", lambda: self._watch(canonical, title), True)])
