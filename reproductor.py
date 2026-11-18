"""
Mini reproductor de AUDYAX.

Archivo aparte a propósito: es la base de la "parte 2" del proyecto (un reproductor de
música completo). Tiene dos piezas independientes:

  MotorAudio        reproduce cualquier formato que entienda FFmpeg (MP3, M4A, Opus…):
                    FFmpeg decodifica a audio "crudo" (PCM) y sounddevice lo manda a los
                    parlantes. No sabe nada de la interfaz.
  BarraReproductor  la barra que se muestra en la ventana (customtkinter): portada, título,
                    línea de la letra sincronizada, anterior / reproducir-pausar / siguiente,
                    barra de progreso para adelantar, volumen.

    pip install sounddevice numpy      (en Linux además: sudo apt install libportaudio2)
"""
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

TASA = 44100          # muestras por segundo
CANALES = 2
BYTES_POR_CUADRO = 2 * CANALES   # int16 estéreo
CUADROS_POR_BLOQUE = 2048
MAX_BUFFER = TASA * BYTES_POR_CUADRO * 2   # ~2 s de audio decodificado por adelantado


class Detener(Exception):
    """La lanza el motor dentro del callback cuando se acabó la canción."""


def audio_disponible() -> tuple:
    """(True, "") si se puede reproducir; si no, (False, motivo para mostrar)."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        return False, "falta la librería numpy (pip install numpy)"
    try:
        import sounddevice as sd
    except ImportError:
        return False, "falta la librería sounddevice (pip install sounddevice)"
    except OSError as e:  # Linux sin PortAudio
        return False, f"falta PortAudio ({e}). En Linux: sudo apt install libportaudio2"
    try:
        if sd.default.device[1] is None or sd.default.device[1] < 0:
            sd.query_devices(kind="output")
    except Exception as e:
        return False, f"no se encontró una salida de audio ({e})"
    if not shutil.which("ffmpeg"):
        return False, "no se encontró FFmpeg"
    return True, ""


def duracion_audio(ruta) -> float:
    try:
        from mutagen import File
        audio = File(ruta)
        if audio is not None and audio.info is not None:
            return float(audio.info.length)
    except Exception:
        pass
    return 0.0


class SalidaSounddevice:
    """Salida real a los parlantes."""

    def __init__(self, callback, al_terminar):
        import sounddevice as sd

        def cb(outdata, frames, _tiempo, _estado):
            try:
                callback(outdata, frames)
            except Detener:
                raise sd.CallbackStop

        # latency=0.1: sin esto, algunos sistemas (p. ej. PulseAudio) piden el audio en bloques
        # de ~1 s y la barra de progreso avanza a saltos; 0,1 s sigue siendo seguro en equipos lentos
        self.stream = sd.RawOutputStream(samplerate=TASA, channels=CANALES, dtype="int16",
                                         blocksize=CUADROS_POR_BLOQUE, callback=cb, latency=0.1,
                                         finished_callback=al_terminar)

    def iniciar(self):
        self.stream.start()

    def cerrar(self):
        try:
            self.stream.abort(ignore_errors=True)
            self.stream.close(ignore_errors=True)
        except Exception:
            pass


class MotorAudio:
    """Reproduce un archivo. Métodos: reproducir(ruta, desde), pausar(), reanudar(),
    alternar(), buscar(seg), detener(); propiedades: posicion, duracion, volumen, estado.

    al_terminar() se llama (desde el hilo de audio) cuando la canción termina sola."""

    def __init__(self, al_terminar=None, fabrica_salida=SalidaSounddevice):
        self.al_terminar = al_terminar
        self._fabrica = fabrica_salida
        self._volumen = 1.0
        self.ruta, self.duracion = None, 0.0
        self.estado = "detenido"           # "reproduciendo" | "pausado" | "detenido"
        self._inicio, self._cuadros = 0.0, 0
        self._salida, self._proceso = None, None
        self._buffer = bytearray()
        self._cond = threading.Condition()
        self._fin_lectura = False
        self._generacion = 0              # cada arranque invalida los hilos del anterior
        self._en_pausa = False

    # ---------------------------------------------------------------- control
    def reproducir(self, ruta, desde: float = 0.0):
        self.detener()
        self.ruta = str(ruta)
        self.duracion = duracion_audio(ruta)
        self._en_pausa = False
        self._arrancar(desde)

    def pausar(self):
        if self.estado == "reproduciendo":
            self._en_pausa, self.estado = True, "pausado"

    def reanudar(self):
        if self.estado == "pausado":
            self._en_pausa, self.estado = False, "reproduciendo"
        elif self.estado == "detenido" and self.ruta:
            self.reproducir(self.ruta)

    def alternar(self):
        self.pausar() if self.estado == "reproduciendo" else self.reanudar()

    def buscar(self, segundos: float):
        """Salta a otra parte de la canción (se vuelve a decodificar desde ahí)."""
        if not self.ruta:
            return
        segundos = max(0.0, min(segundos, max(0.0, self.duracion - 0.5) if self.duracion else segundos))
        pausado = self._en_pausa
        self._parar_todo()
        self._en_pausa = pausado
        self._arrancar(segundos)
        if pausado:
            self.estado = "pausado"

    def detener(self):
        self._parar_todo()
        self.estado = "detenido"

    @property
    def posicion(self) -> float:
        return self._inicio + self._cuadros / TASA

    @property
    def volumen(self) -> float:
        return self._volumen

    @volumen.setter
    def volumen(self, valor: float):
        self._volumen = max(0.0, min(1.0, float(valor)))

    # ---------------------------------------------------------------- interno
    def _arrancar(self, desde: float):
        self._generacion += 1
        gen = self._generacion
        self._inicio, self._cuadros = float(desde), 0
        with self._cond:
            self._buffer.clear()
            self._fin_lectura = False
        extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
        self._proceso = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{desde:.3f}", "-i", self.ruta,
             "-vn", "-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(CANALES), "-ar", str(TASA), "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **extra)
        threading.Thread(target=self._leer, args=(gen, self._proceso), daemon=True).start()
        # Esperar a tener algo decodificado antes de abrir el audio (máx. 1 s): si no, los
        # primeros bloques que pide la tarjeta de sonido salen en silencio
        with self._cond:
            self._cond.wait_for(lambda: len(self._buffer) >= TASA * BYTES_POR_CUADRO // 5
                                or self._fin_lectura or gen != self._generacion, timeout=1.0)
        self._salida = self._fabrica(lambda out, n: self._rellenar(gen, out, n),
                                     lambda: self._terminado(gen))
        self.estado = "reproduciendo"
        self._salida.iniciar()

    def _leer(self, gen, proceso):
        """Hilo: lee el audio decodificado de FFmpeg hasta tener ~2 s por adelantado."""
        try:
            while True:
                trozo = proceso.stdout.read(16384)
                with self._cond:
                    if gen != self._generacion:
                        return
                    if not trozo:
                        self._fin_lectura = True
                        self._cond.notify_all()
                        return
                    self._buffer.extend(trozo)
                    self._cond.notify_all()
                    while len(self._buffer) > MAX_BUFFER and gen == self._generacion:
                        self._cond.wait(0.5)
        except (OSError, ValueError):
            with self._cond:
                if gen == self._generacion:
                    self._fin_lectura = True

    def _rellenar(self, gen, salida, cuadros: int):
        """Callback de audio: entrega `cuadros` de audio (o silencio si está en pausa)."""
        necesarios = cuadros * BYTES_POR_CUADRO
        if gen != self._generacion:
            raise Detener
        if self._en_pausa:
            salida[:] = bytes(necesarios)
            return
        with self._cond:
            datos = bytes(self._buffer[:necesarios])
            del self._buffer[:necesarios]
            self._cond.notify()
            acabado = self._fin_lectura and not self._buffer
        reales = len(datos) // BYTES_POR_CUADRO
        if len(datos) < necesarios:
            datos += bytes(necesarios - len(datos))
        if self._volumen < 0.999:
            import numpy as np
            muestras = np.frombuffer(datos, dtype=np.int16).astype(np.float32) * self._volumen
            datos = muestras.astype(np.int16).tobytes()
        salida[:] = datos
        self._cuadros += reales
        if acabado and reales < cuadros:
            raise Detener

    def _terminado(self, gen):
        if gen != self._generacion:
            return  # se cerró a propósito (detener / buscar): no es "fin de la canción"
        self.estado = "detenido"
        if self.al_terminar:
            self.al_terminar()

    def _parar_todo(self):
        self._generacion += 1
        salida, proceso = self._salida, self._proceso
        self._salida = self._proceso = None
        with self._cond:
            self._buffer.clear()
            self._cond.notify_all()
        if salida is not None:
            salida.cerrar()
        if proceso is not None:
            try:
                proceso.kill()
                proceso.wait(timeout=2)
            except Exception:
                pass


# ==============================================================================
# INTERFAZ: barra del mini reproductor
# ==============================================================================
try:
    import customtkinter as ctk
    from PIL import Image, ImageDraw
except ImportError:  # el motor se puede usar sin interfaz
    ctk = None


def _formato_tiempo(segundos: float) -> str:
    segundos = max(0, int(segundos))
    return f"{segundos // 60}:{segundos % 60:02d}"


def _dibujar(nombre: str, color: str, n: int = 64) -> "Image.Image":
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def p(x, y):
        return (x * n, y * n)
    if nombre == "play":
        d.polygon([p(.28, .16), p(.84, .5), p(.28, .84)], fill=color)
    elif nombre == "pausa":
        d.rounded_rectangle([p(.24, .16), p(.42, .84)], radius=n * .05, fill=color)
        d.rounded_rectangle([p(.58, .16), p(.76, .84)], radius=n * .05, fill=color)
    elif nombre in ("anterior", "siguiente"):
        if nombre == "siguiente":
            d.polygon([p(.18, .2), p(.66, .5), p(.18, .8)], fill=color)
            d.rectangle([p(.68, .2), p(.8, .8)], fill=color)
        else:
            d.polygon([p(.82, .2), p(.34, .5), p(.82, .8)], fill=color)
            d.rectangle([p(.2, .2), p(.32, .8)], fill=color)
    elif nombre == "volumen":
        d.polygon([p(.12, .38), p(.32, .38), p(.54, .18), p(.54, .82), p(.32, .62), p(.12, .62)], fill=color)
        d.arc([p(.46, .3), p(.76, .7)], -55, 55, fill=color, width=max(2, n // 14))
        d.arc([p(.46, .16), p(.92, .84)], -55, 55, fill=color, width=max(2, n // 14))
    elif nombre == "cerrar":
        w = max(2, n // 10)
        d.line([p(.25, .25), p(.75, .75)], fill=color, width=w)
        d.line([p(.75, .25), p(.25, .75)], fill=color, width=w)
    return im


def _icono(nombre: str, colores, tam: int):
    """Icono con versión clara y oscura (colores = (claro, oscuro))."""
    claro, oscuro = colores if isinstance(colores, (tuple, list)) else (colores, colores)
    return ctk.CTkImage(light_image=_dibujar(nombre, claro).resize((tam * 2, tam * 2), Image.LANCZOS),
                        dark_image=_dibujar(nombre, oscuro).resize((tam * 2, tam * 2), Image.LANCZOS),
                        size=(tam, tam))


class BarraReproductor(ctk.CTkFrame if ctk else object):
    """Barra del mini reproductor. La ventana le pasa:
      colores: dict con "card", "texto", "suave", "acento", "acento_hover", "sobre_acento", "hover"
               (cada uno (claro, oscuro), como usa customtkinter);
      fuentes: dict con "neg", "norm", "peq" (CTkFont);
      vecina(item, paso) -> el item siguiente (paso=1) o anterior (paso=-1) de la lista, o None;
      al_cambiar_volumen(v) -> para recordar el volumen;
      al_cerrar() -> la ventana esconde la barra.
    Un "item" es un dict con: id, ruta, titulo, artista y, opcional, imagen (PIL.Image)."""

    def __init__(self, master, colores, fuentes, vecina, al_cambiar_volumen=None, al_cerrar=None,
                 volumen=0.8, motor=None):
        super().__init__(master, fg_color=colores["card"], corner_radius=14, border_width=1,
                         border_color=colores.get("borde", colores["card"]))
        self.colores, self.fuentes = colores, fuentes
        self.vecina, self.al_cambiar_volumen, self.al_cerrar = vecina, al_cambiar_volumen, al_cerrar
        self._fin_pendiente = threading.Event()
        self.motor = motor or MotorAudio(al_terminar=self._fin_pendiente.set)
        self.motor.volumen = volumen
        self.item = None
        self._lineas = []
        self._arrastrando = False
        self._construir()
        self.after(250, self._refrescar)

    # ---------------------------------------------------------------- construcción
    def _boton(self, padre, nombre, comando, tam=18, acento=False):
        c = self.colores
        return ctk.CTkButton(padre, text="", width=tam + 18, height=tam + 14, corner_radius=(tam + 14) // 2,
                             image=_icono(nombre, c["sobre_acento"] if acento else c["texto"], tam),
                             fg_color=c["acento"] if acento else "transparent",
                             hover_color=c["acento_hover"] if acento else c["hover"], command=comando)

    def _construir(self):
        c, f = self.colores, self.fuentes
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(3, weight=2)
        self.l_portada = ctk.CTkLabel(self, text="", width=48, height=48, fg_color=c["hover"], corner_radius=8)
        self.l_portada.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=10)
        self.l_titulo = ctk.CTkLabel(self, text="", font=f["neg"], anchor="w")
        self.l_titulo.grid(row=0, column=1, sticky="sw", pady=(10, 0))
        self.l_sub = ctk.CTkLabel(self, text="", font=f["peq"], text_color=c["suave"], anchor="w")
        self.l_sub.grid(row=1, column=1, sticky="nw", pady=(0, 10))

        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.grid(row=0, column=2, rowspan=2, padx=10)
        self._boton(botones, "anterior", lambda: self.saltar(-1), 14).pack(side="left", padx=2)
        self.b_play = self._boton(botones, "pausa", self.alternar, 18, acento=True)
        self.b_play.pack(side="left", padx=4)
        self._boton(botones, "siguiente", lambda: self.saltar(1), 14).pack(side="left", padx=2)
        self.ico_play = _icono("play", c["sobre_acento"], 18)
        self.ico_pausa = _icono("pausa", c["sobre_acento"], 18)

        progreso = ctk.CTkFrame(self, fg_color="transparent")
        progreso.grid(row=0, column=3, rowspan=2, sticky="ew", padx=6)
        progreso.grid_columnconfigure(1, weight=1)
        self.l_tiempo = ctk.CTkLabel(progreso, text="0:00", width=40, font=f["peq"], text_color=c["suave"])
        self.l_tiempo.grid(row=0, column=0)
        self.barra = ctk.CTkSlider(progreso, from_=0, to=1, number_of_steps=None, height=16,
                                   progress_color=c["acento"], button_color=c["acento"],
                                   button_hover_color=c["acento_hover"])
        self.barra.set(0)
        self.barra.grid(row=0, column=1, sticky="ew", padx=6)
        self.barra.bind("<Button-1>", lambda e: setattr(self, "_arrastrando", True), add=True)
        self.barra.bind("<ButtonRelease-1>", self._soltar_barra, add=True)
        self.l_total = ctk.CTkLabel(progreso, text="0:00", width=40, font=f["peq"], text_color=c["suave"])
        self.l_total.grid(row=0, column=2)

        vol = ctk.CTkFrame(self, fg_color="transparent")
        vol.grid(row=0, column=4, rowspan=2, padx=6)
        ctk.CTkLabel(vol, text="", image=_icono("volumen", c["suave"], 16)).pack(side="left", padx=(0, 4))
        self.s_volumen = ctk.CTkSlider(vol, from_=0, to=1, width=90, height=16, command=self._cambiar_volumen,
                                       progress_color=c["acento"], button_color=c["acento"],
                                       button_hover_color=c["acento_hover"])
        self.s_volumen.set(self.motor.volumen)
        self.s_volumen.pack(side="left")
        self._boton(self, "cerrar", self.cerrar, 12).grid(row=0, column=5, rowspan=2, padx=(4, 10))

    # ---------------------------------------------------------------- acciones
    def reproducir(self, item: dict):
        """Empieza a sonar `item`. Lanza RuntimeError con el motivo si no se puede."""
        disponible, motivo = audio_disponible() if self.motor._fabrica is SalidaSounddevice else (True, "")
        if not disponible:
            raise RuntimeError(motivo)
        if not item.get("ruta") or not Path(item["ruta"]).exists():
            raise RuntimeError("el archivo ya no está en su carpeta")
        self.item = item
        self._fin_pendiente.clear()
        self.motor.reproducir(item["ruta"])
        self.l_titulo.configure(text=_corto(item.get("titulo") or Path(item["ruta"]).stem, 42))
        self._artista = _corto(item.get("artista") or "", 42)
        self.l_sub.configure(text=self._artista, text_color=self.colores["suave"])
        self.l_total.configure(text=_formato_tiempo(self.motor.duracion))
        self.barra.configure(to=max(1.0, self.motor.duracion))
        self._poner_portada(item.get("imagen"))
        self._lineas = []
        try:
            import letras
            self._lineas = letras.parsear_lrc(letras.leer_letra(item["ruta"]))
        except Exception:
            pass
        self.b_play.configure(image=self.ico_pausa)

    def alternar(self):
        if not self.item:
            return
        self.motor.alternar()
        self.b_play.configure(image=self.ico_pausa if self.motor.estado == "reproduciendo" else self.ico_play)

    def saltar(self, paso: int):
        """Anterior (-1) o siguiente (1). "Anterior" pasados 3 s vuelve al inicio de la canción."""
        if not self.item:
            return
        if paso < 0 and self.motor.posicion > 3:
            self.motor.buscar(0)
            return
        otro = self.vecina(self.item, paso)
        if otro is None:
            if paso > 0:
                self.motor.detener()
                self.b_play.configure(image=self.ico_play)
            return
        try:
            self.reproducir(otro)
        except RuntimeError:
            self.item = otro
            self.saltar(paso)  # si ese archivo ya no existe, se pasa al siguiente

    def cerrar(self):
        self.motor.detener()
        self.item = None
        if self.al_cerrar:
            self.al_cerrar()

    # ---------------------------------------------------------------- refresco
    def _poner_portada(self, imagen):
        if imagen is None:
            self.l_portada.configure(image=None)
            return
        try:
            img = imagen.convert("RGB").resize((96, 96), Image.LANCZOS)
            self._img_portada = ctk.CTkImage(light_image=img, dark_image=img, size=(48, 48))
            self.l_portada.configure(image=self._img_portada)
        except Exception:
            pass

    def _cambiar_volumen(self, valor):
        self.motor.volumen = float(valor)
        if self.al_cambiar_volumen:
            self.al_cambiar_volumen(float(valor))

    def _soltar_barra(self, _evento=None):
        self._arrastrando = False
        if self.item:
            self.motor.buscar(float(self.barra.get()))

    def _refrescar(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self._fin_pendiente.is_set():   # la canción terminó sola: la siguiente
            self._fin_pendiente.clear()
            self.saltar(1)
        if self.item:
            pos = self.motor.posicion
            if not self._arrastrando:
                self.barra.set(min(pos, self.motor.duracion or pos))
            self.l_tiempo.configure(text=_formato_tiempo(self.barra.get() if self._arrastrando else pos))
            if self._lineas:
                import letras
                i = letras.linea_actual(self._lineas, pos)
                texto = self._lineas[i][1] if i >= 0 and self._lineas[i][1] else "♪"
                self.l_sub.configure(text=_corto(texto, 60), text_color=self.colores["acento"])
        self.after(250, self._refrescar)

    def destroy(self):
        self.motor.detener()
        super().destroy()


def _corto(texto: str, n: int) -> str:
    texto = "".join(c if ord(c) <= 0xFFFF else "□" for c in str(texto or ""))  # Tk no muestra emojis
    return texto if len(texto) <= n else texto[: n - 1] + "…"
