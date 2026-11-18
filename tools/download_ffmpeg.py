"""
Downloads FFmpeg for Windows (BtbN/FFmpeg-Builds LGPL "shared" build) to vendor/ffmpeg,
to include it in the executable with build.py.

    python tools/download_ffmpeg.py

The LGPL version is used because it can be redistributed with AUDYAX (MIT license) as long
as its license is included, which is copied next to the executables. It includes libmp3lame
and libopus, which is what AUDYAX needs for MP3 and Opus.
"""
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
       "ffmpeg-master-latest-win64-lgpl-shared.zip")
TARGET_DIR = Path(__file__).resolve().parent.parent / "vendor" / "ffmpeg"


def main():
    print(f"Downloading {URL} …")
    with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "AUDYAX"}),
                                timeout=300) as r:
        data = r.read()
    print(f"{len(data) / 1048576:.0f} MB downloaded")
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
    TARGET_DIR.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            path = Path(name)
            # bin/ffmpeg.exe, bin/ffprobe.exe, bin/*.dll and the license
            if (path.parent.name == "bin" and (path.stem in ("ffmpeg", "ffprobe") or path.suffix == ".dll")) \
                    or path.name.upper().startswith("LICENSE"):
                (TARGET_DIR / path.name).write_bytes(z.read(name))
    files = sorted(p.name for p in TARGET_DIR.iterdir())
    if "ffmpeg.exe" not in files:
        sys.exit("No se encontró ffmpeg.exe en el archivo descargado")
    print(f"FFmpeg ready in {TARGET_DIR} ({len(files)} files)")


if __name__ == "__main__":
    main()
