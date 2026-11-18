"""
Builds AUDYAX ready to use without Python, with PyInstaller.

    pip install -r requirements-dev.txt
    python tools/download_ffmpeg.py       # (Windows) downloads FFmpeg to vendor/ffmpeg
    python build.py --zip

Result:
    dist/AUDYAX/                 program folder: AUDYAX.exe + _internal/ + ffmpeg/
    dist/AUDYAX-<version>-<system>.zip    (with --zip) what is published on GitHub Releases

A FOLDER is built instead of a single .exe: FFmpeg weighs >100 MB and, inside a single .exe,
it would have to be unpacked on every start (the program would be slow to open).
It must be built on the same operating system it will be used on.

Options:
    --ffmpeg FOLDER    folder with ffmpeg(.exe), ffprobe(.exe) and their DLLs to include
                       (vendor/ffmpeg by default if it exists; otherwise a warning and it's left out)
    --zip              compresses the folder for publishing
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
    sep = os.pathsep  # ';' on Windows, ':' on Linux/macOS (--add-data format)
    args = [
        str(ROOT / "descargador_gui.py"),
        "--name", "AUDYAX",
        "--onedir",
        "--windowed",                              # no console window
        "--icon", str(ROOT / "assets" / "audyax.ico"),
        "--add-data", f"{ROOT / 'assets'}{sep}assets",  # window and tray icons
        "--collect-data", "customtkinter",         # customtkinter themes and fonts
        "--hidden-import", "PIL._tkinter_finder",  # Pillow + tkinter (without it, it doesn't open)
        "--collect-all", "tkinterdnd2",            # native drag and drop library
        "--collect-data", "ytmusicapi",            # translations ytmusicapi loads on startup
        "--collect-submodules", "pystray",         # the tray picks its module on startup
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        "--noconfirm",
        "--clean",
    ]
    # sounddevice ships PortAudio (the mini player's audio output) in _sounddevice_data
    if IS_WINDOWS or sys.platform == "darwin":
        args += ["--collect-data", "_sounddevice_data", "--collect-binaries", "_sounddevice_data"]
    # plyer also picks its notification module on startup, depending on the system
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
        # ffmpeg, ffprobe, their DLLs ("shared" build) and FFmpeg's license
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
