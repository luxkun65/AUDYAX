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


# ------------------------------------------------------------------------------
# Reading it back, for the player: same loudness for every song while listening
# ------------------------------------------------------------------------------
def _number(text):
    found = re.search(r"[-+]?\d+(?:\.\d+)?", str(text or ""))
    return float(found.group(0)) if found else None


def read_replaygain(path) -> dict:
    """ReplayGain stored in a file, as numbers: {"track_gain", "track_peak", "album_gain",
    "album_peak"} (dB relative to -18 LUFS, peaks as 0..1). Missing values are left out.
    Reads the TXXX frames (MP3), the iTunes atoms (M4A), R128_* (Opus) and REPLAYGAIN_*
    comments (FLAC, Ogg), so it also works with files tagged by other programs."""
    if not HAS_MUTAGEN:
        return {}
    path = Path(path)
    out = {}
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
    except Exception:
        return {}
    if audio is None or not audio.tags:
        return {}
    tags = audio.tags
    found = {}
    try:
        items = tags.items() if hasattr(tags, "items") else list(tags)
    except Exception:
        items = []
    for key, value in items:
        name = str(key).upper()
        if name.startswith("TXXX:"):
            name = name[5:]
        elif name.startswith("----:COM.APPLE.ITUNES:"):
            name = name[len("----:COM.APPLE.ITUNES:"):]
        texts = getattr(value, "text", value)
        texts = texts if isinstance(texts, (list, tuple)) else [texts]
        text = texts[0] if texts else ""
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        found[name] = text
    for kind in ("TRACK", "ALBUM"):
        gain = _number(found.get(f"REPLAYGAIN_{kind}_GAIN"))
        if gain is None and found.get(f"R128_{kind}_GAIN") is not None:
            r128 = _number(found.get(f"R128_{kind}_GAIN"))
            if r128 is not None:   # Q7.8 relative to -23 LUFS -> dB relative to -18 LUFS
                gain = r128 / 256 + (REFERENCE_LUFS - REFERENCE_R128)
        peak = _number(found.get(f"REPLAYGAIN_{kind}_PEAK"))
        if gain is not None:
            out[f"{kind.lower()}_gain"] = gain
        if peak is not None and peak > 0:
            out[f"{kind.lower()}_peak"] = peak
    return out


def playback_db(info: dict, mode: str = "track", preamp: float = 0.0) -> float:
    """dB to apply when playing a song with the ReplayGain `info` (read_replaygain).
    mode: "track" (every song at the same loudness), "album" (keeps the differences inside
    an album; falls back to the track value) or "off". Never louder than the song's peak
    allows, so it doesn't distort. Songs without ReplayGain: 0 dB."""
    if mode == "off" or not info:
        return 0.0
    kind = "album" if mode == "album" and "album_gain" in info else "track"
    gain = info.get(f"{kind}_gain")
    if gain is None:
        return 0.0
    db = gain + preamp
    peak = info.get(f"{kind}_peak")
    if peak and db > 0:
        db = min(db, -20 * math.log10(peak))   # headroom up to the peak
    return round(db, 2)
