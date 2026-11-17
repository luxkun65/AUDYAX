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
DESTINO = Path(__file__).resolve().parent.parent / "vendor" / "ffmpeg"


def main():
    print(f"Descargando {URL} …")
    with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "AUDYAX"}),
                                timeout=300) as r:
        datos = r.read()
    print(f"{len(datos) / 1048576:.0f} MB descargados")
    if DESTINO.exists():
        shutil.rmtree(DESTINO)
    DESTINO.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        for nombre in z.namelist():
            ruta = Path(nombre)
            # bin/ffmpeg.exe, bin/ffprobe.exe, bin/*.dll y la licencia
            if (ruta.parent.name == "bin" and (ruta.stem in ("ffmpeg", "ffprobe") or ruta.suffix == ".dll")) \
                    or ruta.name.upper().startswith("LICENSE"):
                (DESTINO / ruta.name).write_bytes(z.read(nombre))
    archivos = sorted(p.name for p in DESTINO.iterdir())
    if "ffmpeg.exe" not in archivos:
        sys.exit("No se encontró ffmpeg.exe en el archivo descargado")
    print(f"FFmpeg listo en {DESTINO} ({len(archivos)} archivos)")


if __name__ == "__main__":
    main()
