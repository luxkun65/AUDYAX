"""ReplayGain: measures loudness with FFmpeg and stores it in the tags WITHOUT touching the audio."""

import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from mutagen.id3 import ID3, TXXX
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4, AtomDataType, MP4FreeForm
    from mutagen.oggopus import OggOpus
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False


# ------------------------------------------------------------------------------
# ReplayGain: measures loudness and stores it in the tags WITHOUT touching the audio
# ------------------------------------------------------------------------------
REFERENCE_LUFS = -18.0   # ReplayGain 2.0 reference level


REFERENCE_R128 = -23.0   # reference level used by Opus (EBU R128)


def analyze_loudness(path, duration=None):
    """Measures loudness with ffmpeg's ebur128 filter. Doesn't modify the file."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform.startswith("win") else {}
    r = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-vn",
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", **extra)
    summary = r.stderr.split("Summary:")[-1]
    lufs_match = re.search(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", summary)
    if not lufs_match:
        return None
    lufs = float(lufs_match.group(1))
    peak_match = re.search(r"Peak:\s+(-?\d+(?:\.\d+)?)\s+dBFS", summary)
    peak = 10 ** (float(peak_match.group(1)) / 20) if peak_match else 1.0
    return {"lufs": lufs, "peak": peak, "gain": REFERENCE_LUFS - lufs, "dur": duration or 1.0}


def album_gain(tracks):
    """Loudness of the whole set: duration-weighted energy average (approximate)."""
    total = sum(p["dur"] for p in tracks)
    energy = sum(p["dur"] * 10 ** (p["lufs"] / 10) for p in tracks) / total
    lufs = 10 * math.log10(energy)
    return {"lufs": lufs, "peak": max(p["peak"] for p in tracks), "gain": REFERENCE_LUFS - lufs}


def rg_text(track, album=None):
    t = f"pista {track['gain']:.2f} dB"
    if album:
        t += f" · álbum {album['gain']:.2f} dB"
    return t


def write_replaygain(path: Path, track: dict, album=None):
    """Writes the ReplayGain tags in the format that matches each file."""
    if not HAS_MUTAGEN:
        raise RuntimeError("falta mutagen (pip install mutagen)")
    ext = path.suffix.lower()

    pairs = {"REPLAYGAIN_TRACK_GAIN": f"{track['gain']:.2f} dB",
             "REPLAYGAIN_TRACK_PEAK": f"{track['peak']:.6f}"}
    if album:
        pairs["REPLAYGAIN_ALBUM_GAIN"] = f"{album['gain']:.2f} dB"
        pairs["REPLAYGAIN_ALBUM_PEAK"] = f"{album['peak']:.6f}"

    if ext == ".mp3":
        audio = MP3(path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        for key, value in pairs.items():
            audio.tags.delall(f"TXXX:{key}")
            audio.tags.add(TXXX(encoding=3, desc=key, text=[value]))
        audio.save(v2_version=3)
    elif ext in (".m4a", ".mp4"):
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        for key, value in pairs.items():
            audio.tags[f"----:com.apple.iTunes:{key.lower()}"] = [
                MP4FreeForm(value.encode("utf-8"), dataformat=AtomDataType.UTF8)]
        audio.save()
    elif ext == ".opus":
        # Opus uses another standard: gain in Q7.8 format relative to -23 LUFS
        def q78(lufs):
            return str(max(-32768, min(32767, round((REFERENCE_R128 - lufs) * 256))))
        audio = OggOpus(path)
        audio["R128_TRACK_GAIN"] = [q78(track["lufs"])]
        if album:
            audio["R128_ALBUM_GAIN"] = [q78(album["lufs"])]
        audio.save()
    else:
        raise ValueError(f"el formato {ext} no admite ReplayGain en este programa")
