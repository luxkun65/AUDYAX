"""
AUDYAX - descargador de música de YouTube / YouTube Music (interfaz gráfica: customtkinter + Pillow).

Este archivo solo contiene la ventana. Todo el trabajo de descarga está en
motor_descarga.py, que debe estar en la misma carpeta.

Requisitos:
    pip install -U yt-dlp mutagen customtkinter pillow tkinterdnd2
    ffmpeg instalado y en el PATH
    (tkinter viene con Python; en Linux: sudo apt install python3-tk)

Uso:  python descargador_gui.py
"""
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox
from tkinter import font as tkfont

import customtkinter as ctk
from PIL import Image, ImageDraw

from motor_descarga import (
    HAY_MUTAGEN,
    DownloadCancelled,
    actualizar_ytdlp,
    buscar,
    cargar_config,
    ejecutar_trabajo,
    es_enlace_youtube,
    es_mas_nueva,
    escribir_etiquetas,
    extraer_enlaces,
    guardar_config,
    info_archivo,
    leer_etiquetas,
    leer_portada,
    puede_actualizarse,
    se_puede_editar,
    ultima_version_ytdlp,
    version_ytdlp,
)

# Arrastrar y soltar: opcional (pip install tkinterdnd2). Sin él la app funciona igual.
try:
    from tkinterdnd2 import DND_TEXT, TkinterDnD
    _BASES_DND = (TkinterDnD.DnDWrapper,)
except ImportError:
    DND_TEXT, TkinterDnD, _BASES_DND = None, None, ()

# ==============================================================================
# PALETA (cada color es una tupla: (modo claro, modo oscuro))
# ==============================================================================
C = {
    "bg": ("#f4f1fa", "#0f0a1a"), "lateral": ("#e9e3f5", "#0b0714"),
    "card": ("#ffffff", "#1a1228"), "card2": ("#ece6f7", "#271a3d"),
    "input": ("#f7f4fc", "#130d20"), "borde": ("#d9cfee", "#33264d"),
    "fila_a": ("#ffffff", "#160f24"), "fila_b": ("#f7f4fc", "#1c132d"),
    "sel": ("#e0d3fa", "#5b34a8"), "nav_activo": ("#e0d3fa", "#3b2268"),
    "acento": ("#7c3aed", "#a78bfa"), "acento_hover": ("#6d28d9", "#c4b5fd"),
    "texto": ("#1e1530", "#efeaf8"), "suave": ("#6e6285", "#a095bd"),
    "sobre_acento": ("#ffffff", "#1a0b3b"), "hover": ("#e4dbf5", "#35264f"),
    "rojo": ("#d63c2c", "#e74c3c"),
}
REPO_URL = "https://github.com/luxkun65/AUDYAX"
COLORES = {"verde": "#2ecc71", "amarillo": "#f1c40f", "rojo": "#e74c3c"}

# Ancho de cada columna de la lista (0 = ocupa el espacio sobrante)
ANCHOS = [34, 150, 52, 0, 170, 170, 60, 250]


# ==============================================================================
# UTILIDADES GRÁFICAS (Pillow)
# ==============================================================================
def limpiar_tk(texto) -> str:
    """Tk antiguo no soporta algunos emojis; se reemplazan para evitar errores."""
    return "".join(c if ord(c) <= 0xFFFF else "□" for c in str(texto))


def corto(texto, n: int) -> str:
    t = limpiar_tk(texto)
    return t if len(t) <= n else t[: n - 1] + "…"


def _dibujar_icono(nombre: str, color: str, tam: int) -> Image.Image:
    """Dibuja un icono sencillo con líneas, a 4x de tamaño para que quede suave."""
    S = 4
    n = tam * S
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = max(2, round(n * 0.09))

    def pt(x, y):
        return (x * n, y * n)

    def linea(puntos, ancho=w, c=color):
        d.line([pt(x, y) for x, y in puntos], fill=c, width=ancho, joint="curve")

    def caja(x0, y0, x1, y1):
        return (x0 * n, y0 * n, x1 * n, y1 * n)

    if nombre == "home":
        d.polygon([pt(.5, .1), pt(.95, .52), pt(.8, .52), pt(.8, .9), pt(.6, .9), pt(.6, .62),
                   pt(.4, .62), pt(.4, .9), pt(.2, .9), pt(.2, .52), pt(.05, .52)], fill=color)
    elif nombre == "gear":
        c = n / 2
        for k in range(8):
            a = math.radians(k * 45)
            d.line([(c + math.cos(a) * n * .28, c + math.sin(a) * n * .28),
                    (c + math.cos(a) * n * .44, c + math.sin(a) * n * .44)],
                   fill=color, width=int(n * .16))
        d.ellipse(caja(.2, .2, .8, .8), fill=color)
        d.ellipse(caja(.38, .38, .62, .62), fill=(0, 0, 0, 0))
    elif nombre == "info":
        d.ellipse(caja(.08, .08, .92, .92), outline=color, width=w)
        d.ellipse(caja(.45, .24, .55, .34), fill=color)
        linea([(.5, .45), (.5, .72)], w + 1)
    elif nombre == "link":
        d.rounded_rectangle(caja(.04, .32, .58, .68), radius=n * .18, outline=color, width=w)
        d.rounded_rectangle(caja(.42, .32, .96, .68), radius=n * .18, outline=color, width=w)
    elif nombre == "folder":
        linea([(.06, .24), (.38, .24), (.48, .34), (.94, .34), (.94, .84), (.06, .84), (.06, .24)])
    elif nombre == "download":
        linea([(.5, .1), (.5, .62)])
        linea([(.28, .42), (.5, .64), (.72, .42)])
        linea([(.14, .72), (.14, .88), (.86, .88), (.86, .72)])
    elif nombre == "cancel":
        d.ellipse(caja(.1, .1, .9, .9), outline=color, width=w)
        linea([(.22, .78), (.78, .22)])
    elif nombre == "copy":
        d.rounded_rectangle(caja(.3, .3, .9, .92), radius=n * .08, outline=color, width=w)
        linea([(.18, .68), (.18, .1), (.62, .1)])
    elif nombre == "external":
        d.rounded_rectangle(caja(.08, .28, .72, .92), radius=n * .08, outline=color, width=w)
        linea([(.45, .55), (.9, .1)])
        linea([(.6, .1), (.9, .1), (.9, .4)])
    elif nombre == "refresh":
        d.arc(caja(.14, .14, .86, .86), start=60, end=350, fill=color, width=w)
        d.polygon([pt(.66, .42), pt(1.0, .42), pt(.83, .7)], fill=color)
    elif nombre == "trash":
        linea([(.12, .24), (.88, .24)])
        linea([(.36, .24), (.36, .1), (.64, .1), (.64, .24)])
        linea([(.24, .34), (.30, .9), (.70, .9), (.76, .34)])
        linea([(.42, .45), (.42, .78)])
        linea([(.58, .45), (.58, .78)])
    elif nombre == "search":
        d.ellipse(caja(.1, .1, .64, .64), outline=color, width=w)
        linea([(.58, .58), (.9, .9)], w + 1)
    elif nombre == "edit":
        d.polygon([pt(.18, .66), pt(.66, .18), pt(.82, .34), pt(.34, .82)], outline=color, width=w)
        d.polygon([pt(.12, .88), pt(.18, .66), pt(.34, .82)], fill=color)
    elif nombre == "nota":
        d.ellipse(caja(.08, .62, .42, .92), fill=color)
        d.ellipse(caja(.56, .52, .9, .82), fill=color)
        d.polygon([pt(.36, .76), pt(.36, .18), pt(.84, .08), pt(.84, .66), pt(.76, .66),
                   pt(.76, .3), pt(.44, .37), pt(.44, .76)], fill=color)
    elif nombre in ("ok", "reloj", "err"):
        base = {"ok": COLORES["verde"], "reloj": COLORES["amarillo"], "err": COLORES["rojo"]}[nombre]
        d.ellipse(caja(.04, .04, .96, .96), fill=base)
        if nombre == "ok":
            linea([(.28, .52), (.44, .68), (.74, .34)], w + 1, "#ffffff")
        elif nombre == "reloj":
            linea([(.5, .24), (.5, .5), (.7, .62)], w, "#5a4500")
        else:
            linea([(.33, .33), (.67, .67)], w + 1, "#ffffff")
            linea([(.67, .33), (.33, .67)], w + 1, "#ffffff")
    elif nombre == "punto":
        d.ellipse(caja(.05, .05, .95, .95), fill=color)
    return im.resize((tam, tam), Image.LANCZOS)


def icono(nombre: str, tam: int = 18, color=None) -> ctk.CTkImage:
    """Icono que cambia solo de color entre modo claro y oscuro."""
    color = color or C["texto"]
    if isinstance(color, str):
        color = (color, color)
    return ctk.CTkImage(light_image=_dibujar_icono(nombre, color[0], tam * 2),
                        dark_image=_dibujar_icono(nombre, color[1], tam * 2), size=(tam, tam))


def miniatura_redondeada(img: Image.Image, lado: int, radio: int) -> Image.Image:
    """Recorta al centro en cuadrado y redondea las esquinas."""
    S = 3
    img = img.convert("RGB")
    w, h = img.size
    m = min(w, h)
    x0, y0 = (w - m) // 2, (h - m) // 2
    img = img.crop((x0, y0, x0 + m, y0 + m)).resize((lado * S, lado * S), Image.LANCZOS)
    mascara = Image.new("L", (lado * S, lado * S), 0)
    ImageDraw.Draw(mascara).rounded_rectangle((0, 0, lado * S - 1, lado * S - 1),
                                              radius=radio * S, fill=255)
    img = img.convert("RGBA")
    img.putalpha(mascara)
    return img.resize((lado, lado), Image.LANCZOS)


def imagen_cuadrada(img: Image.Image, lado: int, radio: int) -> ctk.CTkImage:
    p = miniatura_redondeada(img, lado * 2, radio * 2)
    return ctk.CTkImage(light_image=p, dark_image=p, size=(lado, lado))


def imagen_vacia(lado: int) -> ctk.CTkImage:
    """Cuadro gris que se muestra mientras llega la miniatura real."""
    claro = miniatura_redondeada(Image.new("RGB", (8, 8), "#d9cfee"), lado * 2, 12)
    oscuro = miniatura_redondeada(Image.new("RGB", (8, 8), "#33264d"), lado * 2, 12)
    return ctk.CTkImage(light_image=claro, dark_image=oscuro, size=(lado, lado))


def imagen_ecualizador() -> ctk.CTkImage:
    """Adorno de la barra lateral: bloques morados que se desvanecen hacia arriba."""
    ancho, alto, S = 96, 100, 2
    im = Image.new("RGBA", (ancho * S, alto * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cuad, hueco = 14 * S, 4 * S
    alturas = [4, 6, 3, 5]
    total = len(alturas) * cuad + (len(alturas) - 1) * hueco
    x0 = (ancho * S - total) // 2
    for c, h in enumerate(alturas):
        for r in range(h):
            alfa = int(255 * (0.9 - 0.7 * r / max(1, h - 1)))
            x = x0 + c * (cuad + hueco)
            y = alto * S - (r + 1) * (cuad + hueco) + hueco
            d.rounded_rectangle((x, y, x + cuad, y + cuad), radius=3 * S, fill=(167, 139, 250, alfa))
    return ctk.CTkImage(light_image=im, dark_image=im, size=(ancho, alto))


def portada_jpeg(img: Image.Image, cuadrada: bool, lado_max: int = 1200) -> bytes:
    """Convierte una imagen cualquiera en la portada JPEG que se incrusta en el archivo."""
    img = img.convert("RGB")
    if cuadrada:
        w, h = img.size
        m = min(w, h)
        img = img.crop(((w - m) // 2, (h - m) // 2, (w - m) // 2 + m, (h - m) // 2 + m))
    img.thumbnail((lado_max, lado_max), Image.LANCZOS)
    salida = BytesIO()
    img.save(salida, "JPEG", quality=90)
    return salida.getvalue()


def descargar_bytes(url: str, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


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


def elegir_fuente(raiz) -> str:
    familias = set(tkfont.families(raiz))
    for f in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Ubuntu", "DejaVu Sans"):
        if f in familias:
            return f
    return tkfont.nametofont("TkDefaultFont").actual("family")


def configurar_columnas(frame):
    for i, a in enumerate(ANCHOS):
        frame.grid_columnconfigure(i, minsize=a, weight=1 if a == 0 else 0)


# ==============================================================================
# FILA DE LA LISTA
# ==============================================================================
class FilaLista:
    """Una canción en la lista: casilla, estado, miniatura y datos."""

    def __init__(self, app, padre, vid):
        self.app, self.vid = app, vid
        F = app.f_norm
        self.frame = ctk.CTkFrame(padre, corner_radius=8, fg_color=C["fila_a"])
        self.frame.pack(fill="x", pady=1)
        configurar_columnas(self.frame)
        self._ult = {}

        self.chk = ctk.CTkCheckBox(
            self.frame, text="", variable=app.filas[vid]["marca"], width=24,
            checkbox_width=18, checkbox_height=18, corner_radius=5, border_width=2,
            fg_color=C["acento"], hover_color=C["acento_hover"], border_color=C["suave"],
            checkmark_color=C["sobre_acento"], command=app._al_marcar_fila)
        self.chk.grid(row=0, column=0, padx=(10, 0), pady=6)

        def etiqueta(col, ancho, **kw):
            lbl = ctk.CTkLabel(self.frame, text="", anchor="w", width=ancho, font=F, **kw)
            lbl.grid(row=0, column=col, sticky="ew", padx=(0, 6))
            return lbl

        self.l_estado = etiqueta(1, 140, compound="left", image=app.ico_estado["amarillo"])
        self.l_thumb = ctk.CTkLabel(self.frame, text="", image=app.img_vacia, width=40, height=40)
        self.l_thumb.grid(row=0, column=2, padx=6, pady=6)
        self.l_titulo = etiqueta(3, 200)
        self.l_artista = etiqueta(4, 160)
        self.l_album = etiqueta(5, 160)
        self.l_anio = etiqueta(6, 50)
        self.l_enlace = etiqueta(7, 230, compound="left", image=app.ico_externo)

        for w in (self.frame, self.l_estado, self.l_thumb, self.l_titulo, self.l_artista,
                  self.l_album, self.l_anio, self.l_enlace):
            w.bind("<Button-1>", lambda e: app._seleccionar(vid), add=True)
            w.bind("<Double-Button-1>", lambda e: app._abrir_enlace(vid), add=True)

    def _set(self, clave, widget, **kw):
        if self._ult.get(clave) != kw:
            widget.configure(**kw)
            self._ult[clave] = kw

    def actualizar(self, f):
        self._set("estado", self.l_estado, text="  " + corto(f["texto"], 20),
                  image=self.app.ico_estado[f["color"]])
        self._set("titulo", self.l_titulo, text=corto(f["titulo"], 34))
        self._set("artista", self.l_artista, text=corto(f["artista"] or "—", 22))
        self._set("album", self.l_album, text=corto(f["album"] or "—", 22))
        self._set("anio", self.l_anio, text=f["anio"] or "—")
        self._set("enlace", self.l_enlace, text="  " + corto(f["url"], 32))
        if f["img_s"] is not None:
            self._set("thumb", self.l_thumb, image=f["img_s"])

    def pintar(self, seleccionada: bool):
        num = self.app.filas[self.vid]["num"]
        color = C["sel"] if seleccionada else (C["fila_a"] if num % 2 else C["fila_b"])
        if self._ult.get("color") != color:
            self.frame.configure(fg_color=color)
            self._ult["color"] = color

    def destruir(self):
        self.frame.destroy()


# ==============================================================================
# AVISO (franja dentro de la tarjeta de controles: enlace copiado, actualización…)
# ==============================================================================
class Aviso:
    def __init__(self, app, padre, fila: int, ico: str):
        self.app, self.fila = app, fila
        self.frame = ctk.CTkFrame(padre, fg_color=C["card2"], corner_radius=10)
        # Primero los botones: así el texto nunca les quita espacio
        ctk.CTkButton(self.frame, text="✕", width=28, height=28, fg_color="transparent",
                      hover_color=C["hover"], text_color=C["suave"],
                      command=self.ocultar).pack(side="right", padx=(0, 6))
        self.botones = ctk.CTkFrame(self.frame, fg_color="transparent")
        self.botones.pack(side="right")
        self.lbl = ctk.CTkLabel(self.frame, text="", image=icono(ico, 16), compound="left",
                                font=app.f_norm, anchor="w")
        self.lbl.pack(side="left", fill="x", expand=True, padx=(10, 8), pady=6)
        self.visible = False

    def mostrar(self, texto: str, acciones=()):
        """acciones: lista de (texto, función, es_principal)."""
        for b in self.botones.winfo_children():
            b.destroy()
        self.lbl.configure(text="  " + limpiar_tk(texto))
        for texto_b, funcion, principal in acciones:
            def accion(f=funcion):
                self.ocultar()
                f()
            self.app._boton(self.botones, texto_b, accion, acento=principal, height=28).pack(
                side="left", padx=(0, 6))
        self.frame.grid(row=self.fila, column=0, columnspan=4, sticky="ew", padx=14, pady=(0, 10))
        self.visible = True

    def ocultar(self):
        self.frame.grid_remove()
        self.visible = False


# ==============================================================================
# RESULTADOS DE BÚSQUEDA
# ==============================================================================
class VentanaResultados(ctk.CTkToplevel):
    """Lista de resultados de 'artista canción' para elegir cuáles descargar."""

    def __init__(self, app, consulta: str, resultados: list):
        super().__init__(app)
        self.app = app
        self.title(f"Resultados: {limpiar_tk(consulta)}")
        self.geometry("760x580")
        self.minsize(560, 380)
        self.transient(app)
        self.configure(fg_color=C["bg"])
        self.resultados = resultados
        self.marcas = {}
        self.miniaturas = {}

        ctk.CTkLabel(self, text=f"  Resultados para “{corto(consulta, 50)}”", image=icono("search", 18),
                     compound="left", font=app.f_det, anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        ctk.CTkLabel(self, text="Marca las canciones que quieres descargar.", font=app.f_norm,
                     text_color=C["suave"], anchor="w").pack(fill="x", padx=16, pady=(0, 8))

        lista = ctk.CTkScrollableFrame(self, fg_color=C["card"], corner_radius=14, border_width=1,
                                       border_color=C["borde"])
        lista.pack(fill="both", expand=True, padx=16)
        for i, r in enumerate(resultados):
            self._fila(lista, i, r)

        pie = ctk.CTkFrame(self, fg_color="transparent")
        pie.pack(fill="x", padx=16, pady=12)
        self.btn_bajar = app._boton(pie, "", self._descargar, icono("download", 18, C["sobre_acento"]),
                                    acento=True, height=36)
        self.btn_bajar.pack(side="right")
        app._boton(pie, "Cancelar", self.destroy, height=36).pack(side="right", padx=8)
        app._boton(pie, "Abrir en el navegador", self._abrir, icono("external", 16),
                   height=36).pack(side="left")
        if resultados:
            self.marcas[resultados[0]["id"]].set(True)  # el primero suele ser el correcto
        self._contar()
        self.after(150, self.focus)

    def _fila(self, padre, i, r):
        var = tk.BooleanVar(value=False)
        self.marcas[r["id"]] = var
        fila = ctk.CTkFrame(padre, fg_color=C["fila_a"] if i % 2 == 0 else C["fila_b"], corner_radius=8)
        fila.pack(fill="x", pady=1)
        ctk.CTkCheckBox(fila, text="", variable=var, width=24, checkbox_width=18, checkbox_height=18,
                        corner_radius=5, border_width=2, fg_color=C["acento"],
                        hover_color=C["acento_hover"], border_color=C["suave"],
                        checkmark_color=C["sobre_acento"], command=self._contar).pack(
            side="left", padx=(10, 6), pady=6)
        thumb = ctk.CTkLabel(fila, text="", image=self.app.img_vacia, width=40, height=40)
        thumb.pack(side="left", padx=6, pady=6)
        self.miniaturas[r["id"]] = thumb
        textos = ctk.CTkFrame(fila, fg_color="transparent")
        textos.pack(side="left", fill="x", expand=True, padx=6)
        ctk.CTkLabel(textos, text=corto(r["titulo"], 70), font=self.app.f_neg, anchor="w").pack(anchor="w")
        sub = " • ".join(p for p in (r.get("canal"), r.get("duracion")) if p)
        ctk.CTkLabel(textos, text=limpiar_tk(sub) or "—", font=self.app.f_peq, text_color=C["suave"],
                     anchor="w").pack(anchor="w")

        def alternar(_e=None, v=var):
            v.set(not v.get())
            self._contar()
        for w in (fila, thumb, textos, *textos.winfo_children()):
            w.bind("<Button-1>", alternar, add=True)
        if r.get("miniatura"):
            self.app.pool.submit(self.app._bajar_imagen_resultado, r["id"], r["miniatura"])

    def poner_imagen(self, vid, datos):
        lbl = self.miniaturas.get(vid)
        if lbl is None:
            return
        try:
            img = Image.open(BytesIO(datos))
            img.load()
            lbl.configure(image=imagen_cuadrada(img, 40, 8))
        except Exception:
            pass

    def _elegidos(self):
        return [r for r in self.resultados if self.marcas[r["id"]].get()]

    def _contar(self):
        n = len(self._elegidos())
        self.btn_bajar.configure(text=f"Descargar ({n})" if n else "Descargar",
                                 state="normal" if n else "disabled")

    def _abrir(self):
        for r in self._elegidos()[:5] or self.resultados[:1]:
            webbrowser.open(r["url"])

    def _descargar(self):
        for r in self._elegidos():
            self.app._encolar(r["url"])
        self.app.var_url.set("")
        self.destroy()


# ==============================================================================
# EDITOR DE ETIQUETAS
# ==============================================================================
class VentanaEtiquetas(ctk.CTkToplevel):
    """Corrige título, artista, álbum, año, género y portada de un archivo ya descargado."""

    CAMPOS = [("titulo", "Título"), ("artista", "Artista"), ("album", "Álbum"),
              ("anio", "Año"), ("genero", "Género")]

    def __init__(self, app, vid: str, ruta: str):
        super().__init__(app)
        self.app, self.vid, self.ruta = app, vid, ruta
        self.title("Editar etiquetas")
        self.geometry("640x400")
        self.resizable(False, False)
        self.transient(app)
        self.configure(fg_color=C["bg"])
        self.portada_nueva = None
        actuales = leer_etiquetas(ruta)

        ctk.CTkLabel(self, text="  Editar etiquetas", image=icono("edit", 18), compound="left",
                     font=app.f_det, anchor="w").pack(fill="x", padx=18, pady=(14, 0))
        ctk.CTkLabel(self, text=corto(Path(ruta).name, 80), font=app.f_peq, text_color=C["suave"],
                     anchor="w").pack(fill="x", padx=18, pady=(0, 10))

        cuerpo = self.app._tarjeta(self)
        cuerpo.pack(fill="both", expand=True, padx=18)
        izq = ctk.CTkFrame(cuerpo, fg_color="transparent")
        izq.pack(side="left", padx=16, pady=16, anchor="n")
        self.l_portada = ctk.CTkLabel(izq, text="", image=app.img_vacia_portada, width=160, height=160)
        self.l_portada.pack()
        datos = leer_portada(ruta)
        if datos:
            self._mostrar_portada(datos)
        app._boton(izq, "Cambiar portada…", self._elegir_portada, icono("folder", 16),
                   height=32).pack(pady=(10, 0), fill="x")

        der = ctk.CTkFrame(cuerpo, fg_color="transparent")
        der.pack(side="left", fill="both", expand=True, padx=(0, 16), pady=16)
        der.grid_columnconfigure(1, weight=1)
        self.vars = {}
        for i, (clave, titulo) in enumerate(self.CAMPOS):
            ctk.CTkLabel(der, text=titulo, font=app.f_neg, anchor="w").grid(
                row=i, column=0, sticky="w", padx=(0, 12), pady=5)
            var = tk.StringVar(value=limpiar_tk(actuales.get(clave, "")))
            self.vars[clave] = var
            ctk.CTkEntry(der, textvariable=var, height=34, corner_radius=10, font=app.f_norm,
                         fg_color=C["input"], border_color=C["borde"], text_color=C["texto"]).grid(
                row=i, column=1, sticky="ew", pady=5)

        pie = ctk.CTkFrame(self, fg_color="transparent")
        pie.pack(fill="x", padx=18, pady=12)
        app._boton(pie, "Guardar", self._guardar, acento=True, width=120, height=36).pack(side="right")
        app._boton(pie, "Cancelar", self.destroy, width=100, height=36).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self._guardar())
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(150, self.focus)

    def _mostrar_portada(self, datos: bytes):
        try:
            img = Image.open(BytesIO(datos))
            img.load()
            self.l_portada.configure(image=imagen_cuadrada(img, 160, 14))
        except Exception:
            pass

    def _elegir_portada(self):
        archivo = filedialog.askopenfilename(
            parent=self, title="Elige la imagen de portada",
            filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp *.bmp"), ("Todos", "*.*")])
        if not archivo:
            return
        try:
            with Image.open(archivo) as img:
                self.portada_nueva = portada_jpeg(img, cuadrada=bool(self.app.var_recorte.get()))
        except Exception as e:
            messagebox.showerror("Imagen no válida", str(e), parent=self)
            return
        self._mostrar_portada(self.portada_nueva)

    def _guardar(self):
        datos = {k: v.get().strip() for k, v in self.vars.items()}
        try:
            escribir_etiquetas(self.ruta, datos, self.portada_nueva)
        except Exception as e:
            messagebox.showerror("No se pudieron guardar las etiquetas", str(e), parent=self)
            return
        self.app._etiquetas_guardadas(self.vid, datos, self.portada_nueva)
        self.destroy()


# ==============================================================================
# VENTANA PRINCIPAL
# ==============================================================================
class App(ctk.CTk, *_BASES_DND):
    def __init__(self):
        self.cfg = cargar_config()
        self.tema = "light" if self.cfg.get("tema") == "light" else "dark"
        ctk.set_appearance_mode(self.tema)
        super().__init__()
        self.hay_dnd = False
        if TkinterDnD is not None:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self.hay_dnd = True
            except Exception:  # tkdnd no disponible en este sistema: sin arrastrar/soltar
                pass
        self.title("AUDYAX")
        self.geometry("1180x800")
        self.minsize(1050, 640)
        self.configure(fg_color=C["bg"])

        fam = elegir_fuente(self)
        self.f_norm = ctk.CTkFont(family=fam, size=13)
        self.f_neg = ctk.CTkFont(family=fam, size=13, weight="bold")
        self.f_peq = ctk.CTkFont(family=fam, size=11)
        self.f_tit = ctk.CTkFont(family=fam, size=24, weight="bold")
        self.f_det = ctk.CTkFont(family=fam, size=18, weight="bold")

        self.eventos = queue.Queue()   # worker -> interfaz
        self.trabajos = queue.Queue()  # interfaz -> worker
        self.cancelar = threading.Event()
        self.hilo = None
        self.pool = ThreadPoolExecutor(max_workers=4)  # descarga de miniaturas
        self.pendientes = 0
        self.errores_generales = 0
        self.filas = {}   # id -> datos de la canción
        self.sel = None   # id de la canción mostrada en el detalle
        self.ventana_resultados = None
        self.buscando = False
        self.actualizando = False
        self._ultimo_ofrecido = ""     # último enlace del portapapeles que ya se ofreció
        self._pp_programado = False

        self.ico_estado = {"verde": icono("ok", 20, "#2ecc71"), "amarillo": icono("reloj", 20, "#f1c40f"),
                           "rojo": icono("err", 20, "#e74c3c")}
        self.ico_punto = {k: icono("punto", 14, c) for k, c in COLORES.items()}
        self.ico_externo = icono("external", 16, C["suave"])
        self.img_vacia = imagen_vacia(40)
        self.img_vacia_grande = imagen_vacia(88)
        self.img_vacia_portada = imagen_vacia(160)

        self._construir()
        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.after(100, self._procesar_eventos)
        self.after(400, self._revisar_ffmpeg)
        self.after(800, self._revisar_portapapeles)
        self.bind("<FocusIn>", self._al_enfocar, add=True)
        if self.cfg.get("revisar_ytdlp", True):
            self.after(2500, lambda: self._revisar_ytdlp(manual=False))

    # ---------------------------------------------------------------- construcción
    def _boton(self, padre, texto, comando, icono_=None, acento=False, **kw):
        if acento:
            estilo = dict(fg_color=C["acento"], hover_color=C["acento_hover"],
                          text_color=C["sobre_acento"], border_width=0)
        else:
            estilo = dict(fg_color="transparent", hover_color=C["hover"], text_color=C["texto"],
                          border_width=1, border_color=C["borde"])
        return ctk.CTkButton(padre, text=texto, command=comando, image=icono_, compound="left",
                             font=self.f_neg if acento else self.f_norm, corner_radius=10,
                             height=kw.pop("height", 36), **estilo, **kw)

    def _tarjeta(self, padre, **kw):
        return ctk.CTkFrame(padre, fg_color=C["card"], corner_radius=14, border_width=1,
                            border_color=C["borde"], **kw)

    def _construir(self):
        rg_inicial = bool(self.cfg["replaygain"]) and HAY_MUTAGEN
        norm_inicial = bool(self.cfg["normalizar"]) and not rg_inicial  # nunca las dos a la vez
        self.var_url = tk.StringVar()
        self.var_carpeta = tk.StringVar(value=self.cfg["carpeta"])
        self.var_formato = tk.StringVar(value=self.cfg["formato"])
        self.var_calidad = tk.StringVar(value=self.cfg["calidad"])
        self.var_norm = tk.BooleanVar(value=norm_inicial)
        self.var_recorte = tk.BooleanVar(value=self.cfg["recorte"])
        self.var_rg = tk.BooleanVar(value=rg_inicial)
        self.var_todas = tk.BooleanVar(value=False)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._construir_lateral()

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=(14, 16), pady=(12, 10))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)

        # Encabezado
        cab = ctk.CTkFrame(main, fg_color="transparent")
        cab.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(cab, text="AUDYAX", font=self.f_tit, anchor="w").pack(anchor="w")
        ctk.CTkLabel(cab, text="Pega el enlace  •  Descarga  •  Disfruta", font=self.f_norm,
                     text_color=C["suave"], anchor="w").pack(anchor="w")

        # Tarjeta de controles
        ctrl = self._tarjeta(main)
        ctrl.grid(row=1, column=0, sticky="ew")
        ctrl.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(ctrl, text=" Enlace:", image=icono("link", 16), compound="left",
                     font=self.f_neg).grid(row=0, column=0, sticky="w", padx=(14, 8), pady=(12, 6))
        entrada = ctk.CTkEntry(ctrl, textvariable=self.var_url, height=38, corner_radius=10,
                               placeholder_text="Pega un enlace de YouTube o escribe artista y canción…", font=self.f_norm,
                               fg_color=C["input"], border_color=C["borde"], text_color=C["texto"])
        entrada.grid(row=0, column=1, sticky="ew", pady=(12, 6))
        entrada.bind("<Return>", lambda e: self._descargar())
        entrada.focus_set()
        self.entrada = entrada
        self._boton(ctrl, "Pegar", self._pegar, width=90, height=38).grid(
            row=0, column=2, padx=(10, 4), pady=(12, 6))
        self.ico_descargar = icono("download", 18, C["sobre_acento"])
        self.ico_buscar = icono("search", 18, C["sobre_acento"])
        self.btn_descargar = self._boton(ctrl, "Descargar", self._descargar, self.ico_descargar,
                                         acento=True, width=130, height=38)
        self.btn_descargar.grid(row=0, column=3, padx=(4, 14), pady=(12, 6))
        # El botón dice "Buscar" cuando lo escrito no es un enlace
        self.var_url.trace_add("write", lambda *a: self._actualizar_boton_principal())

        fila2 = ctk.CTkFrame(ctrl, fg_color="transparent")
        fila2.grid(row=1, column=0, columnspan=4, sticky="ew", padx=14, pady=4)
        ctk.CTkLabel(fila2, text=" Guardar en:", image=icono("folder", 16), compound="left",
                     font=self.f_neg).pack(side="left")
        ctk.CTkEntry(fila2, textvariable=self.var_carpeta, width=250, height=34, corner_radius=10,
                     font=self.f_norm, fg_color=C["input"], border_color=C["borde"],
                     text_color=C["texto"]).pack(side="left", padx=(10, 4))
        self._boton(fila2, "", self._elegir_carpeta, icono("folder", 18), width=38, height=34).pack(
            side="left", padx=(0, 18))
        ctk.CTkLabel(fila2, text="Formato:", font=self.f_norm).pack(side="left")
        self.cb_formato = ctk.CTkOptionMenu(
            fila2, variable=self.var_formato, values=["mp3", "m4a", "opus", "original"], width=100,
            height=34, font=self.f_norm, dropdown_font=self.f_norm, fg_color=C["input"],
            button_color=C["card2"], button_hover_color=C["hover"], text_color=C["texto"],
            dropdown_fg_color=C["card"], dropdown_text_color=C["texto"],
            dropdown_hover_color=C["hover"], command=lambda v: self._al_cambiar_formato())
        self.cb_formato.pack(side="left", padx=(6, 16))
        ctk.CTkLabel(fila2, text="Calidad (kbps):", font=self.f_norm).pack(side="left")
        self.cb_calidad = ctk.CTkOptionMenu(
            fila2, variable=self.var_calidad, values=["128", "192", "256", "320"], width=80,
            height=34, font=self.f_norm, dropdown_font=self.f_norm, fg_color=C["input"],
            button_color=C["card2"], button_hover_color=C["hover"], text_color=C["texto"],
            dropdown_fg_color=C["card"], dropdown_text_color=C["texto"],
            dropdown_hover_color=C["hover"])
        self.cb_calidad.pack(side="left", padx=(6, 0))
        self.btn_cancelar = self._boton(fila2, "Cancelar todo", self._cancelar, icono("cancel", 16),
                                        width=140, height=34, state="disabled")
        self.btn_cancelar.pack(side="right")

        fila3 = ctk.CTkFrame(ctrl, fg_color="transparent")
        fila3.grid(row=2, column=0, columnspan=4, sticky="ew", padx=14, pady=(4, 12))

        def casilla(texto, var, comando=None):
            c = ctk.CTkCheckBox(fila3, text=texto, variable=var, command=comando, font=self.f_norm,
                                checkbox_width=20, checkbox_height=20, corner_radius=5,
                                fg_color=C["acento"], hover_color=C["acento_hover"],
                                border_color=C["suave"], checkmark_color=C["sobre_acento"])
            c.pack(side="left", padx=(0, 22))
            return c

        self.chk_rg = casilla("ReplayGain (recomendado)", self.var_rg, self._al_marcar_rg)
        if not HAY_MUTAGEN:
            self.chk_rg.configure(state="disabled")
        self.chk_norm = casilla("Normalizar volumen (modifica el audio)", self.var_norm, self._al_marcar_norm)
        casilla("Portada cuadrada", self.var_recorte)
        self._al_cambiar_formato()

        # Avisos (ocultos hasta que hagan falta)
        self.aviso_pp = Aviso(self, ctrl, 3, "link")
        self.aviso_yt = Aviso(self, ctrl, 4, "refresh")

        # Lista
        marco_l = self._tarjeta(main)
        marco_l.grid(row=2, column=0, sticky="nsew", pady=10)
        cab_l = ctk.CTkFrame(marco_l, fg_color=C["card2"], corner_radius=10, height=38)
        cab_l.pack(fill="x", padx=6, pady=(6, 2), ipady=2)
        configurar_columnas(cab_l)
        ctk.CTkCheckBox(cab_l, text="", variable=self.var_todas, width=24, checkbox_width=18,
                        checkbox_height=18, corner_radius=5, fg_color=C["acento"],
                        hover_color=C["acento_hover"], border_color=C["suave"],
                        checkmark_color=C["sobre_acento"], command=self._marcar_todas).grid(
            row=0, column=0, padx=(10, 0), pady=6)
        for col, texto, span in [(1, "Estado", 1), (2, "Título", 2), (4, "Artista", 1),
                                 (5, "Álbum", 1), (6, "Año", 1), (7, "Enlace", 1)]:
            ctk.CTkLabel(cab_l, text=texto, font=self.f_neg, anchor="w").grid(
                row=0, column=col, columnspan=span, sticky="w", padx=(4, 0))
        cab_l.grid_columnconfigure(7, minsize=ANCHOS[7] + 14)
        self.lista = ctk.CTkScrollableFrame(marco_l, fg_color="transparent", corner_radius=0,
                                            scrollbar_button_color=C["card2"],
                                            scrollbar_button_hover_color=C["borde"])
        self.lista.pack(fill="both", expand=True, padx=(6, 2), pady=(0, 6))

        # Detalle
        det = self._tarjeta(main)
        det.grid(row=3, column=0, sticky="ew")
        self.det_cab = ctk.CTkFrame(det, fg_color="transparent")
        self.det_cab.pack(fill="x", padx=10, pady=(6, 0))
        self.btn_plegar = ctk.CTkButton(self.det_cab, text="▾", width=26, height=26, fg_color="transparent",
                                        hover_color=C["hover"], text_color=C["texto"],
                                        command=self._plegar_detalle)
        self.btn_plegar.pack(side="left")
        ctk.CTkLabel(self.det_cab, text=" Detalle de la canción seleccionada", image=icono("nota", 16),
                     compound="left", font=self.f_neg).pack(side="left")

        self.det_cuerpo = ctk.CTkFrame(det, fg_color="transparent")
        self.det_cuerpo.pack(fill="x", padx=12, pady=(2, 0))
        self.det_cuerpo.grid_columnconfigure(3, weight=1)
        self.l_portada = ctk.CTkLabel(self.det_cuerpo, text="", image=self.img_vacia_grande,
                                      width=88, height=88)
        self.l_portada.grid(row=0, column=0, padx=(0, 14), pady=4)
        info = ctk.CTkFrame(self.det_cuerpo, fg_color="transparent", width=300)
        info.grid(row=0, column=1, sticky="nw", pady=4)
        self.l_det_titulo = ctk.CTkLabel(info, text="Selecciona una canción de la lista.",
                                         font=self.f_det, anchor="w")
        self.l_det_titulo.pack(anchor="w")
        self.l_det_artista = ctk.CTkLabel(info, text="", font=self.f_norm, text_color=C["suave"], anchor="w")
        self.l_det_artista.pack(anchor="w")
        self.l_chip = ctk.CTkLabel(info, text="", font=self.f_peq, fg_color=C["card2"],
                                   text_color=C["suave"], corner_radius=12, height=24)
        self.l_chip.pack(anchor="w", pady=4)
        self.l_chip.pack_forget()  # solo se muestra cuando hay año o género
        self.l_det_info = ctk.CTkLabel(info, text="", font=self.f_peq, text_color=C["suave"],
                                       anchor="w", justify="left")
        self.l_det_info.pack(anchor="w")
        # Separador fino + datos técnicos del archivo
        ctk.CTkFrame(self.det_cuerpo, width=1, height=70, fg_color=C["borde"]).grid(
            row=0, column=2, sticky="ns", padx=(6, 24))
        datos = ctk.CTkFrame(self.det_cuerpo, fg_color="transparent")
        datos.grid(row=0, column=3, sticky="w")
        self.l_dato = {}
        for i, (clave, titulo) in enumerate([("formato", "Formato"), ("duracion", "Duración"),
                                             ("bitrate", "Bitrate"), ("tamano", "Tamaño"),
                                             ("muestreo", "Muestreo"), ("rg", "ReplayGain")]):
            fila, col = divmod(i, 3)
            celda = ctk.CTkFrame(datos, fg_color="transparent")
            celda.grid(row=fila, column=col, sticky="w", padx=(0, 36), pady=4)
            ctk.CTkLabel(celda, text=titulo.upper(), font=self.f_peq, text_color=C["suave"],
                         anchor="w").pack(anchor="w")
            self.l_dato[clave] = ctk.CTkLabel(celda, text="—", font=self.f_neg, anchor="w")
            self.l_dato[clave].pack(anchor="w")

        self.det_botones = ctk.CTkFrame(det, fg_color="transparent")
        self.det_botones.pack(fill="x", padx=12, pady=(2, 10))
        for texto, ico, cmd in [("Copiar enlace", "copy", self._copiar_enlace),
                                ("Abrir enlace", "external", lambda: self._abrir_enlace()),
                                ("Abrir carpeta", "folder", self._abrir_carpeta_cancion),
                                ("Editar etiquetas", "edit", self._editar_etiquetas),
                                ("Reintentar", "refresh", self._reintentar)]:
            self._boton(self.det_botones, texto, cmd, icono(ico, 16), height=34).pack(side="left", padx=(0, 8))
        self._boton(self.det_botones, "Limpiar lista", self._limpiar_lista, icono("trash", 16),
                    height=34).pack(side="right")

        # Barra de estado: contadores por color + progreso general
        barra = ctk.CTkFrame(main, fg_color="transparent")
        barra.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.contadores = {}
        for nombre, etiqueta in [("verde", "descargadas"), ("amarillo", "en proceso"), ("rojo", "con error")]:
            lbl = ctk.CTkLabel(barra, image=self.ico_punto[nombre], compound="left",
                               text=f"  0 {etiqueta}", font=self.f_norm)
            lbl.pack(side="left", padx=(6, 26))
            self.contadores[nombre] = (lbl, etiqueta)
        self.lbl_pct = ctk.CTkLabel(barra, text="0%", width=44, anchor="e", font=self.f_norm)
        self.lbl_pct.pack(side="right")
        self.progreso = ctk.CTkProgressBar(barra, width=260, height=8, progress_color=C["acento"],
                                           fg_color=C["card2"])
        self.progreso.set(0)
        self.progreso.pack(side="right", padx=10)

        # Arrastrar y soltar enlaces sobre la ventana, el cuadro de texto o la lista
        if self.hay_dnd:
            for w in (self, self.entrada._entry, self.lista._parent_canvas):
                try:
                    w.drop_target_register(DND_TEXT)
                    w.dnd_bind("<<Drop>>", self._al_soltar)
                except Exception:
                    pass
    def _construir_lateral(self):
        lat = ctk.CTkFrame(self, width=112, corner_radius=0, fg_color=C["lateral"])
        lat.grid(row=0, column=0, sticky="ns")
        lat.grid_propagate(False)
        lat.pack_propagate(False)
        ctk.CTkLabel(lat, text="", image=icono("nota", 44, C["acento"])).pack(pady=(18, 18))

        def item(texto, ico, activo=False, comando=None):
            ctk.CTkButton(lat, text=texto, image=icono(ico, 26, C["texto"] if activo else C["suave"]),
                          compound="top", width=92, height=74, corner_radius=12, font=self.f_peq,
                          fg_color=C["nav_activo"] if activo else "transparent",
                          hover_color=C["nav_activo"] if activo else C["hover"],
                          text_color=C["texto"] if activo else C["suave"],
                          command=comando).pack(padx=10, pady=3)

        item("Inicio", "home", activo=True)
        item("Configuración", "gear", comando=self._configuracion)
        item("Acerca de", "info", comando=self._acerca_de)

        ctk.CTkLabel(lat, text="", image=imagen_ecualizador()).pack(side="bottom", pady=(0, 14))
        self.sw_tema = ctk.CTkSwitch(lat, text="", width=46, progress_color=C["acento"],
                                     command=lambda: self._aplicar_tema(
                                         "dark" if self.sw_tema.get() else "light"))
        self.sw_tema.pack(side="bottom", pady=(0, 6))
        ctk.CTkLabel(lat, text="Modo oscuro", font=self.f_peq, text_color=C["suave"]).pack(side="bottom")
        if self.tema == "dark":
            self.sw_tema.select()

    # ---------------------------------------------------------------- tema / configuración
    def _acerca_de(self):
        top = ctk.CTkToplevel(self)
        top.title("Acerca de AUDYAX")
        top.geometry("340x230")
        top.resizable(False, False)
        top.transient(self)
        top.configure(fg_color=C["bg"])
        ctk.CTkLabel(top, text="", image=icono("nota", 44, C["acento"])).pack(pady=(22, 4))
        ctk.CTkLabel(top, text="AUDYAX", font=self.f_det).pack()
        ctk.CTkLabel(top, text="Usa yt-dlp, FFmpeg, mutagen,\ncustomtkinter y Pillow.", font=self.f_peq,
                     text_color=C["suave"], justify="center").pack(pady=(4, 14))
        self._boton(top, "Ver en GitHub", lambda: webbrowser.open(REPO_URL), icono("external", 16),
                    height=34).pack()
        self.after(150, top.focus)

    def _aplicar_tema(self, modo: str):
        self.tema = modo
        ctk.set_appearance_mode(modo)
        if modo == "dark":
            self.sw_tema.select()
        else:
            self.sw_tema.deselect()

    def _configuracion(self):
        top = ctk.CTkToplevel(self)
        top.title("Configuración")
        top.geometry("420x420")
        top.resizable(False, False)
        top.transient(self)
        top.configure(fg_color=C["bg"])
        ctk.CTkLabel(top, text="Apariencia", font=self.f_neg).pack(anchor="w", padx=20, pady=(20, 6))
        seg = ctk.CTkSegmentedButton(
            top, values=["Claro", "Oscuro"], font=self.f_norm, selected_color=C["acento"],
            selected_hover_color=C["acento_hover"], unselected_color=C["card2"],
            unselected_hover_color=C["hover"], text_color=C["texto"],
            command=lambda v: self._aplicar_tema("dark" if v == "Oscuro" else "light"))
        seg.set("Oscuro" if self.tema == "dark" else "Claro")
        seg.pack(anchor="w", padx=20)
        ctk.CTkLabel(top, text="Archivos del programa", font=self.f_neg).pack(anchor="w", padx=20, pady=(18, 6))
        fila = ctk.CTkFrame(top, fg_color="transparent")
        fila.pack(anchor="w", padx=20)
        self._boton(fila, "Abrir log", lambda: self._abrir_archivo_carpeta("descarga.log"), height=32).pack(
            side="left", padx=(0, 8))
        self._boton(fila, "Faltantes", lambda: self._abrir_archivo_carpeta("faltantes.txt"), height=32).pack(
            side="left")

        ctk.CTkLabel(top, text="Portapapeles", font=self.f_neg).pack(anchor="w", padx=20, pady=(18, 6))
        var_pp = tk.BooleanVar(value=self.cfg.get("detectar_portapapeles", True))

        def cambiar_pp():
            self.cfg["detectar_portapapeles"] = bool(var_pp.get())
            guardar_config(self._cfg_actual())
        ctk.CTkSwitch(top, text="Ofrecer pegar los enlaces de YouTube copiados", variable=var_pp,
                      command=cambiar_pp, font=self.f_norm, progress_color=C["acento"]).pack(
            anchor="w", padx=20)

        ctk.CTkLabel(top, text="yt-dlp (motor de descarga)", font=self.f_neg).pack(
            anchor="w", padx=20, pady=(18, 2))
        ctk.CTkLabel(top, text=f"Versión instalada: {version_ytdlp()}", font=self.f_peq,
                     text_color=C["suave"]).pack(anchor="w", padx=20, pady=(0, 6))
        var_yt = tk.BooleanVar(value=self.cfg.get("revisar_ytdlp", True))

        def cambiar_yt():
            self.cfg["revisar_ytdlp"] = bool(var_yt.get())
            guardar_config(self._cfg_actual())
        ctk.CTkSwitch(top, text="Avisar al abrir si hay una versión nueva", variable=var_yt,
                      command=cambiar_yt, font=self.f_norm, progress_color=C["acento"]).pack(
            anchor="w", padx=20)
        self._boton(top, "Buscar actualización", lambda: self._revisar_ytdlp(manual=True),
                    icono("refresh", 16), height=32).pack(anchor="w", padx=20, pady=(10, 0))
        self.after(150, top.focus)

    def _abrir_archivo_carpeta(self, nombre):
        ruta = Path(self.var_carpeta.get()) / nombre
        if ruta.exists():
            abrir_ruta(ruta)
        else:
            messagebox.showinfo("Archivo", f"Todavía no existe {nombre} en la carpeta de descargas.")

    def _plegar_detalle(self):
        if self.det_cuerpo.winfo_manager():
            self.det_cuerpo.pack_forget()
            self.btn_plegar.configure(text="▸")
        else:
            self.det_cuerpo.pack(fill="x", padx=12, pady=(2, 0), after=self.det_cab)
            self.btn_plegar.configure(text="▾")

    # ---------------------------------------------------------------- acciones
    def _al_cambiar_formato(self):
        formato = self.var_formato.get()
        original = formato == "original"
        if original:
            self.var_norm.set(False)
        # En m4a el audio de YouTube (AAC) se copia tal cual, sin recodificar: la calidad
        # solo se aplica si se normaliza (entonces sí hay que recodificar).
        usa_calidad = formato in ("mp3", "opus") or (formato == "m4a" and self.var_norm.get())
        self.cb_calidad.configure(state="normal" if usa_calidad else "disabled")
        self.chk_norm.configure(state="disabled" if original else "normal")

    def _al_marcar_rg(self):
        """ReplayGain y normalizar son excluyentes: marcar una desmarca la otra."""
        if self.var_rg.get():
            self.var_norm.set(False)
        self._al_cambiar_formato()

    def _al_marcar_norm(self):
        if self.var_norm.get():
            self.var_rg.set(False)
        self._al_cambiar_formato()

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
            "tema": self.tema,
            "revisar_ytdlp": bool(self.cfg.get("revisar_ytdlp", True)),
            "detectar_portapapeles": bool(self.cfg.get("detectar_portapapeles", True)),
        }

    def _descargar(self):
        """Botón principal: descarga si es un enlace; si es texto, busca por nombre."""
        texto = self.var_url.get().strip()
        if not texto:
            messagebox.showinfo("AUDYAX", "Pega un enlace de YouTube o escribe artista y canción.")
            return
        if not texto.startswith(("http://", "https://")):
            self._buscar(texto)
            return
        if self._encolar(texto):
            self.var_url.set("")

    def _actualizar_boton_principal(self):
        texto = self.var_url.get().strip()
        if self.buscando:
            return
        if texto and not texto.startswith(("http://", "https://")):
            self.btn_descargar.configure(text="Buscar", image=self.ico_buscar)
        else:
            self.btn_descargar.configure(text="Descargar", image=self.ico_descargar)

    # ---------------------------------------------------------------- búsqueda
    def _buscar(self, texto: str):
        if self.buscando:
            return
        self.buscando = True
        self.btn_descargar.configure(text="Buscando…", state="disabled")

        def trabajo():
            try:
                self.eventos.put(("busqueda", texto, buscar(texto, 15)))
            except Exception as e:
                self.eventos.put(("busqueda_error", texto, str(e)))
        threading.Thread(target=trabajo, daemon=True).start()

    def _fin_busqueda(self):
        self.buscando = False
        self.btn_descargar.configure(state="normal")
        self._actualizar_boton_principal()

    def _mostrar_resultados(self, texto, resultados):
        self._fin_busqueda()
        if not resultados:
            messagebox.showinfo("Sin resultados", f"No encontré nada para “{texto}”.")
            return
        if self.ventana_resultados is not None and self.ventana_resultados.winfo_exists():
            self.ventana_resultados.destroy()
        self.ventana_resultados = VentanaResultados(self, texto, resultados)

    def _bajar_imagen_resultado(self, vid, url):
        try:
            self.eventos.put(("img_resultado", vid, descargar_bytes(url)))
        except Exception:
            pass

    # ---------------------------------------------------------------- portapapeles / soltar
    def _al_enfocar(self, _evento=None):
        # FocusIn llega una vez por cada widget; se agrupa en una sola revisión
        if not self._pp_programado:
            self._pp_programado = True
            self.after(200, self._revisar_portapapeles)

    def _revisar_portapapeles(self):
        self._pp_programado = False
        if not self.cfg.get("detectar_portapapeles", True):
            return
        try:
            texto = self.clipboard_get().strip()
        except tk.TclError:
            return
        ya_en_lista = any(f["url"] == texto for f in self.filas.values())
        if (not es_enlace_youtube(texto) or texto == self._ultimo_ofrecido
                or texto == self.var_url.get().strip() or ya_en_lista):
            return
        self._ultimo_ofrecido = texto
        self.aviso_pp.mostrar(f"Enlace copiado:  {corto(texto, 60)}", [
            ("Pegar", lambda: self.var_url.set(texto), False),
            ("Descargar", lambda: self._encolar(texto), True)])

    def _al_soltar(self, evento):
        enlaces = extraer_enlaces(getattr(evento, "data", "") or "")
        if not enlaces:
            messagebox.showinfo("Arrastrar y soltar", "No encontré enlaces de YouTube en lo que soltaste.")
        for url in enlaces:
            self._encolar(url)
        return getattr(evento, "action", None)

    # ---------------------------------------------------------------- yt-dlp
    def _revisar_ytdlp(self, manual: bool):
        def trabajo():
            actual = version_ytdlp()
            try:
                ultima = ultima_version_ytdlp()
            except Exception as e:
                if manual:
                    self.eventos.put(("ytdlp_sin_conexion", str(e)))
                return
            if es_mas_nueva(ultima, actual):
                self.eventos.put(("ytdlp_nueva", actual, ultima))
            elif manual:
                self.eventos.put(("ytdlp_al_dia", actual))
        threading.Thread(target=trabajo, daemon=True).start()

    def _ofrecer_actualizacion(self, actual, ultima):
        if puede_actualizarse():
            self.aviso_yt.mostrar(
                f"Hay una versión nueva de yt-dlp ({ultima}; tienes {actual}).",
                [("Más tarde", lambda: None, False), ("Actualizar", self._actualizar_ytdlp, True)])
        else:
            self.aviso_yt.mostrar(
                f"Hay una versión nueva de yt-dlp ({ultima}). Descarga la última versión de AUDYAX.",
                [("Ver versiones", lambda: webbrowser.open(REPO_URL + "/releases"), True)])

    def _actualizar_ytdlp(self):
        if self.pendientes > 0:
            messagebox.showwarning("Actualizar yt-dlp", "Espera a que terminen las descargas en curso.")
            self.aviso_yt.mostrar("yt-dlp: actualización pendiente.",
                                  [("Actualizar", self._actualizar_ytdlp, True)])
            return
        if self.actualizando:
            return
        self.actualizando = True
        self.aviso_yt.mostrar("Actualizando yt-dlp… (puede tardar un minuto)")

        def trabajo():
            ok, mensaje = actualizar_ytdlp()
            self.eventos.put(("ytdlp_actualizado", ok, mensaje))
        threading.Thread(target=trabajo, daemon=True).start()

    def _fin_actualizacion(self, ok, mensaje):
        self.actualizando = False
        if not ok:
            self.aviso_yt.ocultar()
            messagebox.showerror("No se pudo actualizar yt-dlp", mensaje)
            return
        self.aviso_yt.mostrar("yt-dlp se actualizó. Reinicia AUDYAX para usar la versión nueva.",
                              [("Reiniciar ahora", self._reiniciar, True)])

    def _reiniciar(self):
        if self.pendientes > 0 and not messagebox.askyesno(
                "Reiniciar", "Hay descargas en curso que se cancelarán. ¿Reiniciar igualmente?"):
            return
        try:
            subprocess.Popen([sys.executable, *sys.argv])
        except OSError as e:
            messagebox.showerror("No se pudo reiniciar", f"{e}\n\nCierra y abre AUDYAX a mano.")
            return
        self._cerrar()

    def _encolar(self, url: str, subcarpeta: str = "") -> bool:
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
        cfg["subcarpeta"] = subcarpeta  # p. ej. la carpeta de la playlist al reintentar
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
                "añadir las portadas ni calcular el ReplayGain.\n\n"
                "Instálalo y añádelo al PATH.")
        if not HAY_MUTAGEN:
            messagebox.showwarning(
                "Falta mutagen",
                "Para escribir el ReplayGain hace falta la librería mutagen.\n\n"
                "Instálala con:  pip install mutagen")

    # ---------------------------------------------------------------- lista
    def _fila(self, vid: str, **campos):
        nueva = vid not in self.filas
        if nueva:
            self.filas[vid] = {
                "num": len(self.filas) + 1, "color": "amarillo", "texto": "En cola", "titulo": vid,
                "artista": "", "album": "", "anio": "", "genero": "", "url": "", "ruta": "",
                "subcarpeta": "", "motivo": "", "replaygain": "", "miniatura": "", "img_pedida": False,
                "img_s": None, "img_b": None, "datos": None, "datos_pedidos": False,
                "marca": tk.BooleanVar(value=False)}
            self.filas[vid]["w"] = FilaLista(self, self.lista, vid)
        f = self.filas[vid]
        for k, v in campos.items():
            if v is not None and k in f and k not in ("w", "marca"):
                f[k] = v
        if f["miniatura"] and not f["img_pedida"]:
            f["img_pedida"] = True
            self.pool.submit(self._bajar_imagen, vid, f["miniatura"])
        f["w"].actualizar(f)
        f["w"].pintar(vid == self.sel)
        if nueva and self.sel is None:
            self._seleccionar(vid)
        elif vid == self.sel:
            self._mostrar_detalle()
        self._actualizar_contadores()

    def _bajar_imagen(self, vid, url):
        try:
            self.eventos.put(("imagen", vid, descargar_bytes(url)))
        except Exception:
            pass

    def _renumerar(self):
        for i, (vid, f) in enumerate(self.filas.items(), 1):
            f["num"] = i
            f["w"].pintar(vid == self.sel)

    def _actualizar_contadores(self):
        cuenta = {"verde": 0, "amarillo": 0, "rojo": 0}
        for f in self.filas.values():
            cuenta[f["color"]] += 1
        for nombre, (lbl, etiqueta) in self.contadores.items():
            lbl.configure(text=f"  {cuenta[nombre]} {etiqueta}")
        total = len(self.filas)
        hechas = cuenta["verde"] + cuenta["rojo"]
        pct = int(hechas * 100 / total) if total else 0
        self.progreso.set(pct / 100)
        self.lbl_pct.configure(text=f"{pct}%")

    def _procesar_eventos(self):
        try:
            for _ in range(30):  # por tandas, para que la ventana no se congele con playlists grandes
                self._manejar(self.eventos.get_nowait())
        except queue.Empty:
            pass
        self.after(30 if not self.eventos.empty() else 100, self._procesar_eventos)

    def _manejar(self, ev):
        tipo = ev[0]
        if tipo == "nuevo":
            if ev[1] in self.filas:  # reintento: se vuelven a leer los datos del archivo
                self.filas[ev[1]].update(datos=None, datos_pedidos=False)
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
        elif tipo == "imagen":
            self._poner_imagen(ev[1], ev[2])
        elif tipo == "datos":
            f = self.filas.get(ev[1])
            if f and ev[2]:
                f["datos"] = ev[2]
                if self.sel == ev[1]:
                    self._mostrar_detalle()
        elif tipo == "fin":
            self.pendientes = max(0, self.pendientes - 1)
            self._actualizar_botones()
        elif tipo == "busqueda":
            self._mostrar_resultados(ev[1], ev[2])
        elif tipo == "busqueda_error":
            self._fin_busqueda()
            messagebox.showerror("No se pudo buscar", corto(ev[2], 300))
        elif tipo == "img_resultado":
            v = self.ventana_resultados
            if v is not None and v.winfo_exists():
                v.poner_imagen(ev[1], ev[2])
        elif tipo == "ytdlp_nueva":
            self._ofrecer_actualizacion(ev[1], ev[2])
        elif tipo == "ytdlp_al_dia":
            messagebox.showinfo("yt-dlp", f"Ya tienes la última versión ({ev[1]}).")
        elif tipo == "ytdlp_sin_conexion":
            messagebox.showwarning("yt-dlp", "No se pudo consultar la última versión:\n" + corto(ev[1], 200))
        elif tipo == "ytdlp_actualizado":
            self._fin_actualizacion(ev[1], ev[2])

    def _poner_imagen(self, vid, datos):
        f = self.filas.get(vid)
        if not f:
            return
        try:
            img = Image.open(BytesIO(datos))
            img.load()
            f["img_s"] = imagen_cuadrada(img, 40, 8)
            f["img_b"] = imagen_cuadrada(img, 88, 12)
        except Exception:
            return
        f["w"].actualizar(f)
        if self.sel == vid:
            self._mostrar_detalle()

    # ---------------------------------------------------------------- selección
    def _seleccionar(self, vid):
        anterior = self.sel
        self.sel = vid
        if anterior in self.filas:
            self.filas[anterior]["w"].pintar(False)
        if vid in self.filas:
            self.filas[vid]["w"].pintar(True)
        self._mostrar_detalle()

    def _al_marcar_fila(self):
        self.var_todas.set(bool(self.filas) and all(f["marca"].get() for f in self.filas.values()))

    def _marcar_todas(self):
        valor = self.var_todas.get()
        for f in self.filas.values():
            f["marca"].set(valor)

    def _marcadas(self):
        return [vid for vid, f in self.filas.items() if f["marca"].get()]

    def _limpiar_lista(self):
        objetivo = self._marcadas() or list(self.filas)
        for vid in objetivo:
            f = self.filas[vid]
            if f["color"] == "amarillo":
                continue
            f["w"].destruir()
            del self.filas[vid]
            if self.sel == vid:
                self.sel = None
        if self.sel is None and self.filas:
            self.sel = next(iter(self.filas))
        self.var_todas.set(False)
        self._renumerar()
        self._mostrar_detalle()
        self._actualizar_contadores()

    # ---------------------------------------------------------------- detalle
    def _mostrar_detalle(self):
        vid = self.sel if self.sel in self.filas else None
        if not vid:
            self.l_det_titulo.configure(text="Selecciona una canción de la lista.")
            self.l_det_artista.configure(text="")
            self.l_chip.pack_forget()
            self.l_det_info.configure(text="")
            self.l_portada.configure(image=self.img_vacia_grande)
            for lbl in self.l_dato.values():
                lbl.configure(text="—")
            return
        f = self.filas[vid]
        self.l_det_titulo.configure(text=corto(f["titulo"], 30))
        self.l_det_artista.configure(text=corto(f["artista"] or "—", 40))
        chip = " • ".join(p for p in (f["anio"], f["genero"]) if p)
        if chip:
            self.l_chip.configure(text=f"  {limpiar_tk(chip)}  ")
            self.l_chip.pack(anchor="w", pady=4, before=self.l_det_info)
        else:
            self.l_chip.pack_forget()
        ruta = f["ruta"]
        if f["color"] == "rojo":
            self.l_det_info.configure(text=corto(f["motivo"] or f["texto"], 60), text_color=C["rojo"])
        elif f["color"] == "verde" and ruta:
            self.l_det_info.configure(text=corto(Path(ruta).name, 60), text_color=C["suave"])
        else:
            self.l_det_info.configure(text=corto(f["texto"], 60), text_color=C["suave"])
        self.l_portada.configure(image=f["img_b"] or self.img_vacia_grande)

        d = f["datos"] or {}
        rg = f["replaygain"].split(" · ")[0].replace("pista ", "") if f["replaygain"] else ""
        valores = {k: d.get(k) or "—" for k in ("formato", "duracion", "bitrate", "tamano", "muestreo")}
        valores["rg"] = rg or "—"
        for clave, texto in valores.items():
            self.l_dato[clave].configure(text=texto)
        if (f["datos"] is None and f["color"] == "verde" and ruta and not f["datos_pedidos"]
                and Path(ruta).exists()):
            f["datos_pedidos"] = True
            threading.Thread(target=self._leer_datos, args=(vid, ruta), daemon=True).start()

    def _leer_datos(self, vid, ruta):
        try:
            self.eventos.put(("datos", vid, info_archivo(ruta)))
        except Exception:
            pass

    def _enlace_de(self, vid=None):
        vid = vid or self.sel
        return self.filas[vid]["url"] if vid in self.filas else ""

    def _copiar_enlace(self):
        url = self._enlace_de()
        if url:
            self.clipboard_clear()
            self.clipboard_append(url)

    def _abrir_enlace(self, vid=None):
        url = self._enlace_de(vid)
        if url:
            webbrowser.open(url)

    def _abrir_carpeta_cancion(self):
        ruta = self.filas[self.sel]["ruta"] if self.sel in self.filas else ""
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

    def _editar_etiquetas(self):
        f = self.filas.get(self.sel)
        ruta = f["ruta"] if f else ""
        if not f or f["color"] != "verde" or not ruta or not Path(ruta).exists():
            messagebox.showinfo("Editar etiquetas", "Selecciona una canción ya descargada en esta sesión.")
            return
        if not se_puede_editar(ruta):
            messagebox.showinfo("Editar etiquetas",
                                "Solo se pueden editar archivos MP3, M4A y Opus (hace falta mutagen).")
            return
        try:
            VentanaEtiquetas(self, self.sel, ruta)
        except Exception as e:
            messagebox.showerror("No se pudo leer el archivo", str(e))

    def _etiquetas_guardadas(self, vid, datos, portada):
        f = self.filas.get(vid)
        if not f:
            return
        f.update(datos=None, datos_pedidos=False)  # el tamaño del archivo cambia
        self._fila(vid, **datos)
        if portada:
            self._poner_imagen(vid, portada)

    def _reintentar(self):
        candidatas = self._marcadas() or ([self.sel] if self.sel in self.filas else [])
        validas = [v for v in candidatas if self.filas[v]["color"] == "rojo" and self.filas[v]["url"]]
        if not validas:
            messagebox.showinfo("Reintentar", "Solo se pueden reintentar canciones con error que tengan enlace.")
            return
        for vid in validas:
            # Se reintenta en la misma subcarpeta (la de su playlist, si venía de una)
            self._encolar(self.filas[vid]["url"], self.filas[vid]["subcarpeta"])

    # ---------------------------------------------------------------- cierre
    def _cerrar(self):
        guardar_config(self._cfg_actual())
        self.cancelar.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()