"""
Descarga FFmpeg para Windows (compilación LGPL "shared" de BtbN/FFmpeg-Builds) a vendor/ffmpeg,
para incluirlo en el ejecutable con build.py.

    python tools/descargar_ffmpeg.py

Se usa la versión LGPL porque se puede redistribuir junto a AUDYAX (licencia MIT) siempre que
se incluya su licencia, que se copia junto a los ejecutables. Incluye libmp3lame y libopus,
que es lo que AUDYAX necesita para MP3 y Opus.
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
    print(f"Descargando {URL} …")
    with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "AUDYAX"}),
                                timeout=300) as r:
        data = r.read()
    print(f"{len(data) / 1048576:.0f} MB descargados")
    if TARGET_DIR.exists():
        shutil.rmtree(TARGET_DIR)
    TARGET_DIR.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            path = Path(name)
            # bin/ffmpeg.exe, bin/ffprobe.exe, bin/*.dll y la licencia
            if (path.parent.name == "bin" and (path.stem in ("ffmpeg", "ffprobe") or path.suffix == ".dll")) \
                    or path.name.upper().startswith("LICENSE"):
                (TARGET_DIR / path.name).write_bytes(z.read(name))
    files = sorted(p.name for p in TARGET_DIR.iterdir())
    if "ffmpeg.exe" not in files:
        sys.exit("No se encontró ffmpeg.exe en el archivo descargado")
    print(f"FFmpeg listo en {TARGET_DIR} ({len(files)} archivos)")


if __name__ == "__main__":
    main()
