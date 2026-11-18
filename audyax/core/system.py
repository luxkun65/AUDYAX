"""Where the program lives and the FFmpeg bundled with the .exe."""

import os
import sys
from pathlib import Path


def program_folder() -> Path:
    """Folder where AUDYAX lives: the .exe's folder when packaged, or the project root."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def setup_bundled_ffmpeg() -> str:
    """If there's an 'ffmpeg' folder next to the program (the .exe version ships it), it is put
    first in the PATH so yt-dlp and the ReplayGain calculation use it.
    Returns the folder used, or "" if there is none."""
    candidates = [program_folder() / "ffmpeg"]
    if getattr(sys, "_MEIPASS", None):
        candidates.append(Path(sys._MEIPASS) / "ffmpeg")
    for folder in candidates:
        exe = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
        if (folder / exe).is_file():
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
            return str(folder)
    return ""
