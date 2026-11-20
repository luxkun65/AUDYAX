"""Audio engine of the player: FFmpeg decodes, sounddevice plays. No UI code here.

Besides one song it can continue with the next one without a gap (gapless) or mixing the end
of one with the start of the next (crossfade), and apply a 10-band equalizer (FFmpeg filters)."""

import os
import shutil
import subprocess
import sys
import threading

SAMPLE_RATE = 44100          # samples per second


CHANNELS = 2


BYTES_PER_FRAME = 2 * CHANNELS   # int16 stereo


FRAMES_PER_BLOCK = 2048


MAX_BUFFER = SAMPLE_RATE * BYTES_PER_FRAME * 2   # ~2 s of audio decoded ahead


BYTES_PER_SECOND = SAMPLE_RATE * BYTES_PER_FRAME
READ_SIZE = 16384                # bytes read from FFmpeg at a time (a whole number of frames)

# Equalizer: centre of each band (Hz) and limits of each gain (dB)
EQ_BANDS = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
EQ_LIMIT = 12.0
EQ_PRESETS = {
    "Plano":        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "Graves+":      (7, 6, 5, 3, 1, 0, 0, 0, 0, 0),
    "Agudos+":      (0, 0, 0, 0, 0, 1, 3, 5, 6, 7),
    "Rock":         (5, 4, 3, 1, -1, -1, 1, 3, 4, 5),
    "Pop":          (-1, 0, 2, 4, 5, 4, 2, 0, -1, -1),
    "Electrónica":  (6, 5, 2, 0, -2, 0, 1, 3, 5, 6),
    "Hip-hop":      (6, 5, 3, 2, -1, -1, 1, 0, 2, 3),
    "Jazz":         (3, 2, 1, 2, -1, -1, 0, 1, 2, 3),
    "Clásica":      (4, 3, 2, 1, -1, -1, 0, 2, 3, 4),
    "Acústica":     (4, 3, 2, 1, 2, 2, 3, 3, 2, 1),
    "Voz":          (-2, -1, 0, 2, 4, 4, 3, 1, 0, -1),
    "Noche":        (-3, -2, 0, 1, 2, 2, 1, 0, -2, -4),
}


def eq_filter(gains) -> str:
    """FFmpeg filter for the equalizer ("" if it's flat). It lowers the volume as much as
    the highest boost so loud songs don't distort, and a limiter catches what's left."""
    gains = [max(-EQ_LIMIT, min(EQ_LIMIT, float(g))) for g in (gains or [])][:len(EQ_BANDS)]
    if not any(abs(g) >= 0.05 for g in gains):
        return ""
    parts = [f"equalizer=f={f}:t=o:w=1:g={g:.1f}" for f, g in zip(EQ_BANDS, gains) if abs(g) >= 0.05]
    headroom = max(0.0, max(gains))
    if headroom:
        parts.insert(0, f"volume={-headroom:.1f}dB")
    parts.append("alimiter=limit=0.97:level=false")
    return ",".join(parts)


class StopPlayback(Exception):
    """Raised by the engine inside the callback when the song is over."""


def audio_available() -> tuple:
    """(True, "") if playback is possible; otherwise (False, reason to show)."""
    try:
        import numpy  # noqa: F401
    except ImportError:
        return False, "falta la librería numpy (pip install numpy)"
    try:
        import sounddevice as sd
    except ImportError:
        return False, "falta la librería sounddevice (pip install sounddevice)"
    except OSError as e:  # Linux without PortAudio
        return False, f"falta PortAudio ({e}). En Linux: sudo apt install libportaudio2"
    except Exception as e:  # PortAudioError: PortAudio doesn't start (e.g. no sound server)
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
    """Real output to the speakers."""

    def __init__(self, callback, on_finish):
        import sounddevice as sd

        def cb(outdata, frames, _time, _state):
            try:
                callback(outdata, frames)
            except StopPlayback:
                raise sd.CallbackStop

        # latency=0.1: without it, some systems (e.g. PulseAudio) request audio in blocks
        # of ~1 s and the progress bar moves in jumps; 0.1 s is still safe on slow computers
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


class _Decoder:
    """One FFmpeg process decoding one file to raw PCM."""

    def __init__(self, path, start_at=0.0, audio_filter=""):
        self.path, self.start = str(path), float(start_at)
        self.duration = audio_duration(path)
        self.read_bytes = 0               # bytes delivered so far
        extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
        af = ["-af", audio_filter] if audio_filter else []
        self.process = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{self.start:.3f}", "-i", self.path,
             "-vn", *af, "-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **extra)

    def read(self, n=READ_SIZE) -> bytes:
        """Up to n bytes (fewer only at the end of the file)."""
        out = bytearray()
        while len(out) < n:
            chunk = self.process.stdout.read(n - len(out))
            if not chunk:
                break
            out += chunk
        self.read_bytes += len(out)
        return bytes(out)

    @property
    def remaining(self) -> float:
        """Seconds left to decode (by the duration in the tags; 0 if unknown)."""
        if not self.duration:
            return float("inf")
        return self.duration - self.start - self.read_bytes / BYTES_PER_SECOND

    def kill(self):
        try:
            self.process.kill()
            self.process.wait(timeout=2)
        except Exception:
            pass


def mix(out_chunk: bytes, in_chunk: bytes, done: int, total: int) -> bytes:
    """Crossfade of two int16 stereo blocks: `out` fades out and `in` fades in.
    `done` frames of the fade had already gone by, out of `total`."""
    import numpy as np
    n = max(len(out_chunk), len(in_chunk)) // BYTES_PER_FRAME
    a = np.zeros(n * CHANNELS, dtype=np.float32)
    b = np.zeros(n * CHANNELS, dtype=np.float32)
    ia = np.frombuffer(out_chunk[:len(out_chunk) // BYTES_PER_FRAME * BYTES_PER_FRAME], dtype=np.int16)
    ib = np.frombuffer(in_chunk[:len(in_chunk) // BYTES_PER_FRAME * BYTES_PER_FRAME], dtype=np.int16)
    a[:len(ia)], b[:len(ib)] = ia, ib
    t = np.clip((done + np.arange(n, dtype=np.float32)) / max(1, total), 0.0, 1.0)
    t = np.repeat(t, CHANNELS)
    # equal power: the volume doesn't dip in the middle of the fade
    mixed = a * np.cos(t * np.pi / 2) + b * np.sin(t * np.pi / 2)
    return np.clip(mixed, -32768, 32767).astype(np.int16).tobytes()


class AudioEngine:
    """Plays a file. Methods: play(path, start_at), pause_playback(), resume(),
    toggle(), seek(sec), stop(), set_equalizer(gains); properties: position, duration,
    volume, status, crossfade.

    on_finish() is called (from the audio thread) when the song ends by itself and nothing
    follows. To continue without a gap, set peek_next(ahead) and on_advance(path):
      peek_next(ahead) -> path of the song that follows, or None. Decoding runs ~2 s ahead,
        so `ahead` songs may already be waiting after the one that is heard: the answer is
        the song after those. It must not move anything.
      on_advance(path) -> called from the audio thread when that song starts sounding.
    crossfade > 0 mixes that many seconds of both songs."""

    def __init__(self, on_finish=None, output_factory=SounddeviceOutput):
        self.on_finish = on_finish
        self.peek_next = None
        self.on_advance = None
        self.crossfade = 0.0
        self._factory = output_factory
        self._volume = 1.0
        self._eq = None                   # gains in dB per band, or None
        self.path, self.duration = None, 0.0
        self.status = "stopped"           # "playing" | "paused" | "stopped"
        self._offset, self._frames = 0.0, 0
        self._output, self._decoders = None, []
        self._buffer = bytearray()
        self._cond = threading.Condition()
        self._end_of_stream = False
        self._generation = 0              # each start invalidates the previous one's threads
        self._paused = False
        self._written = self._consumed = 0   # bytes into / out of the buffer since the start
        self._marks = []                  # [(byte where a new song starts, path, duration)]

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

    def seek(self, seconds: float):
        """Jumps to another part of the song (decoding starts again from there)."""
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

    def set_equalizer(self, gains):
        """Changes the equalizer (None or all 0 = off). If something is playing it continues
        from the same point with the new sound."""
        new = list(gains) if gains is not None else None
        if eq_filter(new) == eq_filter(self._eq):
            self._eq = new
            return
        self._eq = new
        if self.status in ("playing", "paused") and self.path:
            self.seek(self.position)

    @property
    def equalizer(self):
        return self._eq

    @property
    def position(self) -> float:
        return self._offset + self._frames / SAMPLE_RATE

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = max(0.0, min(1.0, float(value)))

    # ---------------------------------------------------------------- internals
    def _start(self, start_at: float):
        self._generation += 1
        gen = self._generation
        self._offset, self._frames = float(start_at), 0
        with self._cond:
            self._buffer.clear()
            self._end_of_stream = False
            self._written = self._consumed = 0
            self._marks = []
        first = _Decoder(self.path, start_at, eq_filter(self._eq))
        self._decoders = [first]
        threading.Thread(target=self._read, args=(gen, first), daemon=True).start()
        # Wait until something is decoded before opening the audio (max 1 s): otherwise the
        # first blocks the sound card asks for come out silent
        with self._cond:
            self._cond.wait_for(lambda: len(self._buffer) >= BYTES_PER_SECOND // 5
                                or self._end_of_stream or gen != self._generation, timeout=1.0)
        self._output = self._factory(lambda out, n: self._refill(gen, out, n),
                                     lambda: self._finished(gen))
        self.status = "playing"
        self._output.start()

    def _open_next(self, gen):
        """Starts decoding the next song (if there is one) and marks where it begins."""
        if gen != self._generation or not callable(self.peek_next):
            return None
        with self._cond:
            ahead = len(self._marks)      # songs already decoded that haven't started sounding
        try:
            path = self.peek_next(ahead)
        except Exception:
            path = None
        if not path or not os.path.isfile(path):
            return None
        decoder = _Decoder(path, 0.0, eq_filter(self._eq))
        with self._cond:
            if gen != self._generation:
                decoder.kill()
                return None
            self._decoders.append(decoder)
            self._marks.append((self._written, decoder.path, decoder.duration))
        return decoder

    def _push(self, gen, data: bytes) -> bool:
        """Adds decoded audio to the buffer, waiting while ~2 s are already ahead."""
        with self._cond:
            if gen != self._generation:
                return False
            self._buffer.extend(data)
            self._written += len(data)
            self._cond.notify_all()
            while len(self._buffer) > MAX_BUFFER and gen == self._generation:
                self._cond.wait(0.5)
            return gen == self._generation

    def _read(self, gen, current):
        """Thread: decodes the current song (and the next ones, without a gap or mixing
        them) into the buffer, staying ~2 s ahead of what is heard."""
        nxt, fade_total, fade_done, tried_next = None, 0, 0, False
        try:
            while gen == self._generation:
                chunk = current.read()
                if gen != self._generation:
                    return
                xfade = max(0.0, float(self.crossfade or 0))
                # crossfade: start the next song `xfade` seconds before this one ends
                if (chunk and xfade > 0 and nxt is None and not tried_next
                        and current.remaining <= xfade):
                    tried_next = True
                    nxt = self._open_next(gen)
                    if nxt is not None:
                        fade_total = max(1, int(max(0.0, current.remaining) * SAMPLE_RATE) + len(chunk) // BYTES_PER_FRAME)
                        fade_done = 0
                if chunk and nxt is not None:
                    incoming = nxt.read(len(chunk))
                    chunk = mix(chunk, incoming, fade_done, fade_total)
                    fade_done += len(chunk) // BYTES_PER_FRAME
                if chunk:
                    if not self._push(gen, chunk):
                        return
                    continue
                # end of the current song
                current.kill()
                if nxt is None and not tried_next:
                    nxt = self._open_next(gen)          # gapless: straight after
                if nxt is None:
                    with self._cond:
                        if gen == self._generation:
                            self._end_of_stream = True
                            self._cond.notify_all()
                    return
                current, nxt, tried_next = nxt, None, False
        except (OSError, ValueError):
            with self._cond:
                if gen == self._generation:
                    self._end_of_stream = True
                    self._cond.notify_all()

    def _refill(self, gen, output, frames: int):
        """Audio callback: delivers `frames` of audio (or silence while paused)."""
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
        self._consumed += got * BYTES_PER_FRAME
        while self._marks and self._consumed >= self._marks[0][0]:   # the next song starts sounding
            mark, path, duration = self._marks.pop(0)
            self.path, self.duration = path, duration
            self._offset, self._frames = 0.0, (self._consumed - mark) // BYTES_PER_FRAME
            if self.on_advance:
                self.on_advance(path)
        if exhausted and got < frames:
            raise StopPlayback

    def _finished(self, gen):
        if gen != self._generation:
            return  # closed on purpose (stop / seek): it's not the "end of the song"
        self.status = "stopped"
        if self.on_finish:
            self.on_finish()

    def _stop_all(self):
        self._generation += 1
        output, decoders = self._output, self._decoders
        self._output, self._decoders = None, []
        with self._cond:
            self._buffer.clear()
            self._marks = []
            self._cond.notify_all()
        if output is not None:
            output.close()
        for d in decoders:
            d.kill()
