"""Audio engine of the player: FFmpeg decodes, sounddevice plays. No UI code here."""

import shutil
import subprocess
import sys
import threading

SAMPLE_RATE = 44100          # samples per second


CHANNELS = 2


BYTES_PER_FRAME = 2 * CHANNELS   # int16 stereo


FRAMES_PER_BLOCK = 2048


MAX_BUFFER = SAMPLE_RATE * BYTES_PER_FRAME * 2   # ~2 s of audio decoded ahead


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


class AudioEngine:
    """Plays a file. Methods: play(path, start_at), pause_playback(), resume(),
    toggle(), seek(sec), stop(); properties: position, duration, volume, status.

    on_finish() is called (from the audio thread) when the song ends by itself."""

    def __init__(self, on_finish=None, output_factory=SounddeviceOutput):
        self.on_finish = on_finish
        self._factory = output_factory
        self._volume = 1.0
        self.path, self.duration = None, 0.0
        self.status = "stopped"           # "playing" | "paused" | "stopped"
        self._offset, self._frames = 0.0, 0
        self._output, self._process = None, None
        self._buffer = bytearray()
        self._cond = threading.Condition()
        self._end_of_stream = False
        self._generation = 0              # each start invalidates the previous one's threads
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
        extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
        self._process = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{start_at:.3f}", "-i", self.path,
             "-vn", "-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **extra)
        threading.Thread(target=self._read, args=(gen, self._process), daemon=True).start()
        # Wait until something is decoded before opening the audio (max 1 s): otherwise the
        # first blocks the sound card asks for come out silent
        with self._cond:
            self._cond.wait_for(lambda: len(self._buffer) >= SAMPLE_RATE * BYTES_PER_FRAME // 5
                                or self._end_of_stream or gen != self._generation, timeout=1.0)
        self._output = self._factory(lambda out, n: self._refill(gen, out, n),
                                     lambda: self._finished(gen))
        self.status = "playing"
        self._output.start()

    def _read(self, gen, process):
        """Thread: reads FFmpeg's decoded audio until ~2 s are buffered ahead."""
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
