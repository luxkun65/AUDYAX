"""
Descargador de música de YouTube / YouTube Music - INTERFAZ GRÁFICA.

Este archivo solo contiene la ventana. Todo el trabajo de descarga está en
motor_descarga.py, que debe estar en la misma carpeta.

Requisitos:
    pip install -U yt-dlp mutagen
    ffmpeg instalado y en el PATH
    (tkinter viene con Python; en Linux: sudo apt install python3-tk)

Uso:  python descargador_gui.py
"""
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from motor_descarga import (
    HAY_MUTAGEN,
    DownloadCancelled,
    cargar_config,
    ejecutar_trabajo,
    guardar_config,
)

# Los tres estados de cada canción y su color
COLORES = {"verde": "#2ecc71", "amarillo": "#f1c40f", "rojo": "#e74c3c"}


# ==============================================================================
# INTERFAZ
# ==============================================================================
def limpiar_tk(texto: str) -> str:
    """Tk antiguo no soporta algunos emojis; se reemplazan para evitar errores."""
    return "".join(c if ord(c) <= 0xFFFF else "□" for c in str(texto))


def crear_punto(color: str, tam: int = 14) -> tk.PhotoImage:
    img = tk.PhotoImage(width=tam, height=tam)
    r = tam / 2 - 1
    c = tam / 2
    for x in range(tam):
        for y in range(tam):
            if (x + 0.5 - c) ** 2 + (y + 0.5 - c) ** 2 <= r * r:
                img.put(color, (x, y))
    return img


def abrir_ruta(ruta):
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(ruta))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(ruta)])
        else:
            subprocess.Popen(["xdg-open", str(ruta)])
    except Exception as e:
        messagebox.showerror("No se pudo abrir", str(e))


# Paleta del tema oscuro con acento verde
C = {
    "bg": "#0a1410", "lateral": "#08110d", "card": "#10201a", "card2": "#173025",
    "input": "#0c1812", "borde": "#1f3a2c", "fila_a": "#0e1c15", "fila_b": "#122419",
    "sel": "#1f6b43", "acento": "#2ecc71", "acento_hover": "#4ade80",
    "texto": "#e8f1ec", "suave": "#8aa399", "sobre_acento": "#04210f",
}


def elegir_fuente(raiz) -> str:
    from tkinter import font as tkfont
    familias = set(tkfont.families(raiz))
    for f in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Ubuntu", "DejaVu Sans"):
        if f in familias:
            return f
    return tkfont.nametofont("TkDefaultFont").actual("family")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Descargador de Música")
        self.geometry("1180x800")
        self.minsize(1050, 620)

        self.cfg = cargar_config()
        self.eventos = queue.Queue()   # worker -> interfaz
        self.trabajos = queue.Queue()  # interfaz -> worker
        self.cancelar = threading.Event()
        self.hilo = None
        self.pendientes = 0
        self.errores_generales = 0
        self.filas = {}  # id -> datos de la canción
        self.puntos = {nombre: crear_punto(c) for nombre, c in COLORES.items()}

        self._construir()
        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.after(100, self._procesar_eventos)
        self.after(400, self._revisar_ffmpeg)

    # ---------------------------------------------------------------- estilo
    def _estilos(self):
        self.fuente = F = elegir_fuente(self)
        self.configure(background=C["bg"])
        # Lista desplegable de los Combobox
        self.option_add("*TCombobox*Listbox.background", C["input"])
        self.option_add("*TCombobox*Listbox.foreground", C["texto"])
        self.option_add("*TCombobox*Listbox.selectBackground", C["sel"])
        self.option_add("*TCombobox*Listbox.selectForeground", C["texto"])
        self.option_add("*TCombobox*Listbox.font", (F, 10))

        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=C["bg"], foreground=C["texto"], font=(F, 10),
                    bordercolor=C["borde"], lightcolor=C["borde"], darkcolor=C["borde"],
                    troughcolor=C["input"], focuscolor=C["card"])

        s.configure("TFrame", background=C["bg"])
        s.configure("Card.TFrame", background=C["card"])
        s.configure("TLabel", background=C["bg"], foreground=C["texto"])
        s.configure("Card.TLabel", background=C["card"], foreground=C["texto"])
        s.configure("CardBold.TLabel", background=C["card"], foreground=C["texto"], font=(F, 10, "bold"))
        s.configure("Muted.TLabel", background=C["bg"], foreground=C["suave"])
        s.configure("Title.TLabel", background=C["bg"], foreground=C["texto"], font=(F, 17, "bold"))

        s.configure("TEntry", fieldbackground=C["input"], foreground=C["texto"],
                    insertcolor=C["texto"], padding=7)
        s.map("TEntry", bordercolor=[("focus", C["acento"])],
              lightcolor=[("focus", C["acento"])], darkcolor=[("focus", C["acento"])])

        s.configure("TCombobox", fieldbackground=C["input"], background=C["card2"],
                    foreground=C["texto"], arrowcolor=C["texto"], padding=5,
                    selectbackground=C["input"], selectforeground=C["texto"])
        s.map("TCombobox",
              fieldbackground=[("disabled", C["card"]), ("readonly", C["input"])],
              foreground=[("disabled", C["suave"])],
              arrowcolor=[("disabled", C["suave"])],
              selectbackground=[("readonly", C["input"])],
              selectforeground=[("readonly", C["texto"])])

        s.configure("TButton", background=C["card2"], foreground=C["texto"],
                    padding=(14, 7), borderwidth=1, relief="flat")
        s.map("TButton",
              background=[("disabled", C["card"]), ("pressed", C["borde"]), ("active", "#1f3f30")],
              foreground=[("disabled", C["suave"])])
        s.configure("Accent.TButton", background=C["acento"], foreground=C["sobre_acento"],
                    font=(F, 10, "bold"), padding=(18, 7), bordercolor=C["acento"])
        s.map("Accent.TButton",
              background=[("pressed", "#1fa85a"), ("active", C["acento_hover"])],
              bordercolor=[("active", C["acento_hover"])])

        s.configure("Card.TCheckbutton", background=C["card"], foreground=C["texto"],
                    indicatorbackground=C["input"], indicatorforeground=C["sobre_acento"],
                    indicatorcolor=C["input"])
        s.map("Card.TCheckbutton",
              background=[("active", C["card"])],
              foreground=[("disabled", C["suave"])],
              indicatorbackground=[("disabled", C["card"]), ("selected", C["acento"])],
              indicatorcolor=[("disabled", C["card"]), ("selected", C["acento"])])

        s.configure("Treeview", background=C["fila_a"], fieldbackground=C["fila_a"],
                    foreground=C["texto"], rowheight=30, borderwidth=0, font=(F, 10))
        s.map("Treeview", background=[("selected", C["sel"])], foreground=[("selected", "#ffffff")])
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        # El número (texto) se dibuja a la izquierda del punto de color (imagen)
        s.layout("Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
            ("Treeitem.indicator", {"side": "left", "sticky": ""}),
            ("Treeitem.text", {"side": "left", "sticky": ""}),
            ("Treeitem.image", {"side": "left", "sticky": ""}),
        ]})])
        s.configure("Treeview.Heading", background=C["card"], foreground=C["texto"],
                    font=(F, 10, "bold"), relief="flat", padding=(8, 8), borderwidth=0)
        s.map("Treeview.Heading", background=[("active", C["card2"])])

        for orient in ("Vertical", "Horizontal"):
            s.configure(f"{orient}.TScrollbar", background=C["card2"], troughcolor=C["bg"],
                        bordercolor=C["bg"], lightcolor=C["card2"], darkcolor=C["card2"],
                        arrowcolor=C["suave"], relief="flat", gripcount=0)
            s.map(f"{orient}.TScrollbar", background=[("active", C["borde"])])

        s.configure("Verde.Horizontal.TProgressbar", troughcolor=C["input"], background=C["acento"],
                    bordercolor=C["input"], lightcolor=C["acento"], darkcolor=C["acento"])

    def _tarjeta(self, padre, padding=12):
        """Panel con borde fino. Devuelve (marco_externo, interior)."""
        marco = tk.Frame(padre, bg=C["card"], highlightthickness=1,
                         highlightbackground=C["borde"], highlightcolor=C["borde"])
        interior = ttk.Frame(marco, style="Card.TFrame", padding=padding)
        interior.pack(fill="both", expand=True)
        return marco, interior

    def _construir_lateral(self):
        F = self.fuente
        lat = tk.Frame(self, bg=C["lateral"], width=112)
        lat.grid(row=0, column=0, sticky="ns")
        lat.grid_propagate(False)
        lat.pack_propagate(False)
        tk.Label(lat, text="♫", bg=C["lateral"], fg=C["acento"], font=(F, 30, "bold")).pack(pady=(18, 22))

        def item(texto, activo=False, comando=None):
            bg = "#12422a" if activo else C["lateral"]
            lbl = tk.Label(lat, text=texto, bg=bg, fg=C["texto"] if activo else C["suave"],
                           font=(F, 10, "bold" if activo else "normal"), pady=12, cursor="hand2")
            lbl.pack(fill="x", padx=10, pady=2)
            if not activo:
                lbl.bind("<Enter>", lambda e: lbl.configure(bg=C["card"], fg=C["texto"]))
                lbl.bind("<Leave>", lambda e: lbl.configure(bg=C["lateral"], fg=C["suave"]))
            if comando:
                lbl.bind("<Button-1>", lambda e: comando())

        item("Inicio", activo=True)
        item("Acerca de", comando=lambda: messagebox.showinfo(
            "Acerca de", "Descargador de Música\n\nUsa yt-dlp, FFmpeg y mutagen."))

    # ---------------------------------------------------------------- construcción
    def _construir(self):
        self._estilos()
        F = self.fuente

        self.var_url = tk.StringVar()
        self.var_carpeta = tk.StringVar(value=self.cfg["carpeta"])
        self.var_formato = tk.StringVar(value=self.cfg["formato"])
        self.var_calidad = tk.StringVar(value=self.cfg["calidad"])
        rg_inicial = bool(self.cfg["replaygain"]) and HAY_MUTAGEN
        norm_inicial = bool(self.cfg["normalizar"]) and not rg_inicial  # nunca las dos a la vez
        self.var_norm = tk.BooleanVar(value=norm_inicial)
        self.var_recorte = tk.BooleanVar(value=self.cfg["recorte"])
        self.var_rg = tk.BooleanVar(value=rg_inicial)

        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._construir_lateral()

        main = ttk.Frame(self, padding=(18, 14, 18, 10))
        main.grid(row=0, column=1, sticky="nsew")

        # Encabezado
        cab = ttk.Frame(main)
        cab.pack(fill="x", pady=(0, 10))
        ttk.Label(cab, text="Descargador de Música", style="Title.TLabel").pack(anchor="w")
        ttk.Label(cab, text="Busca  •  Descarga  •  Disfruta", style="Muted.TLabel").pack(anchor="w")

        # Tarjeta de controles
        marco_c, sup = self._tarjeta(main)
        marco_c.pack(fill="x")
        sup.columnconfigure(1, weight=1)

        ttk.Label(sup, text="Enlace:", style="CardBold.TLabel").grid(row=0, column=0, sticky="w")
        entrada = ttk.Entry(sup, textvariable=self.var_url)
        entrada.grid(row=0, column=1, sticky="ew", padx=10, pady=4)
        entrada.bind("<Return>", lambda e: self._descargar())
        entrada.focus_set()
        ttk.Button(sup, text="Pegar", command=self._pegar).grid(row=0, column=2, padx=3)
        ttk.Button(sup, text="↓  Descargar", style="Accent.TButton",
                   command=self._descargar).grid(row=0, column=3, padx=3)

        ttk.Label(sup, text="Guardar en:", style="CardBold.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Entry(sup, textvariable=self.var_carpeta).grid(row=1, column=1, sticky="ew", padx=10, pady=4)
        ttk.Button(sup, text="Cambiar…", command=self._elegir_carpeta).grid(row=1, column=2, padx=3)
        ttk.Button(sup, text="Abrir", command=self._abrir_carpeta_base).grid(row=1, column=3, padx=3, sticky="ew")

        opc = ttk.Frame(sup, style="Card.TFrame")
        opc.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Label(opc, text="Formato:", style="Card.TLabel").pack(side="left")
        self.cb_formato = ttk.Combobox(opc, textvariable=self.var_formato, width=9, state="readonly",
                                       values=["mp3", "m4a", "opus", "original"])
        self.cb_formato.pack(side="left", padx=(6, 16))
        self.cb_formato.bind("<<ComboboxSelected>>", lambda e: self._al_cambiar_formato())
        ttk.Label(opc, text="Calidad (kbps):", style="Card.TLabel").pack(side="left")
        self.cb_calidad = ttk.Combobox(opc, textvariable=self.var_calidad, width=6, state="readonly",
                                       values=["128", "192", "256", "320"])
        self.cb_calidad.pack(side="left", padx=(6, 16))
        self.chk_rg = ttk.Checkbutton(opc, text="ReplayGain (recomendado)", style="Card.TCheckbutton",
                                      variable=self.var_rg, command=self._al_marcar_rg)
        self.chk_rg.pack(side="left", padx=(0, 14))
        if not HAY_MUTAGEN:
            self.chk_rg.configure(state="disabled")
        self.chk_norm = ttk.Checkbutton(opc, text="Normalizar volumen (modifica el audio)",
                                        style="Card.TCheckbutton", variable=self.var_norm,
                                        command=self._al_marcar_norm)
        self.chk_norm.pack(side="left", padx=(0, 14))
        ttk.Checkbutton(opc, text="Portada cuadrada", style="Card.TCheckbutton",
                        variable=self.var_recorte).pack(side="left")
        self.btn_cancelar = ttk.Button(opc, text="✕  Cancelar todo", command=self._cancelar, state="disabled")
        self.btn_cancelar.pack(side="right")
        self._al_cambiar_formato()

        # Lista
        marco_l, cont = self._tarjeta(main, padding=0)
        cols =("estado", "titulo", "artista", "album", "anio", "enlace")
        self.tree = ttk.Treeview(cont, columns=cols, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="  #", anchor="w")
        self.tree.column("#0", width=92, minwidth=80, stretch=False, anchor="w")
        for col, texto, ancho in [("estado", "Estado", 150), ("titulo", "Título", 240),
                                  ("artista", "Artista", 160), ("album", "Álbum", 190),
                                  ("anio", "Año", 55), ("enlace", "Enlace", 260)]:
            self.tree.heading(col, text=texto, anchor="w")
            self.tree.column(col, width=ancho, minwidth=50, stretch=(col != "anio"), anchor="w")
        self.tree.tag_configure("a", background=C["fila_a"])
        self.tree.tag_configure("b", background=C["fila_b"])
        sy = ttk.Scrollbar(cont, orient="vertical", command=self.tree.yview)
        sx = ttk.Scrollbar(cont, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        cont.rowconfigure(0, weight=1)
        cont.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._mostrar_detalle())
        self.tree.bind("<Double-1>", self._doble_clic)

        # Detalle
        marco_d, det = self._tarjeta(main)
        marco_d.pack(side="bottom", fill="x")
        ttk.Label(det, text="♪  Detalle de la canción seleccionada", style="CardBold.TLabel").pack(anchor="w")
        self.txt = tk.Text(det, height=6, wrap="word", font="TkFixedFont", relief="flat",
                           background=C["card"], foreground=C["texto"], insertbackground=C["texto"],
                           highlightthickness=0, borderwidth=0, padx=2, pady=6)
        self.txt.pack(fill="x")
        self.txt.configure(state="disabled")
        botones = ttk.Frame(det, style="Card.TFrame")
        botones.pack(fill="x", pady=(4, 0))
        for texto, cmd in [("Copiar enlace", self._copiar_enlace), ("Abrir enlace", self._abrir_enlace),
                           ("Abrir carpeta", self._abrir_carpeta_cancion), ("⟳  Reintentar", self._reintentar)]:
            ttk.Button(botones, text=texto, command=cmd).pack(side="left", padx=(0, 8))
        ttk.Button(botones, text="Limpiar lista", command=self._limpiar_lista).pack(side="right")

        # Barra de estado: contadores por color + progreso general
        barra = ttk.Frame(main)
        barra.pack(side="bottom", fill="x", pady=(10, 0), before=marco_d)
        self.contadores = {}
        for nombre, etiqueta in [("verde", "descargadas"), ("amarillo", "en proceso"), ("rojo", "con error")]:
            lbl = ttk.Label(barra, image=self.puntos[nombre], compound="left", text=f" 0 {etiqueta}")
            lbl.pack(side="left", padx=(0, 22))
            self.contadores[nombre] = (lbl, etiqueta)
        self.lbl_pct = ttk.Label(barra, text="0%", width=5, anchor="e")
        self.lbl_pct.pack(side="right")
        self.progreso = ttk.Progressbar(barra, style="Verde.Horizontal.TProgressbar",
                                        length=260, maximum=100, mode="determinate")
        self.progreso.pack(side="right", padx=10)

        # La lista se empaqueta al final: ocupa solo el espacio que sobra
        marco_l.pack(fill="both", expand=True, pady=10)

    # ---------------------------------------------------------------- acciones
    def _al_cambiar_formato(self):
        estado = "disabled" if self.var_formato.get() == "original" else "readonly"
        self.cb_calidad.configure(state=estado)
        self.chk_norm.configure(state="disabled" if estado == "disabled" else "normal")

    def _al_marcar_rg(self):
        """ReplayGain y normalizar son excluyentes: marcar una desmarca la otra."""
        if self.var_rg.get():
            self.var_norm.set(False)

    def _al_marcar_norm(self):
        if self.var_norm.get():
            self.var_rg.set(False)

    def _pegar(self):
        try:
            self.var_url.set(self.clipboard_get().strip())
        except tk.TclError:
            pass

    def _elegir_carpeta(self):
        nueva = filedialog.askdirectory(initialdir=self.var_carpeta.get() or None,
                                        title="Elige la carpeta donde guardar")
        if nueva:
            self.var_carpeta.set(os.path.normpath(nueva))

    def _cfg_actual(self) -> dict:
        return {
            "carpeta": self.var_carpeta.get().strip(),
            "formato": self.var_formato.get(),
            "calidad": self.var_calidad.get(),
            "normalizar": bool(self.var_norm.get()),
            "recorte": bool(self.var_recorte.get()),
            "replaygain": bool(self.var_rg.get()) and HAY_MUTAGEN,
        }

    def _descargar(self):
        url = self.var_url.get().strip()
        if not url.startswith(("http://", "https://")):
            messagebox.showwarning("Enlace no válido", "Pega un enlace de YouTube o YouTube Music.")
            return
        if self._encolar(url):
            self.var_url.set("")

    def _encolar(self, url: str) -> bool:
        cfg = self._cfg_actual()
        if not cfg["carpeta"]:
            messagebox.showwarning("Falta la carpeta", "Elige dónde guardar las canciones.")
            return False
        try:
            Path(cfg["carpeta"]).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Carpeta no válida", str(e))
            return False
        guardar_config(cfg)
        self.pendientes += 1
        self.trabajos.put((url, cfg))
        if self.hilo is None or not self.hilo.is_alive():
            self.hilo = threading.Thread(target=self._hilo_trabajo, daemon=True)
            self.hilo.start()
        self._actualizar_botones()
        return True

    def _hilo_trabajo(self):
        while True:
            url, cfg = self.trabajos.get()
            self.cancelar.clear()
            try:
                ejecutar_trabajo(url, cfg, self.eventos.put, self.cancelar)
            except DownloadCancelled:
                self.eventos.put(("cancelado",))
            except Exception as e:
                self.eventos.put(("error", "?", str(e)))
            finally:
                self.eventos.put(("fin",))

    def _cancelar(self):
        self.cancelar.set()
        try:  # descarta los trabajos que aún no empezaron
            while True:
                self.trabajos.get_nowait()
                self.pendientes -= 1
        except queue.Empty:
            pass
        self._actualizar_botones()

    def _actualizar_botones(self):
        self.btn_cancelar.configure(state="normal" if self.pendientes > 0 else "disabled")

    def _revisar_ffmpeg(self):
        if not shutil.which("ffmpeg"):
            messagebox.showwarning(
                "Falta FFmpeg",
                "No encuentro FFmpeg en tu equipo. Sin él no se puede convertir el audio, "
                "añadir las portadas ni calcular el ReplayGain.\n\nInstálalo y añádelo al PATH.")
        if not HAY_MUTAGEN:
            messagebox.showwarning(
                "Falta mutagen",
                "Para escribir el ReplayGain hace falta la librería mutagen.\n\n"
                "Instálala con:  pip install mutagen")

    # ---------------------------------------------------------------- lista
    @staticmethod
    def _texto_num(num: int) -> str:
        return f"{num:\u2007>3} "  # espacios de cifra: los puntos quedan alineados

    @staticmethod
    def _tag_fila(num: int) -> str:
        return "a" if num % 2 else "b"

    def _fila(self, vid: str, **campos):
        nueva = vid not in self.filas
        if nueva:
            self.filas[vid] = {"num": len(self.filas) + 1, "color": "amarillo", "texto": "En cola",
                               "titulo": vid, "artista": "", "album": "", "anio": "", "url": "",
                               "ruta": "", "motivo": "", "replaygain": ""}
        self.filas[vid].update({k: v for k, v in campos.items() if v is not None})
        f = self.filas[vid]
        valores = [limpiar_tk(x) for x in (f["texto"], f["titulo"], f["artista"], f["album"], f["anio"], f["url"])]
        if nueva:
            self.tree.insert("", "end", iid=vid, text=self._texto_num(f["num"]), image=self.puntos[f["color"]],
                             values=valores, tags=(self._tag_fila(f["num"]),))
            self.tree.see(vid)
        else:
            self.tree.item(vid, image=self.puntos[f["color"]], values=valores)
        if self.tree.selection() == (vid,):
            self._mostrar_detalle()
        self._actualizar_contadores()

    def _renumerar(self):
        for i, (vid, f) in enumerate(self.filas.items(), 1):
            f["num"] = i
            self.tree.item(vid, text=self._texto_num(i), tags=(self._tag_fila(i),))

    def _actualizar_contadores(self):
        cuenta = {"verde": 0, "amarillo": 0, "rojo": 0}
        for f in self.filas.values():
            cuenta[f["color"]] += 1
        for nombre, (lbl, etiqueta) in self.contadores.items():
            lbl.configure(text=f" {cuenta[nombre]} {etiqueta}")
        total = len(self.filas)
        hechas = cuenta["verde"] + cuenta["rojo"]
        pct = int(hechas * 100 / total) if total else 0
        self.progreso["value"] = pct
        self.lbl_pct.configure(text=f"{pct}%")

    def _procesar_eventos(self):
        try:
            while True:
                self._manejar(self.eventos.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._procesar_eventos)

    def _manejar(self, ev):
        tipo = ev[0]
        if tipo == "nuevo":
            self._fila(ev[1], color="amarillo", texto="En cola", motivo="", ruta="", **ev[2])
        elif tipo == "info":
            self._fila(ev[1], **ev[2])
        elif tipo == "estado":
            if self.filas.get(ev[1], {}).get("color") != "rojo":
                self._fila(ev[1], color="amarillo", texto=ev[2])
        elif tipo == "listo":
            self._fila(ev[1], color="verde", texto="Descargada", motivo="", **ev[2])
        elif tipo == "rg":
            self._fila(ev[1], replaygain=ev[2])
        elif tipo == "omitida":
            self._fila(ev[1], color="verde", texto="Ya descargada antes")
        elif tipo == "error":
            vid, motivo = ev[1], ev[2]
            if vid == "?":
                self.errores_generales += 1
                vid = f"error-{self.errores_generales}"
                self._fila(vid, titulo="Error general", color="rojo", texto="Error", motivo=motivo)
            else:
                self._fila(vid, color="rojo", texto="Error: " + motivo[:60], motivo=motivo)
        elif tipo == "cancelado":
            for vid, f in list(self.filas.items()):
                if f["color"] == "amarillo":
                    self._fila(vid, color="rojo", texto="Cancelada", motivo="Cancelada por el usuario")
        elif tipo == "fin":
            self.pendientes = max(0, self.pendientes - 1)
            self._actualizar_botones()

    def _limpiar_lista(self):
        for vid, f in list(self.filas.items()):
            if f["color"] != "amarillo":
                self.tree.delete(vid)
                del self.filas[vid]
        self._renumerar()
        self._mostrar_detalle()
        self._actualizar_contadores()

    # ---------------------------------------------------------------- detalle
    def _seleccion(self):
        sel = self.tree.selection()
        return sel[0] if sel and sel[0] in self.filas else None

    def _mostrar_detalle(self):
        vid = self._seleccion()
        lineas = []
        if vid:
            f = self.filas[vid]
            estado = {"verde": "Descargada", "amarillo": "En proceso", "rojo": "Error"}[f["color"]]
            lineas = [f"Nº:       {f['num']}", f"Estado:   {estado} ({f['texto']})", f"Título:   {f['titulo']}",
                      f"Artista:  {f['artista']}", f"Álbum:    {f['album']}", f"Año:      {f['anio']}",
                      f"Enlace:   {f['url']}", f"Archivo:  {f['ruta']}"]
            if f["replaygain"]:
                lineas.append(f"Volumen:  ReplayGain {f['replaygain']}")
            if f["motivo"]:
                lineas.append(f"Motivo:   {f['motivo']}")
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", limpiar_tk("\n".join(lineas)) if lineas else "Selecciona una canción de la lista.")
        self.txt.configure(state="disabled")

    def _doble_clic(self, evento):
        vid = self.tree.identify_row(evento.y)
        if vid in self.filas and self.filas[vid]["url"]:
            webbrowser.open(self.filas[vid]["url"])

    def _copiar_enlace(self):
        vid = self._seleccion()
        if vid and self.filas[vid]["url"]:
            self.clipboard_clear()
            self.clipboard_append(self.filas[vid]["url"])

    def _abrir_enlace(self):
        vid = self._seleccion()
        if vid and self.filas[vid]["url"]:
            webbrowser.open(self.filas[vid]["url"])

    def _abrir_carpeta_cancion(self):
        vid = self._seleccion()
        ruta = self.filas[vid]["ruta"] if vid else ""
        if ruta and Path(ruta).exists():
            abrir_ruta(Path(ruta).parent)
        else:
            self._abrir_carpeta_base()

    def _abrir_carpeta_base(self):
        carpeta = Path(self.var_carpeta.get())
        if carpeta.exists():
            abrir_ruta(carpeta)
        else:
            messagebox.showinfo("Carpeta", "Esa carpeta todavía no existe.")

    def _reintentar(self):
        vid = self._seleccion()
        if not vid:
            return
        f = self.filas[vid]
        if f["color"] != "rojo" or not f["url"]:
            messagebox.showinfo("Reintentar", "Solo se pueden reintentar canciones con error que tengan enlace.")
            return
        self._encolar(f["url"])

    # ---------------------------------------------------------------- cierre
    def _cerrar(self):
        guardar_config(self._cfg_actual())
        self.cancelar.set()
        self.destroy()


def main():
    if sys.platform.startswith("win"):
        try:  # texto nítido en pantallas de alta resolución
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()