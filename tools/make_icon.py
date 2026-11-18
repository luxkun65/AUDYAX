"""Genera assets/audyax.ico (ejecutable de Windows) y assets/audyax.png (ventana y bandeja)
a partir del mismo dibujo de nota musical que usa la interfaz.

    python tools/generar_icono.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gui_app import _draw_icon  # noqa: E402

BACKGROUND, NOTE = "#1a0b3b", "#a78bfa"


def icon(side_px: int) -> Image.Image:
    S = 4
    n = side_px * S
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, n - 1, n - 1), radius=int(n * 0.22), fill=BACKGROUND)
    note = _draw_icon("note", NOTE, int(n * 0.62))
    im.alpha_composite(note, ((n - note.width) // 2, (n - note.height) // 2 + int(n * 0.02)))
    return im.resize((side_px, side_px), Image.LANCZOS)


def main():
    dest = ROOT / "assets"
    dest.mkdir(exist_ok=True)
    big = icon(256)
    big.save(dest / "audyax.png")
    big.save(dest / "audyax.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Iconos generados en", dest)


if __name__ == "__main__":
    main()
