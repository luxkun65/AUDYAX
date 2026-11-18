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

SAMPLE_RATE = 44100          # muestras por segundo
CHANNELS = 2
BYTES_PER_FRAME = 2 * CHANNELS   # int16 estéreo
FRAMES_PER_BLOCK = 2048
MAX_BUFFER = SAMPLE_RATE * BYTES_PER_FRAME * 2   # ~2 s de audio decodificado por adelantado


class StopPlayback(Exception):
    """La lanza el motor dentro del callback cuando se acabó la canción."""


def audio_available() -> tuple:
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
    except Exception as e:  # PortAudioError: PortAudio no arranca (p. ej. sin servidor de sonido)
        return False, f"no se pudo iniciar el audio ({e})"
    try:
        if sd.default.device[1] is None or sd.default.device[1] < 0:
            sd.query_devices(kind="output")
    except Exception as e:
        return False, f"no se encontró una salida de audio ({e})"
    if not shutil.which("ffmpeg"):
        return False, "no se encontró FFmpeg"
    return True, ""


def audio_duration(path) -> float:
    try:
        from mutagen import File
        audio = File(path)
        if audio is not None and audio.info is not None:
            return float(audio.info.length)
    except Exception:
        pass
    return 0.0


class SounddeviceOutput:
    """Salida real a los parlantes."""

    def __init__(self, callback, on_finish):
        import sounddevice as sd

        def cb(outdata, frames, _time, _state):
            try:
                callback(outdata, frames)
            except StopPlayback:
                raise sd.CallbackStop

        # latency=0.1: sin esto, algunos sistemas (p. ej. PulseAudio) piden el audio en bloques
        # de ~1 s y la barra de progreso avanza a saltos; 0,1 s sigue siendo seguro en equipos lentos
        self.stream = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16",
                                         blocksize=FRAMES_PER_BLOCK, callback=cb, latency=0.1,
                                         finished_callback=on_finish)

    def start(self):
        self.stream.start()

    def close(self):
        try:
            self.stream.abort(ignore_errors=True)
            self.stream.close(ignore_errors=True)
        except Exception:
            pass


class AudioEngine:
    """Reproduce un archivo. Métodos: reproducir(ruta, desde), pausar(), reanudar(),
    alternar(), buscar(seg), detener(); propiedades: posicion, duracion, volumen, estado.

    al_terminar() se llama (desde el hilo de audio) cuando la canción termina sola."""

    def __init__(self, on_finish=None, output_factory=SounddeviceOutput):
        self.on_finish = on_finish
        self._factory = output_factory
        self._volume = 1.0
        self.path, self.duration = None, 0.0
        self.status = "stopped"           # "reproduciendo" | "pausado" | "detenido"
        self._offset, self._frames = 0.0, 0
        self._output, self._process = None, None
        self._buffer = bytearray()
        self._cond = threading.Condition()
        self._end_of_stream = False
        self._generation = 0              # cada arranque invalida los hilos del anterior
        self._paused = False

    # ---------------------------------------------------------------- control
    def play(self, path, start_at: float = 0.0):
        self.stop()
        self.path = str(path)
        self.duration = audio_duration(path)
        self._paused = False
        self._start(start_at)

    def pause_playback(self):
        if self.status == "playing":
            self._paused, self.status = True, "paused"

    def resume(self):
        if self.status == "paused":
            self._paused, self.status = False, "playing"
        elif self.status == "stopped" and self.path:
            self.play(self.path)

    def toggle(self):
        self.pause_playback() if self.status == "playing" else self.resume()

    def search(self, seconds: float):
        """Salta a otra parte de la canción (se vuelve a decodificar desde ahí)."""
        if not self.path:
            return
        seconds = max(0.0, min(seconds, max(0.0, self.duration - 0.5) if self.duration else seconds))
        paused = self._paused
        self._stop_all()
        self._paused = paused
        self._start(seconds)
        if paused:
            self.status = "paused"

    def stop(self):
        self._stop_all()
        self.status = "stopped"

    @property
    def position(self) -> float:
        return self._offset + self._frames / SAMPLE_RATE

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = max(0.0, min(1.0, float(value)))

    # ---------------------------------------------------------------- interno
    def _start(self, start_at: float):
        self._generation += 1
        gen = self._generation
        self._offset, self._frames = float(start_at), 0
        with self._cond:
            self._buffer.clear()
            self._end_of_stream = False
        extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
        self._process = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{start_at:.3f}", "-i", self.path,
             "-vn", "-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **extra)
        threading.Thread(target=self._read, args=(gen, self._process), daemon=True).start()
        # Esperar a tener algo decodificado antes de abrir el audio (máx. 1 s): si no, los
        # primeros bloques que pide la tarjeta de sonido salen en silencio
        with self._cond:
            self._cond.wait_for(lambda: len(self._buffer) >= SAMPLE_RATE * BYTES_PER_FRAME // 5
                                or self._end_of_stream or gen != self._generation, timeout=1.0)
        self._output = self._factory(lambda out, n: self._refill(gen, out, n),
                                     lambda: self._finished(gen))
        self.status = "playing"
        self._output.start()

    def _read(self, gen, process):
        """Hilo: lee el audio decodificado de FFmpeg hasta tener ~2 s por adelantado."""
        try:
            while True:
                chunk = process.stdout.read(16384)
                with self._cond:
                    if gen != self._generation:
                        return
                    if not chunk:
                        self._end_of_stream = True
                        self._cond.notify_all()
                        return
                    self._buffer.extend(chunk)
                    self._cond.notify_all()
                    while len(self._buffer) > MAX_BUFFER and gen == self._generation:
                        self._cond.wait(0.5)
        except (OSError, ValueError):
            with self._cond:
                if gen == self._generation:
                    self._end_of_stream = True

    def _refill(self, gen, output, frames: int):
        """Callback de audio: entrega `cuadros` de audio (o silencio si está en pausa)."""
        required = frames * BYTES_PER_FRAME
        if gen != self._generation:
            raise StopPlayback
        if self._paused:
            output[:] = bytes(required)
            return
        with self._cond:
            data = bytes(self._buffer[:required])
            del self._buffer[:required]
            self._cond.notify()
            exhausted = self._end_of_stream and not self._buffer
        got = len(data) // BYTES_PER_FRAME
        if len(data) < required:
            data += bytes(required - len(data))
        if self._volume < 0.999:
            import numpy as np
            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) * self._volume
            data = samples.astype(np.int16).tobytes()
        output[:] = data
        self._frames += got
        if exhausted and got < frames:
            raise StopPlayback

    def _finished(self, gen):
        if gen != self._generation:
            return  # se cerró a propósito (detener / buscar): no es "fin de la canción"
        self.status = "stopped"
        if self.on_finish:
            self.on_finish()

    def _stop_all(self):
        self._generation += 1
        output, process = self._output, self._process
        self._output = self._process = None
        with self._cond:
            self._buffer.clear()
            self._cond.notify_all()
        if output is not None:
            output.close()
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=2)
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


def _format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _draw(name: str, color: str, n: int = 64) -> "Image.Image":
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def p(x, y):
        return (x * n, y * n)
    if name == "play":
        d.polygon([p(.28, .16), p(.84, .5), p(.28, .84)], fill=color)
    elif name == "pause":
        d.rounded_rectangle([p(.24, .16), p(.42, .84)], radius=n * .05, fill=color)
        d.rounded_rectangle([p(.58, .16), p(.76, .84)], radius=n * .05, fill=color)
    elif name in ("previous", "next"):
        if name == "next":
            d.polygon([p(.18, .2), p(.66, .5), p(.18, .8)], fill=color)
            d.rectangle([p(.68, .2), p(.8, .8)], fill=color)
        else:
            d.polygon([p(.82, .2), p(.34, .5), p(.82, .8)], fill=color)
            d.rectangle([p(.2, .2), p(.32, .8)], fill=color)
    elif name == "volume":
        d.polygon([p(.12, .38), p(.32, .38), p(.54, .18), p(.54, .82), p(.32, .62), p(.12, .62)], fill=color)
        d.arc([p(.46, .3), p(.76, .7)], -55, 55, fill=color, width=max(2, n // 14))
        d.arc([p(.46, .16), p(.92, .84)], -55, 55, fill=color, width=max(2, n // 14))
    elif name == "close":
        w = max(2, n // 10)
        d.line([p(.25, .25), p(.75, .75)], fill=color, width=w)
        d.line([p(.75, .25), p(.25, .75)], fill=color, width=w)
    return im


def _icon(name: str, colors, size: int):
    """Icono con versión clara y oscura (colores = (claro, oscuro))."""
    light, dark = colors if isinstance(colors, (tuple, list)) else (colors, colors)
    return ctk.CTkImage(light_image=_draw(name, light).resize((size * 2, size * 2), Image.LANCZOS),
                        dark_image=_draw(name, dark).resize((size * 2, size * 2), Image.LANCZOS),
                        size=(size, size))


class PlayerBar(ctk.CTkFrame if ctk else object):
    """Barra del mini reproductor. La ventana le pasa:
      colores: dict con "card", "texto", "suave", "acento", "acento_hover", "sobre_acento", "hover"
               (cada uno (claro, oscuro), como usa customtkinter);
      fuentes: dict con "neg", "norm", "peq" (CTkFont);
      vecina(item, paso) -> el item siguiente (paso=1) o anterior (paso=-1) de la lista, o None;
      al_cambiar_volumen(v) -> para recordar el volumen;
      al_cerrar() -> la ventana esconde la barra.
    Un "item" es un dict con: id, ruta, titulo, artista y, opcional, imagen (PIL.Image)."""

    def __init__(self, master, colors, fonts, neighbor, on_volume_change=None, on_close=None,
                 volume=0.8, engine=None):
        super().__init__(master, fg_color=colors["card"], corner_radius=14, border_width=1,
                         border_color=colors.get("border", colors["card"]))
        self.colors, self.fonts = colors, fonts
        self.neighbor, self.on_volume_change, self.on_close = neighbor, on_volume_change, on_close
        self._end_pending = threading.Event()
        self.engine = engine or AudioEngine(on_finish=self._end_pending.set)
        self.engine.volume = volume
        self.item = None
        self._lines = []
        self._dragging = False
        self._build()
        self.after(250, self._refresh)

    # ---------------------------------------------------------------- construcción
    def _button(self, parent, name, command, size=18, accent=False):
        c = self.colors
        return ctk.CTkButton(parent, text="", width=size + 18, height=size + 14, corner_radius=(size + 14) // 2,
                             image=_icon(name, c["on_accent"] if accent else c["text"], size),
                             fg_color=c["accent"] if accent else "transparent",
                             hover_color=c["accent_hover"] if accent else c["hover"], command=command)

    def _build(self):
        c, f = self.colors, self.fonts
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(3, weight=2)
        self.l_cover = ctk.CTkLabel(self, text="", width=48, height=48, fg_color=c["hover"], corner_radius=8)
        self.l_cover.grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=10)
        self.l_title = ctk.CTkLabel(self, text="", font=f["bold"], anchor="w")
        self.l_title.grid(row=0, column=1, sticky="sw", pady=(10, 0))
        self.l_sub = ctk.CTkLabel(self, text="", font=f["small"], text_color=c["muted"], anchor="w")
        self.l_sub.grid(row=1, column=1, sticky="nw", pady=(0, 10))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=0, column=2, rowspan=2, padx=10)
        self._button(buttons, "previous", lambda: self.jump(-1), 14).pack(side="left", padx=2)
        self.btn_play = self._button(buttons, "pause", self.toggle, 18, accent=True)
        self.btn_play.pack(side="left", padx=4)
        self._button(buttons, "next", lambda: self.jump(1), 14).pack(side="left", padx=2)
        self.ico_play = _icon("play", c["on_accent"], 18)
        self.ico_pause = _icon("pause", c["on_accent"], 18)

        progress = ctk.CTkFrame(self, fg_color="transparent")
        progress.grid(row=0, column=3, rowspan=2, sticky="ew", padx=6)
        progress.grid_columnconfigure(1, weight=1)
        self.l_time = ctk.CTkLabel(progress, text="0:00", width=40, font=f["small"], text_color=c["muted"])
        self.l_time.grid(row=0, column=0)
        self.bar = ctk.CTkSlider(progress, from_=0, to=1, number_of_steps=None, height=16,
                                   progress_color=c["accent"], button_color=c["accent"],
                                   button_hover_color=c["accent_hover"])
        self.bar.set(0)
        self.bar.grid(row=0, column=1, sticky="ew", padx=6)
        self.bar.bind("<Button-1>", lambda e: setattr(self, "_dragging", True), add=True)
        self.bar.bind("<ButtonRelease-1>", self._release_bar, add=True)
        self.l_total = ctk.CTkLabel(progress, text="0:00", width=40, font=f["small"], text_color=c["muted"])
        self.l_total.grid(row=0, column=2)

        vol = ctk.CTkFrame(self, fg_color="transparent")
        vol.grid(row=0, column=4, rowspan=2, padx=6)
        ctk.CTkLabel(vol, text="", image=_icon("volume", c["muted"], 16)).pack(side="left", padx=(0, 4))
        self.s_volume = ctk.CTkSlider(vol, from_=0, to=1, width=90, height=16, command=self._change_volume,
                                       progress_color=c["accent"], button_color=c["accent"],
                                       button_hover_color=c["accent_hover"])
        self.s_volume.set(self.engine.volume)
        self.s_volume.pack(side="left")
        self._button(self, "close", self.close, 12).grid(row=0, column=5, rowspan=2, padx=(4, 10))

    # ---------------------------------------------------------------- acciones
    def play(self, item: dict):
        """Empieza a sonar `item`. Lanza RuntimeError con el motivo si no se puede."""
        available, reason = audio_available() if self.engine._factory is SounddeviceOutput else (True, "")
        if not available:
            raise RuntimeError(reason)
        if not item.get("path") or not Path(item["path"]).exists():
            raise RuntimeError("el archivo ya no está en su carpeta")
        self.item = item
        self._end_pending.clear()
        self.engine.play(item["path"])
        self.l_title.configure(text=_short(item.get("title") or Path(item["path"]).stem, 42))
        self._artist = _short(item.get("artist") or "", 42)
        self.l_sub.configure(text=self._artist, text_color=self.colors["muted"])
        self.l_total.configure(text=_format_time(self.engine.duration))
        self.bar.configure(to=max(1.0, self.engine.duration))
        self._set_cover(item.get("image"))
        self._lines = []
        try:
            import lyrics
            self._lines = lyrics.parse_lrc(lyrics.read_lyrics(item["path"]))
        except Exception:
            pass
        self.btn_play.configure(image=self.ico_pause)

    def toggle(self):
        if not self.item:
            return
        self.engine.toggle()
        self.btn_play.configure(image=self.ico_pause if self.engine.status == "playing" else self.ico_play)

    def jump(self, step: int):
        """Anterior (-1) o siguiente (1). "Anterior" pasados 3 s vuelve al inicio de la canción."""
        if not self.item:
            return
        if step < 0 and self.engine.position > 3:
            self.engine.search(0)
            return
        another = self.neighbor(self.item, step)
        if another is None:
            if step > 0:
                self.engine.stop()
                self.btn_play.configure(image=self.ico_play)
            return
        try:
            self.play(another)
        except RuntimeError:
            self.item = another
            self.jump(step)  # si ese archivo ya no existe, se pasa al siguiente

    def close(self):
        self.engine.stop()
        self.item = None
        if self.on_close:
            self.on_close()

    # ---------------------------------------------------------------- refresco
    def _set_cover(self, image):
        if image is None:
            self.l_cover.configure(image=None)
            return
        try:
            img = image.convert("RGB").resize((96, 96), Image.LANCZOS)
            self._cover_img = ctk.CTkImage(light_image=img, dark_image=img, size=(48, 48))
            self.l_cover.configure(image=self._cover_img)
        except Exception:
            pass

    def _change_volume(self, value):
        self.engine.volume = float(value)
        if self.on_volume_change:
            self.on_volume_change(float(value))

    def _release_bar(self, _event=None):
        self._dragging = False
        if self.item:
            self.engine.search(float(self.bar.get()))

    def _refresh(self):
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self._end_pending.is_set():   # la canción terminó sola: la siguiente
            self._end_pending.clear()
            self.jump(1)
        if self.item:
            pos = self.engine.position
            if not self._dragging:
                self.bar.set(min(pos, self.engine.duration or pos))
            self.l_time.configure(text=_format_time(self.bar.get() if self._dragging else pos))
            if self._lines:
                import lyrics
                i = lyrics.current_line(self._lines, pos)
                text = self._lines[i][1] if i >= 0 and self._lines[i][1] else "♪"
                self.l_sub.configure(text=_short(text, 60), text_color=self.colors["accent"])
        self.after(250, self._refresh)

    def destroy(self):
        self.engine.stop()
        super().destroy()


def _short(text: str, n: int) -> str:
    text = "".join(c if ord(c) <= 0xFFFF else "□" for c in str(text or ""))  # Tk no muestra emojis
    return text if len(text) <= n else text[: n - 1] + "…"
