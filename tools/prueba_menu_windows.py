"""
Prueba de interfaz en Windows (la corre GitHub Actions): abre AUDYAX y, con clics DE VERDAD
del mouse, pulsa "Exportar" y luego "Lista de reproducción (.m3u)"; comprueba que se exportó.

Existe porque el menú emergente de Exportar no aparecía en algunos Windows: este tipo de
fallo solo se ve en Windows y con clics reales.

    python tools/prueba_menu_windows.py resultado.json
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pyautogui

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import motor_descarga as m  # noqa: E402

m.CONFIG_PATH = Path(tempfile.gettempdir()) / "audyax_prueba_menu.json"
import descargador_gui as g  # noqa: E402

pyautogui.FAILSAFE = False
salida = Path(sys.argv[1])
carpeta = Path(tempfile.mkdtemp(prefix="audyax_menu_"))
cancion = carpeta / "Uno.mp3"
subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                "-c:a", "libmp3lame", str(cancion)], check=True)
lista = carpeta / "lista.m3u"

resultado = {"mensajes": []}
g.filedialog.asksaveasfilename = lambda **k: str(lista)       # sin el diálogo nativo
g.messagebox.showinfo = lambda t, msg, **k: resultado["mensajes"].append(msg)
g.messagebox.showerror = lambda t, msg, **k: resultado["mensajes"].append("ERROR: " + msg)

app = g.App()
app.minsize(800, 500)
app.geometry("1000x700+0+0")
app._manejar(("nuevo", "v1", {"titulo": "Uno", "url": "u", "miniatura": "", "subcarpeta": ""}))
app._manejar(("listo", "v1", {"titulo": "Uno", "artista": "A", "album": "", "anio": "", "genero": "",
                              "url": "u", "ruta": str(cancion)}))
pedido = {"centro": None}           # el robot pide dónde hacer clic; la ventana se lo dice
listo = threading.Event()
terminado = threading.Event()


def centro(widget):
    widget.update_idletasks()
    return (widget.winfo_rootx() + widget.winfo_width() // 2,
            widget.winfo_rooty() + widget.winfo_height() // 2)


def preparar():
    app.lift()
    app.focus_force()
    app.update()
    pedido["centro"] = centro(app.btn_exportar)
    listo.set()


def buscar_dialogo(intentos=[0]):
    v = app.ventana_exportar
    if v is not None and v.winfo_exists() and v.winfo_ismapped():
        resultado["dialogo_abierto"] = True
        pedido["centro"] = centro(v.btn_m3u)
        listo.set()
    elif intentos[0] < 25:
        intentos[0] += 1
        app.after(200, buscar_dialogo)
    else:
        resultado["dialogo_abierto"] = False
        terminado.set()


def captura():
    try:
        pyautogui.screenshot(str(salida.with_suffix(".png")))
    except Exception:  # en Linux necesita herramientas extra; la captura es solo de ayuda
        pass


def robot():
    try:
        listo.wait(30)
        time.sleep(1)
        listo.clear()
        pyautogui.click(*pedido["centro"])                 # clic en "Exportar"
        time.sleep(1)
        app.after(0, buscar_dialogo)
        if not listo.wait(10):
            captura()
            return
        captura()
        pyautogui.click(*pedido["centro"])                 # clic en "Lista de reproducción (.m3u)"
        time.sleep(2)
    except Exception as e:
        resultado["error_robot"] = f"{type(e).__name__}: {e}"
    finally:
        terminado.set()


def esperar_fin():
    if terminado.is_set():
        resultado["exportado"] = lista.exists()
        salida.write_text(json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8")
        app.destroy()
    else:
        app.after(200, esperar_fin)


threading.Thread(target=robot, daemon=True).start()
app.after(2000, preparar)
app.after(500, esperar_fin)
app.mainloop()
print(json.dumps(resultado, indent=2, ensure_ascii=False))
sys.exit(0 if resultado.get("exportado") else 1)
