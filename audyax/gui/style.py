"""Colors, fonts, icons and image helpers shared by every window."""

import math
import os
import subprocess
import sys
import tkinter as tk
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image, ImageDraw

# ==============================================================================
# PALETTE (each color is a tuple: (light mode, dark mode))
# ==============================================================================
C = {
    "bg": ("#f4f1fa", "#0f0a1a"), "sidebar": ("#e9e3f5", "#0b0714"),
    "card": ("#ffffff", "#1a1228"), "card2": ("#ece6f7", "#271a3d"),
    "input": ("#f7f4fc", "#130d20"), "border": ("#d9cfee", "#33264d"),
    "row_a": ("#ffffff", "#160f24"), "row_b": ("#f7f4fc", "#1c132d"),
    "sel": ("#e0d3fa", "#5b34a8"), "nav_active": ("#e0d3fa", "#3b2268"),
    "accent": ("#7c3aed", "#a78bfa"), "accent_hover": ("#6d28d9", "#c4b5fd"),
    "text": ("#1e1530", "#efeaf8"), "muted": ("#6e6285", "#a095bd"),
    "on_accent": ("#ffffff", "#1a0b3b"), "hover": ("#e4dbf5", "#35264f"),
    "red": ("#d63c2c", "#e74c3c"),
}


STATUS_COLORS = {"green": "#2ecc71", "yellow": "#f1c40f", "red": "#e74c3c"}


# ==============================================================================
# GRAPHICS HELPERS (Pillow)
# ==============================================================================
def tk_safe(text) -> str:
    """Old Tk doesn't support some emojis; they are replaced to avoid errors."""
    return "".join(c if ord(c) <= 0xFFFF else "□" for c in str(text))


def shorten(text, n: int) -> str:
    t = tk_safe(text)
    return t if len(t) <= n else t[: n - 1] + "…"


def _draw_icon(name: str, color: str, size: int) -> Image.Image:
    """Draws a simple line icon, at 4x size so it comes out smooth."""
    S = 4
    n = size * S
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w = max(2, round(n * 0.09))

    def pt(x, y):
        return (x * n, y * n)

    def line(points, width=w, c=color):
        d.line([pt(x, y) for x, y in points], fill=c, width=width, joint="curve")

    def box(x0, y0, x1, y1):
        return (x0 * n, y0 * n, x1 * n, y1 * n)

    if name == "home":
        d.polygon([pt(.5, .1), pt(.95, .52), pt(.8, .52), pt(.8, .9), pt(.6, .9), pt(.6, .62),
                   pt(.4, .62), pt(.4, .9), pt(.2, .9), pt(.2, .52), pt(.05, .52)], fill=color)
    elif name == "gear":
        c = n / 2
        for k in range(8):
            a = math.radians(k * 45)
            d.line([(c + math.cos(a) * n * .28, c + math.sin(a) * n * .28),
                    (c + math.cos(a) * n * .44, c + math.sin(a) * n * .44)],
                   fill=color, width=int(n * .16))
        d.ellipse(box(.2, .2, .8, .8), fill=color)
        d.ellipse(box(.38, .38, .62, .62), fill=(0, 0, 0, 0))
    elif name == "info":
        d.ellipse(box(.08, .08, .92, .92), outline=color, width=w)
        d.ellipse(box(.45, .24, .55, .34), fill=color)
        line([(.5, .45), (.5, .72)], w + 1)
    elif name == "link":
        d.rounded_rectangle(box(.04, .32, .58, .68), radius=n * .18, outline=color, width=w)
        d.rounded_rectangle(box(.42, .32, .96, .68), radius=n * .18, outline=color, width=w)
    elif name == "folder":
        line([(.06, .24), (.38, .24), (.48, .34), (.94, .34), (.94, .84), (.06, .84), (.06, .24)])
    elif name == "download":
        line([(.5, .1), (.5, .62)])
        line([(.28, .42), (.5, .64), (.72, .42)])
        line([(.14, .72), (.14, .88), (.86, .88), (.86, .72)])
    elif name == "cancel":
        d.ellipse(box(.1, .1, .9, .9), outline=color, width=w)
        line([(.22, .78), (.78, .22)])
    elif name == "copy":
        d.rounded_rectangle(box(.3, .3, .9, .92), radius=n * .08, outline=color, width=w)
        line([(.18, .68), (.18, .1), (.62, .1)])
    elif name == "external":
        d.rounded_rectangle(box(.08, .28, .72, .92), radius=n * .08, outline=color, width=w)
        line([(.45, .55), (.9, .1)])
        line([(.6, .1), (.9, .1), (.9, .4)])
    elif name == "refresh":
        d.arc(box(.14, .14, .86, .86), start=60, end=350, fill=color, width=w)
        d.polygon([pt(.66, .42), pt(1.0, .42), pt(.83, .7)], fill=color)
    elif name == "trash":
        line([(.12, .24), (.88, .24)])
        line([(.36, .24), (.36, .1), (.64, .1), (.64, .24)])
        line([(.24, .34), (.30, .9), (.70, .9), (.76, .34)])
        line([(.42, .45), (.42, .78)])
        line([(.58, .45), (.58, .78)])
    elif name == "search":
        d.ellipse(box(.1, .1, .64, .64), outline=color, width=w)
        line([(.58, .58), (.9, .9)], w + 1)
    elif name == "edit":
        d.polygon([pt(.18, .66), pt(.66, .18), pt(.82, .34), pt(.34, .82)], outline=color, width=w)
        d.polygon([pt(.12, .88), pt(.18, .66), pt(.34, .82)], fill=color)
    elif name == "pause":
        d.rounded_rectangle(box(.2, .14, .4, .86), radius=n * .04, fill=color)
        d.rounded_rectangle(box(.6, .14, .8, .86), radius=n * .04, fill=color)
    elif name == "play":
        d.polygon([pt(.26, .12), pt(.86, .5), pt(.26, .88)], fill=color)
    elif name == "export":
        line([(.5, .62), (.5, .1)])
        line([(.28, .32), (.5, .1), (.72, .32)])
        line([(.14, .6), (.14, .88), (.86, .88), (.86, .6)])
    elif name == "note":
        d.ellipse(box(.08, .62, .42, .92), fill=color)
        d.ellipse(box(.56, .52, .9, .82), fill=color)
        d.polygon([pt(.36, .76), pt(.36, .18), pt(.84, .08), pt(.84, .66), pt(.76, .66),
                   pt(.76, .3), pt(.44, .37), pt(.44, .76)], fill=color)
    elif name in ("ok", "clock", "err"):
        base = {"ok": STATUS_COLORS["green"], "clock": STATUS_COLORS["yellow"], "err": STATUS_COLORS["red"]}[name]
        d.ellipse(box(.04, .04, .96, .96), fill=base)
        if name == "ok":
            line([(.28, .52), (.44, .68), (.74, .34)], w + 1, "#ffffff")
        elif name == "clock":
            line([(.5, .24), (.5, .5), (.7, .62)], w, "#5a4500")
        else:
            line([(.33, .33), (.67, .67)], w + 1, "#ffffff")
            line([(.67, .33), (.33, .67)], w + 1, "#ffffff")
    elif name == "dot":
        d.ellipse(box(.05, .05, .95, .95), fill=color)
    elif name == "eye":
        d.chord(box(.04, .22, .96, 1.18), start=200, end=340, outline=color, width=w)
        d.chord(box(.04, -.18, .96, .78), start=20, end=160, outline=color, width=w)
        d.ellipse(box(.36, .36, .64, .64), fill=color)
    return im.resize((size, size), Image.LANCZOS)


def icon(name: str, size: int = 18, color=None) -> ctk.CTkImage:
    """Icon that switches color between light and dark mode by itself."""
    color = color or C["text"]
    if isinstance(color, str):
        color = (color, color)
    return ctk.CTkImage(light_image=_draw_icon(name, color[0], size * 2),
                        dark_image=_draw_icon(name, color[1], size * 2), size=(size, size))


def rounded_thumbnail(img: Image.Image, side_px: int, radius: int) -> Image.Image:
    """Crops the center into a square and rounds the corners."""
    S = 3
    img = img.convert("RGB")
    w, h = img.size
    m = min(w, h)
    x0, y0 = (w - m) // 2, (h - m) // 2
    img = img.crop((x0, y0, x0 + m, y0 + m)).resize((side_px * S, side_px * S), Image.LANCZOS)
    mask = Image.new("L", (side_px * S, side_px * S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, side_px * S - 1, side_px * S - 1),
                                              radius=radius * S, fill=255)
    img = img.convert("RGBA")
    img.putalpha(mask)
    return img.resize((side_px, side_px), Image.LANCZOS)


def square_image(img: Image.Image, side_px: int, radius: int) -> ctk.CTkImage:
    p = rounded_thumbnail(img, side_px * 2, radius * 2)
    return ctk.CTkImage(light_image=p, dark_image=p, size=(side_px, side_px))


def empty_image(side_px: int) -> ctk.CTkImage:
    """Gray square shown while the real thumbnail arrives."""
    light = rounded_thumbnail(Image.new("RGB", (8, 8), "#d9cfee"), side_px * 2, 12)
    dark = rounded_thumbnail(Image.new("RGB", (8, 8), "#33264d"), side_px * 2, 12)
    return ctk.CTkImage(light_image=light, dark_image=dark, size=(side_px, side_px))


def equalizer_image() -> ctk.CTkImage:
    """Sidebar ornament: purple blocks that fade out upwards."""
    width, tall, S = 96, 100, 2
    im = Image.new("RGBA", (width * S, tall * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cell_px, gap = 14 * S, 4 * S
    heights = [4, 6, 3, 5]
    total = len(heights) * cell_px + (len(heights) - 1) * gap
    x0 = (width * S - total) // 2
    for c, h in enumerate(heights):
        for r in range(h):
            alpha = int(255 * (0.9 - 0.7 * r / max(1, h - 1)))
            x = x0 + c * (cell_px + gap)
            y = tall * S - (r + 1) * (cell_px + gap) + gap
            d.rounded_rectangle((x, y, x + cell_px, y + cell_px), radius=3 * S, fill=(167, 139, 250, alpha))
    return ctk.CTkImage(light_image=im, dark_image=im, size=(width, tall))


def cover_jpeg(img: Image.Image, square: bool, max_side: int = 1200) -> bytes:
    """Turns any image into the JPEG cover embedded in the file."""
    img = img.convert("RGB")
    if square:
        w, h = img.size
        m = min(w, h)
        img = img.crop(((w - m) // 2, (h - m) // 2, (w - m) // 2 + m, (h - m) // 2 + m))
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    output = BytesIO()
    img.save(output, "JPEG", quality=90)
    return output.getvalue()


def resource_path(name: str) -> Path:
    """File in the assets/ folder, both when running with Python and from the .exe."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))  # project root
    return base / "assets" / name


def fetch_bytes(url: str, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def open_path(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        messagebox.showerror("No se pudo abrir", str(e))


def choose_font(root) -> str:
    families = set(tkfont.families(root))
    for f in ("Segoe UI", "SF Pro Text", "Helvetica Neue", "Ubuntu", "DejaVu Sans"):
        if f in families:
            return f
    return tkfont.nametofont("TkDefaultFont").actual("family")


def release_variable(var: tk.Variable):
    """Releases a tkinter variable that is no longer used, right now, on the main thread.
    Otherwise Python frees it wherever its last reference drops, sometimes on a download
    thread, and tkinter prints "main thread is not in main loop" to the console."""
    try:
        if var._tk is not None:
            var._tk.globalunsetvar(var._name)
            for name in var._tclCommands or ():
                var._tk.deletecommand(name)
            var._tclCommands = None
    except (tk.TclError, AttributeError):
        pass
    var._tk = None  # so its __del__ no longer does anything
