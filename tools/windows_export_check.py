"""
UI test on Windows (run by GitHub Actions): opens AUDYAX and, with REAL mouse clicks,
presses "Exportar" and then "Lista de reproducción (.m3u)"; checks that it was exported.

It exists because the Export popup menu didn't appear on some Windows computers: this kind
of failure only shows up on Windows and with real clicks.

    python tools/windows_export_check.py result.json
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pyautogui


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audyax.core import config  # noqa: E402
from audyax.gui import app as g  # noqa: E402

config.CONFIG_PATH = Path(tempfile.gettempdir()) / "audyax_export_check.json"

pyautogui.FAILSAFE = False
output = Path(sys.argv[1])
folder = Path(tempfile.mkdtemp(prefix="audyax_menu_"))
song = folder / "Uno.mp3"
subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                "-c:a", "libmp3lame", str(song)], check=True)
m3u_path = folder / "lista.m3u"

result = {"messages": []}
g.filedialog.asksaveasfilename = lambda **k: str(m3u_path)       # without the native dialog
g.messagebox.showinfo = lambda t, msg, **k: result["messages"].append(msg)
g.messagebox.showerror = lambda t, msg, **k: result["messages"].append("ERROR: " + msg)

app = g.App()
app.minsize(800, 500)
app.geometry("1000x700+0+0")
app._handle(("new", "v1", {"title": "Uno", "url": "u", "thumbnail": "", "subfolder": ""}))
app._handle(("done", "v1", {"title": "Uno", "artist": "A", "album": "", "year": "", "genre": "",
                              "url": "u", "path": str(song)}))
request_info = {"center": None}           # the robot asks where to click; the window tells it
ready = threading.Event()      # the window tells the robot where to click
finished = threading.Event()   # the check is over


def center(widget):
    widget.update_idletasks()
    return (widget.winfo_rootx() + widget.winfo_width() // 2,
            widget.winfo_rooty() + widget.winfo_height() // 2)


def prepare():
    app.lift()
    app.focus_force()
    app.update()
    request_info["center"] = center(app.btn_export)
    ready.set()


def find_dialog(attempts=[0]):
    v = app.export_window
    if v is not None and v.winfo_exists() and v.winfo_ismapped():
        result["dialog_opened"] = True
        request_info["center"] = center(v.btn_m3u)
        ready.set()
    elif attempts[0] < 25:
        attempts[0] += 1
        app.after(200, find_dialog)
    else:
        result["dialog_opened"] = False
        finished.set()


def capture():
    try:
        pyautogui.screenshot(str(output.with_suffix(".png")))
    except Exception:  # on Linux it needs extra tools; the screenshot is only a help
        pass


def robot():
    try:
        ready.wait(30)
        time.sleep(1)
        ready.clear()
        pyautogui.click(*request_info["center"])                 # click on "Exportar"
        time.sleep(1)
        app.after(0, find_dialog)
        if not ready.wait(10):
            capture()
            return
        capture()
        pyautogui.click(*request_info["center"])                 # click on "Lista de reproducción (.m3u)"
        time.sleep(2)
    except Exception as e:
        result["error_robot"] = f"{type(e).__name__}: {e}"
    finally:
        finished.set()


def wait_end():
    if finished.is_set():
        result["exported"] = m3u_path.exists()
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        app.destroy()
    else:
        app.after(200, wait_end)


threading.Thread(target=robot, daemon=True).start()
app.after(2000, prepare)
app.after(500, wait_end)
app.mainloop()
print(json.dumps(result, indent=2, ensure_ascii=False))
sys.exit(0 if result.get("exported") else 1)
