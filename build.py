"""
Genera AUDYAX listo para usar sin Python, con PyInstaller.

    pip install -r requirements-dev.txt
    python tools/descargar_ffmpeg.py      # (Windows) baja FFmpeg a vendor/ffmpeg
    python build.py --zip

Resultado:
    dist/AUDYAX/                 carpeta del programa: AUDYAX.exe + _internal/ + ffmpeg/
    dist/AUDYAX-<versión>-<sistema>.zip   (con --zip) lo que se publica en GitHub Releases

Se genera una CARPETA y no un solo .exe: FFmpeg pesa >100 MB y, dentro de un único .exe,
habría que descomprimirlo en cada arranque (el programa tardaría en abrir).
Hay que generarlo en el mismo sistema operativo en el que se va a usar.

Opciones:
    --ffmpeg CARPETA   carpeta con ffmpeg(.exe), ffprobe(.exe) y sus DLL a incluir
                       (por defecto vendor/ffmpeg si existe; si no, se avisa y no se incluye)
    --zip              comprime la carpeta para publicarla
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
from motor_descarga import VERSION  # noqa: E402

ES_WINDOWS = sys.platform.startswith("win")


def argumentos_pyinstaller() -> list:
    sep = os.pathsep  # ';' en Windows, ':' en Linux/macOS (formato de --add-data)
    args = [
        str(RAIZ / "descargador_gui.py"),
        "--name", "AUDYAX",
        "--onedir",
        "--windowed",                              # sin ventana de consola
        "--icon", str(RAIZ / "assets" / "audyax.ico"),
        "--add-data", f"{RAIZ / 'assets'}{sep}assets",  # iconos de la ventana y la bandeja
        "--collect-data", "customtkinter",         # temas y fuentes de customtkinter
        "--hidden-import", "PIL._tkinter_finder",  # Pillow + tkinter (si falta, no abre)
        "--collect-all", "tkinterdnd2",            # librería nativa de arrastrar y soltar
        "--collect-data", "ytmusicapi",            # traducciones que ytmusicapi carga al iniciar
        "--collect-submodules", "pystray",         # la bandeja elige su módulo al arrancar
        "--distpath", str(RAIZ / "dist"),
        "--workpath", str(RAIZ / "build"),
        "--specpath", str(RAIZ / "build"),
        "--noconfirm",
        "--clean",
    ]
    # sounddevice trae PortAudio (la salida de audio del mini reproductor) en _sounddevice_data
    if ES_WINDOWS or sys.platform == "darwin":
        args += ["--collect-data", "_sounddevice_data", "--collect-binaries", "_sounddevice_data"]
    # plyer también elige su módulo de notificaciones al arrancar, según el sistema
    if ES_WINDOWS:
        args += ["--hidden-import", "plyer.platforms.win.notification",
                 "--hidden-import", "plyer.platforms.win.libs.balloontip"]
    elif sys.platform == "darwin":
        args += ["--hidden-import", "plyer.platforms.macosx.notification"]
    else:
        args += ["--hidden-import", "plyer.platforms.linux.notification"]
    return args


def copiar_ffmpeg(origen: Path, destino: Path) -> bool:
    exe = ".exe" if ES_WINDOWS else ""
    if not (origen / f"ffmpeg{exe}").is_file():
        print(f"AVISO: no hay ffmpeg{exe} en {origen}; el programa necesitará FFmpeg instalado.")
        return False
    destino.mkdir(parents=True, exist_ok=True)
    for archivo in origen.iterdir():
        # ffmpeg, ffprobe, sus DLL (compilación "shared") y la licencia de FFmpeg
        if archivo.is_file() and (archivo.stem in ("ffmpeg", "ffprobe") or archivo.suffix.lower() == ".dll"
                                  or archivo.name.upper().startswith("LICENSE")):
            shutil.copy2(archivo, destino / archivo.name)
    print(f"FFmpeg incluido desde {origen}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Genera el ejecutable de AUDYAX")
    parser.add_argument("--ffmpeg", type=Path, default=RAIZ / "vendor" / "ffmpeg")
    parser.add_argument("--zip", action="store_true")
    opciones = parser.parse_args()

    PyInstaller.__main__.run(argumentos_pyinstaller())
    carpeta = RAIZ / "dist" / "AUDYAX"
    copiar_ffmpeg(opciones.ffmpeg, carpeta / "ffmpeg")
    for nombre in ("LICENSE", "README.md"):
        shutil.copy2(RAIZ / nombre, carpeta / nombre)

    if opciones.zip:
        sistema = "windows" if ES_WINDOWS else ("macos" if sys.platform == "darwin" else "linux")
        base = RAIZ / "dist" / f"AUDYAX-{VERSION}-{sistema}"
        archivo = shutil.make_archive(str(base), "zip", root_dir=carpeta.parent, base_dir=carpeta.name)
        print(f"Listo: {archivo}")
    print(f"Programa en: {carpeta}")


if __name__ == "__main__":
    main()
