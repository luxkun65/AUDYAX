"""Settings window (tabs General, Descargas, Letras y álbum, Avanzado)."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from audyax import VERSION
from audyax.core.config import (COOKIE_BROWSERS, DEFAULT_CONFIG, MAX_PARALLEL, VALID_PROXY, parallel_downloads,
                                save_config)
from audyax.downloader.updates import can_self_update, ytdlp_version
from audyax.gui.desktop import no_tray_reason, notifications_available, tray_available
from audyax.gui.style import STATUS_COLORS, C, icon, open_path

# "Organize downloads" options (Settings): saved key -> text and example
ORGANIZE_OPTIONS = {
    "flat": ("Todo junto (sin carpetas)", "Descargas/One More Time.mp3"),
    "artist": ("Una carpeta por artista", "Descargas/Daft Punk/One More Time.mp3"),
    "artist_album": ("Artista / Álbum", "Descargas/Daft Punk/Discovery/One More Time.mp3"),
}


# Cookie menu options: text -> saved value
COOKIE_SOURCES = {"Firefox (recomendado)": "firefox", "Archivo cookies.txt": "file"}


COOKIE_SOURCES.update({n.capitalize(): n for n in COOKIE_BROWSERS if n != "firefox"})


class SettingsMixin:
    """Settings window. Mixed into App."""

    def _open_settings(self):
        top = ctk.CTkToplevel(self)
        top.title("Configuración")
        top.geometry("500x640")
        top.resizable(False, True)
        top.transient(self)
        top.configure(fg_color=C["bg"])
        tabs = ctk.CTkTabview(top, fg_color=C["card"], corner_radius=14, border_width=1,
                                  border_color=C["border"], segmented_button_fg_color=C["card2"],
                                  segmented_button_selected_color=C["accent"],
                                  segmented_button_selected_hover_color=C["accent_hover"],
                                  segmented_button_unselected_color=C["card2"],
                                  segmented_button_unselected_hover_color=C["hover"],
                                  text_color=C["text"])
        tabs.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.settings_tabs = tabs

        def tab(name):
            tabs.add(name)
            container = ctk.CTkScrollableFrame(tabs.tab(name), fg_color="transparent")
            container.pack(fill="both", expand=True)
            return container
        self._tab_general(tab("General"))
        self._tab_downloads(tab("Descargas"))
        self._tab_extras(tab("Letras y álbum"))
        self._tab_advanced(tab("Avanzado"))
        tabs.set("General")
        self.after(150, top.focus)

    # Common pieces of the settings window
    def _cfg_title(self, parent, text, first=False):
        ctk.CTkLabel(parent, text=text, font=self.f_bold).pack(anchor="w", padx=12, pady=(6 if first else 18, 6))

    def _cfg_note(self, parent, text, color=None):
        lbl = ctk.CTkLabel(parent, text=text, font=self.f_small, text_color=color or C["muted"],
                           wraplength=385, justify="left", anchor="w")
        lbl.pack(anchor="w", padx=12, pady=(2, 0), fill="x")
        return lbl

    def _cfg_switch(self, parent, text, key, on_change=None, status="normal"):
        var = tk.BooleanVar(value=bool(self.cfg.get(key, DEFAULT_CONFIG.get(key))))

        def change():
            self.cfg[key] = bool(var.get())
            save_config(self._current_cfg())
            if on_change:
                on_change()
        sw = ctk.CTkSwitch(parent, text=text, variable=var, command=change, font=self.f_normal,
                           progress_color=C["accent"], state=status)
        sw.pack(anchor="w", padx=12, pady=(6, 2))
        return var

    def _cfg_entry(self, parent, width=200, **kw):
        return ctk.CTkEntry(parent, width=width, height=32, corner_radius=10, font=self.f_normal,
                            fg_color=C["input"], border_color=C["border"], text_color=C["text"], **kw)

    def _tab_general(self, t):
        self._cfg_title(t, "Apariencia", first=True)
        seg = ctk.CTkSegmentedButton(
            t, values=["Claro", "Oscuro"], font=self.f_normal, selected_color=C["accent"],
            selected_hover_color=C["accent_hover"], unselected_color=C["card2"],
            unselected_hover_color=C["hover"], text_color=C["text"],
            command=lambda v: self._apply_theme("dark" if v == "Oscuro" else "light"))
        seg.set("Oscuro" if self.theme == "dark" else "Claro")
        seg.pack(anchor="w", padx=12)

        self._cfg_title(t, "Notificaciones y bandeja del sistema")
        has_notifications, has_tray = notifications_available(), tray_available()
        self._cfg_switch(t, "Avisarme cuando terminen las descargas", "notifications",
                              status="normal" if has_notifications else "disabled")
        self._cfg_note(t, "Solo avisa si AUDYAX no está en primer plano (minimizado, en la bandeja "
                          "o detrás de otra ventana)." if has_notifications else
                       "No disponible: falta la librería plyer (pip install plyer).")
        self._cfg_switch(t, "Seguir en segundo plano al cerrar (X)", "keep_in_background",
                              status="normal" if has_tray else "disabled")
        self._cfg_note(t, "Al cerrar la ventana, AUDYAX sigue abierto junto al reloj (y sigue "
                          "descargando). Clic en su icono para volver; para salir del todo: clic "
                          "derecho en el icono → Salir. Minimizar funciona como siempre."
                       if has_tray else "No disponible en este sistema (" + (no_tray_reason() or
                                                                                  "falta pystray") + ").")

        self._cfg_title(t, "Portapapeles")
        self._cfg_switch(t, "Ofrecer pegar los enlaces de YouTube copiados", "detect_clipboard")

        self._cfg_title(t, "Archivos del programa")
        row = ctk.CTkFrame(t, fg_color="transparent")
        row.pack(anchor="w", padx=12)
        self._button(row, "Abrir log", lambda: self._open_program_file("descarga.log"), height=32).pack(
            side="left", padx=(0, 8))
        self._button(row, "Faltantes", lambda: self._open_program_file("faltantes.txt"), height=32).pack(
            side="left")

    def _tab_downloads(self, t):
        self._organize_section(t)

        self._cfg_title(t, "Descargas simultáneas")
        seg_n = ctk.CTkSegmentedButton(
            t, values=[str(i) for i in range(1, MAX_PARALLEL + 1)], font=self.f_normal,
            selected_color=C["accent"], selected_hover_color=C["accent_hover"],
            unselected_color=C["card2"], unselected_hover_color=C["hover"], text_color=C["text"],
            command=lambda v: self._set_parallel(int(v)))
        seg_n.set(str(parallel_downloads(self.cfg)))
        seg_n.pack(anchor="w", padx=12)
        self._cfg_note(t, "Canciones que se bajan a la vez. Con más, YouTube puede frenar las descargas.")

        self._cfg_title(t, "Playlists vigiladas")
        self._cfg_switch(t, "Revisar si tienen canciones nuevas al abrir AUDYAX", "check_watched")
        self._cfg_switch(t, "Bajar las canciones nuevas sin preguntar", "auto_download_new")
        self._cfg_note(t, "Las playlists se agregan desde «Vigiladas» en la barra de la izquierda.")

        self._cfg_title(t, "Actualizaciones")
        if can_self_update():
            self._cfg_note(t, f"AUDYAX {VERSION}  ·  yt-dlp {ytdlp_version()}")
        else:
            self._cfg_note(t, f"AUDYAX {VERSION} (incluye yt-dlp {ytdlp_version()})")
        self._cfg_switch(t, "Avisar al abrir si hay una versión nueva", "check_ytdlp")
        self._button(t, "Buscar actualización", lambda: self._check_ytdlp(manual=True),
                    icon("refresh", 16), height=32).pack(anchor="w", padx=12, pady=(10, 0))

    def _tab_extras(self, t):
        """Lyrics and MusicBrainz / Cover Art Archive data."""
        self._cfg_title(t, "Letras", first=True)
        names = {"off": "No", "lrc": "Archivo .lrc", "embedded": "Dentro del archivo", "both": "Ambas"}
        inverse = {v: k for k, v in names.items()}

        def change_lyrics(v):
            self.cfg["lyrics"] = inverse[v]
            save_config(self._current_cfg())
        seg = ctk.CTkSegmentedButton(
            t, values=list(names.values()), font=self.f_normal, selected_color=C["accent"],
            selected_hover_color=C["accent_hover"], unselected_color=C["card2"],
            unselected_hover_color=C["hover"], text_color=C["text"], command=change_lyrics)
        seg.set(names.get(self.cfg.get("lyrics", "off"), "No"))
        seg.pack(anchor="w", padx=12)
        self._cfg_note(t, "Busca la letra en LRCLIB al terminar cada canción. «Archivo .lrc» la deja "
                          "al lado de la canción (la leen la mayoría de reproductores); «Dentro del "
                          "archivo» la guarda en sus etiquetas. Si hay letra sincronizada, el mini "
                          "reproductor la va mostrando mientras suena.")

        self._cfg_title(t, "Álbum, año y portada")
        self._cfg_switch(t, "Completar álbum y año con MusicBrainz", "musicbrainz")
        self._cfg_switch(t, "Usar la portada del álbum (Cover Art Archive)", "caa_cover")
        self._cfg_note(t, "Solo cuando YouTube no trae el álbum o el año. Si la canción no aparece "
                          "con seguridad en MusicBrainz, se deja como viene. La portada del álbum "
                          "reemplaza la miniatura del video; si no hay, se usa la miniatura.")

    def _tab_advanced(self, t):
        """Advanced options. All of them come off; they only act when turned on."""
        self._cfg_note(t, "Estas opciones vienen apagadas. Actívalas solo si las necesitas.")
        dependents = []  # (switch BooleanVar, [widgets that depend on it])

        def refresh():
            for var, widgets in dependents:
                for w in widgets:
                    w.configure(state="normal" if var.get() else "disabled")
            show_file_row()
            validate_proxy()

        # 1) Speed limit
        self._cfg_title(t, "Límite de velocidad")
        var_speed = self._cfg_switch(t, "Limitar la velocidad de descarga", "speed_limit_enabled",
                                        on_change=refresh)
        row = ctk.CTkFrame(t, fg_color="transparent")
        row.pack(anchor="w", padx=12, pady=(4, 0))
        var_mb = tk.StringVar(value=str(self.cfg.get("speed_limit_mb", 2.0)))
        e_mb = self._cfg_entry(row, 70, textvariable=var_mb)
        e_mb.pack(side="left")
        ctk.CTkLabel(row, text="MB/s en total", font=self.f_normal).pack(side="left", padx=8)
        self._cfg_note(t, "Para que una playlist grande no te deje sin internet para lo demás. "
                          "Se reparte entre las descargas simultáneas.")
        dependents.append((var_speed, [e_mb]))

        def save_mb(*_):
            try:
                value = float(var_mb.get().replace(",", "."))
            except ValueError:
                return
            if value > 0:
                self.cfg["speed_limit_mb"] = value
                save_config(self._current_cfg())
        var_mb.trace_add("write", save_mb)

        # 2) Proxy
        self._cfg_title(t, "Proxy")
        var_px = self._cfg_switch(t, "Usar un proxy", "proxy_enabled", on_change=refresh)
        var_proxy = tk.StringVar(value=self.cfg.get("proxy", ""))
        e_proxy = self._cfg_entry(t, 420, textvariable=var_proxy)
        e_proxy.pack(anchor="w", padx=12, pady=(4, 0))
        proxy_error = ctk.CTkLabel(t, text="", font=self.f_small, text_color=C["red"], wraplength=385,
                                justify="left", anchor="w")
        proxy_help = self._cfg_note(t, "Ejemplo: http://servidor:8080  o  socks5://127.0.0.1:1080. "
                                     "Para redes que bloquean YouTube o videos bloqueados en tu país; se usa "
                                     "en descargas, búsquedas y playlists de YouTube Music.")
        dependents.append((var_px, [e_proxy]))

        def validate_proxy(*_):
            text = var_proxy.get().strip()
            bad = bool(var_px.get()) and text and not VALID_PROXY.match(text)
            error = ("Formato no válido: usa http://, https://, socks4:// o socks5:// seguido de "
                     "servidor:puerto." if bad else
                     ("Escribe la dirección del proxy." if var_px.get() and not text else ""))
            proxy_error.configure(text=error)
            if error:  # the red warning only takes space when there's something to say
                proxy_error.pack(anchor="w", padx=12, pady=(2, 0), fill="x", before=proxy_help)
            else:
                proxy_error.pack_forget()
            self.cfg["proxy"] = text
            save_config(self._current_cfg())
        var_proxy.trace_add("write", validate_proxy)

        # 3) Cookies
        self._cfg_title(t, "Cookies (videos con restricción de edad)")
        var_cookies = self._cfg_switch(t, "Usar mi sesión de YouTube", "cookies_enabled", on_change=refresh)
        by_value = {v: k for k, v in COOKIE_SOURCES.items()}
        var_source = tk.StringVar(value=by_value.get(self.cfg.get("cookies_source", "firefox"),
                                                      "Firefox (recomendado)"))
        cookies_row = ctk.CTkFrame(t, fg_color="transparent")
        cookies_row.pack(anchor="w", padx=12, pady=(4, 0))
        ctk.CTkLabel(cookies_row, text="Tomarla de:", font=self.f_normal).pack(side="left", padx=(0, 8))
        cookies_menu = ctk.CTkOptionMenu(
            cookies_row, variable=var_source, values=list(COOKIE_SOURCES), width=210, height=32,
            font=self.f_normal, dropdown_font=self.f_normal, fg_color=C["input"], button_color=C["card2"],
            button_hover_color=C["hover"], text_color=C["text"], dropdown_fg_color=C["card"],
            dropdown_text_color=C["text"], dropdown_hover_color=C["hover"],
            command=lambda v: (self.cfg.__setitem__("cookies_source", COOKIE_SOURCES[v]),
                               save_config(self._current_cfg()), show_file_row()))
        cookies_menu.pack(side="left")
        file_row = ctk.CTkFrame(t, fg_color="transparent")
        var_file = tk.StringVar(value=self.cfg.get("cookies_file", ""))
        e_file = self._cfg_entry(file_row, 300, textvariable=var_file, placeholder_text="cookies.txt")
        e_file.pack(side="left")

        def choose_file():
            path = filedialog.askopenfilename(parent=t.winfo_toplevel(), title="Elige tu archivo cookies.txt",
                                              filetypes=[("Cookies", "*.txt"), ("Todos", "*.*")])
            if path:
                var_file.set(path)
                self.cfg["cookies_file"] = path
                save_config(self._current_cfg())
        btn_file = self._button(file_row, "Elegir…", choose_file, icon("folder", 16), height=32, width=90)
        btn_file.pack(side="left", padx=8)
        cookies_notice = self._cfg_note(t, "")
        self._cfg_note(t, "Usar tu cuenta con programas de descarga puede hacer que YouTube la limite. "
                          "Si puedes, usa una cuenta secundaria (mayor de edad).", color=STATUS_COLORS["yellow"])
        dependents.append((var_cookies, [cookies_menu, e_file, btn_file]))

        def show_file_row():
            source = self.cfg.get("cookies_source", "firefox")
            if source == "file":
                file_row.pack(anchor="w", padx=12, pady=(6, 0), after=cookies_row)
                cookies_notice.configure(text="Un archivo cookies.txt (formato Netscape) exportado con una "
                                        "extensión del navegador, p. ej. «Get cookies.txt LOCALLY».")
            else:
                file_row.pack_forget()
                cookies_notice.configure(text=(
                    "Firefox funciona bien: basta con haber iniciado sesión en YouTube en Firefox."
                    if source == "firefox" else
                    "Ojo: en Windows, Chrome, Edge y demás navegadores basados en Chromium ya no dejan "
                    "que otros programas lean sus cookies. Si falla, usa Firefox o un archivo cookies.txt."))

        refresh()

    def _organize_section(self, top):
        """Settings > Organize downloads (off by default: everything together)."""
        self._cfg_title(top, "Organizar descargas", first=True)
        current = self.cfg.get("organize", "flat")
        if current not in ORGANIZE_OPTIONS:
            current = "flat"
        by_text = {text: key for key, (text, _) in ORGANIZE_OPTIONS.items()}
        var_mode = tk.StringVar(value=ORGANIZE_OPTIONS[current][0])
        var_pl = tk.BooleanVar(value=self.cfg.get("playlists_in_folder", True))
        example = ctk.CTkLabel(top, text="", font=self.f_small, text_color=C["muted"], anchor="w")
        sw_pl = ctk.CTkSwitch(top, text="Las playlists van juntas en su propia carpeta", variable=var_pl,
                              font=self.f_normal, progress_color=C["accent"])

        def apply(*_):
            mode = by_text.get(var_mode.get(), "flat")
            self.cfg["organize"] = mode
            self.cfg["playlists_in_folder"] = bool(var_pl.get())
            example.configure(text="Ejemplo:  " + ORGANIZE_OPTIONS[mode][1])
            sw_pl.configure(state="disabled" if mode == "flat" else "normal")
            save_config(self._current_cfg())

        ctk.CTkOptionMenu(top, variable=var_mode, values=[t for t, _ in ORGANIZE_OPTIONS.values()], width=260,
                          height=32, font=self.f_normal, dropdown_font=self.f_normal, fg_color=C["input"],
                          button_color=C["card2"], button_hover_color=C["hover"], text_color=C["text"],
                          dropdown_fg_color=C["card"], dropdown_text_color=C["text"],
                          dropdown_hover_color=C["hover"], command=apply).pack(anchor="w", padx=12)
        example.pack(anchor="w", padx=12, pady=(6, 6))
        sw_pl.configure(command=apply)
        sw_pl.pack(anchor="w", padx=12)
        apply()

    def _open_program_file(self, name):
        path = Path(self.var_folder.get()) / name
        if path.exists():
            open_path(path)
        else:
            messagebox.showinfo("Archivo", f"Todavía no existe {name} en la carpeta de descargas.")
