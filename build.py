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

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from engine import VERSION  # noqa: E402

IS_WINDOWS = sys.platform.startswith("win")


def pyinstaller_args() -> list:
    sep = os.pathsep  # ';' en Windows, ':' en Linux/macOS (formato de --add-data)
    args = [
        str(ROOT / "descargador_gui.py"),
        "--name", "AUDYAX",
        "--onedir",
        "--windowed",                              # sin ventana de consola
        "--icon", str(ROOT / "assets" / "audyax.ico"),
        "--add-data", f"{ROOT / 'assets'}{sep}assets",  # iconos de la ventana y la bandeja
        "--collect-data", "customtkinter",         # temas y fuentes de customtkinter
        "--hidden-import", "PIL._tkinter_finder",  # Pillow + tkinter (si falta, no abre)
        "--collect-all", "tkinterdnd2",            # librería nativa de arrastrar y soltar
        "--collect-data", "ytmusicapi",            # traducciones que ytmusicapi carga al iniciar
        "--collect-submodules", "pystray",         # la bandeja elige su módulo al arrancar
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--noconfirm",
        "--clean",
    ]
    # sounddevice trae PortAudio (la salida de audio del mini reproductor) en _sounddevice_data
    if IS_WINDOWS or sys.platform == "darwin":
        args += ["--collect-data", "_sounddevice_data", "--collect-binaries", "_sounddevice_data"]
    # plyer también elige su módulo de notificaciones al arrancar, según el sistema
    if IS_WINDOWS:
        args += ["--hidden-import", "plyer.platforms.win.notification",
                 "--hidden-import", "plyer.platforms.win.libs.balloontip"]
    elif sys.platform == "darwin":
        args += ["--hidden-import", "plyer.platforms.macosx.notification"]
    else:
        args += ["--hidden-import", "plyer.platforms.linux.notification"]
    return args


def copy_ffmpeg(source: Path, dest: Path) -> bool:
    exe = ".exe" if IS_WINDOWS else ""
    if not (source / f"ffmpeg{exe}").is_file():
        print(f"AVISO: no hay ffmpeg{exe} en {source}; el programa necesitará FFmpeg instalado.")
        return False
    dest.mkdir(parents=True, exist_ok=True)
    for file in source.iterdir():
        # ffmpeg, ffprobe, sus DLL (compilación "shared") y la licencia de FFmpeg
        if file.is_file() and (file.stem in ("ffmpeg", "ffprobe") or file.suffix.lower() == ".dll"
                                  or file.name.upper().startswith("LICENSE")):
            shutil.copy2(file, dest / file.name)
    print(f"FFmpeg incluido desde {source}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Genera el ejecutable de AUDYAX")
    parser.add_argument("--ffmpeg", type=Path, default=ROOT / "vendor" / "ffmpeg")
    parser.add_argument("--zip", action="store_true")
    options = parser.parse_args()

    PyInstaller.__main__.run(pyinstaller_args())
    folder = ROOT / "dist" / "AUDYAX"
    copy_ffmpeg(options.ffmpeg, folder / "ffmpeg")
    for name in ("LICENSE", "README.md"):
        shutil.copy2(ROOT / name, folder / name)

    if options.zip:
        system = "windows" if IS_WINDOWS else ("macos" if sys.platform == "darwin" else "linux")
        base = ROOT / "dist" / f"AUDYAX-{VERSION}-{system}"
        file = shutil.make_archive(str(base), "zip", root_dir=folder.parent, base_dir=folder.name)
        print(f"Listo: {file}")
    print(f"Programa en: {folder}")


if __name__ == "__main__":
    main()
