"""Audio cutter (part 2, library and tools package): keeps the part of a song between two
moments, with optional fade in / fade out, as a new file next to the original (or replacing
it). The tags and the cover are kept. No UI code here.

The audio is encoded again (same format) so the cut is exact to the hundredth of a second;
copying the stream would cut at the nearest frame and could leave a click."""

import os
import subprocess
import sys
from pathlib import Path

# format -> FFmpeg encoder and quality
CODECS = {
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "0"],
    ".m4a": ["-c:a", "aac", "-b:a", "256k"],
    ".mp4": ["-c:a", "aac", "-b:a", "256k"],
    ".opus": ["-c:a", "libopus", "-b:a", "160k"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "6"],
    ".flac": ["-c:a", "flac"],
    ".wav": ["-c:a", "pcm_s16le"],
}
WITH_COVER = {".mp3", ".m4a", ".mp4", ".flac"}   # formats whose cover FFmpeg can carry over


def cut_name(path, suffix: str = " (recorte)") -> Path:
    """"Song.mp3" -> "Song (recorte).mp3"; if it exists, "Song (recorte 2).mp3"…"""
    path = Path(path)
    candidate = path.with_name(f"{path.stem}{suffix}{path.suffix}")
    n = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}{suffix.rstrip(')')} {n}){path.suffix}")
        n += 1
    return candidate


def build_command(src, dst, start: float, end: float, fade_in: float = 0.0, fade_out: float = 0.0):
    """FFmpeg command that writes dst with src[start:end]."""
    src, dst = Path(src), Path(dst)
    if end <= start:
        raise ValueError("El final del recorte tiene que ir después del inicio.")
    length = end - start
    filters = []
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={min(fade_in, length):.2f}")
    if fade_out > 0:
        d = min(fade_out, length)
        filters.append(f"afade=t=out:st={max(0.0, length - d):.2f}:d={d:.2f}")
    ext = src.suffix.lower()
    codec = CODECS.get(ext, ["-c:a", "libmp3lame", "-q:a", "0"])
    maps = ["-map", "0:a:0"]
    if ext in WITH_COVER:
        maps += ["-map", "0:v?", "-c:v", "copy", "-disposition:v", "attached_pic"]
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
           "-i", str(src), *maps, "-map_metadata", "0"]
    if filters:
        cmd += ["-af", ",".join(filters)]
    cmd += codec
    if ext == ".mp3":
        cmd += ["-id3v2_version", "3"]
    cmd.append(str(dst))
    return cmd


def trim(src, start: float, end: float, fade_in: float = 0.0, fade_out: float = 0.0,
         dst=None, replace: bool = False) -> Path:
    """Cuts src and returns the path of the result. replace=True swaps the original for the
    cut (only once the cut is complete, so the song is never lost)."""
    src = Path(src)
    if replace:
        out = src.with_name(f".{src.stem}.recortando{src.suffix}")
    else:
        out = Path(dst) if dst else cut_name(src)
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
    result = subprocess.run(build_command(src, out, start, end, fade_in, fade_out),
                            capture_output=True, text=True, **extra)
    if result.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        try:
            out.unlink()
        except OSError:
            pass
        raise RuntimeError((result.stderr or "FFmpeg no pudo recortar el archivo").strip().splitlines()[-1])
    if replace:
        os.replace(out, src)
        return src
    return out
