"""
Prueba de interfaz en Windows (la corre GitHub Actions): abre AUDYAX, hace clic DE VERDAD
con el mouse en "Exportar" y luego en la primera opción del menú, y comprueba que se exportó.

Existe porque el menú de Exportar no aparecía en Windows (tk_popup) aunque en Linux sí:
este tipo de fallo solo se ve en Windows y con clics reales.

    python tools/prueba_menu_windows.py resultado.json [--antes]

--antes: usa la forma vieja de abrir el menú (para comprobar que la prueba sí detecta el fallo).
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

resultado = {"variante": "antes" if "--antes" in sys.argv else "despues", "mensajes": []}
g.filedialog.asksaveasfilename = lambda **k: str(lista)       # sin el diálogo nativo
g.messagebox.showinfo = lambda t, msg, **k: resultado["mensajes"].append(msg)
g.messagebox.showerror = lambda t, msg, **k: resultado["mensajes"].append("ERROR: " + msg)

if "--antes" in sys.argv:
    def menu_viejo(self):
        menu = self._nuevo_menu()
        menu.add_command(label="m3u", command=self._exportar_m3u)
        menu.add_command(label="csv", command=self._exportar_csv)
        b = self.btn_exportar
        menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())
    g.App._menu_exportar = menu_viejo

app = g.App()
app.minsize(800, 500)
app.geometry("1000x700+0+0")
app._manejar(("nuevo", "v1", {"titulo": "Uno", "url": "u", "miniatura": "", "subcarpeta": ""}))
app._manejar(("listo", "v1", {"titulo": "Uno", "artista": "A", "album": "", "anio": "", "genero": "",
                              "url": "u", "ruta": str(cancion)}))
coords, listo, terminado = {}, threading.Event(), threading.Event()


def preparar():
    app.lift()
    app.focus_force()
    app.update()
    b = app.btn_exportar
    coords.update(x=b.winfo_rootx() + b.winfo_width() // 2, y=b.winfo_rooty() + b.winfo_height() // 2,
                  izq=b.winfo_rootx(), abajo=b.winfo_rooty() + b.winfo_height())
    listo.set()


def robot():
    listo.wait(30)
    time.sleep(1)
    pyautogui.click(coords["x"], coords["y"])               # clic en "Exportar"
    time.sleep(1.5)
    pyautogui.screenshot(str(salida.with_suffix(".menu.png")))
    pyautogui.click(coords["izq"] + 60, coords["abajo"] + 14)  # primera opción del menú
    time.sleep(2)
    pyautogui.screenshot(str(salida.with_suffix(".despues.png")))
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
sys.exit(0 if resultado.get("exportado") or "--antes" in sys.argv else 1)
