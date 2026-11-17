"""
Genera el ejecutable de AUDYAX con PyInstaller.

    pip install -r requirements-dev.txt
    python build.py

El resultado queda en dist/ (AUDYAX.exe en Windows). Hay que generarlo en el mismo
sistema operativo en el que se va a usar. FFmpeg NO va incluido: debe estar instalado
y en el PATH del equipo donde se ejecute.
"""
import PyInstaller.__main__

PyInstaller.__main__.run([
    "descargador_gui.py",
    "--name", "AUDYAX",
    "--onefile",
    "--windowed",                       # sin ventana de consola
    "--collect-data", "customtkinter",  # temas y fuentes de customtkinter
    "--hidden-import", "PIL._tkinter_finder",  # Pillow + tkinter (si falta, no abre)
    "--noconfirm",
    "--clean",
])
