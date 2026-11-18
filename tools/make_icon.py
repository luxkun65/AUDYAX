"""Generates assets/audyax.ico (Windows executable) and assets/audyax.png (window, tray and
sidebar) from the logo emblem in docs/brand/emblem.png (the "A" with the note, on black).

    python tools/make_icon.py
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
EMBLEM = ROOT / "docs" / "brand" / "emblem.png"
BACKGROUND = np.array([12, 5, 28.0])     # dark purple, like the app's dark theme
GLOW = np.array([46, 14, 92.0])          # soft purple glow behind the emblem


def emblem() -> Image.Image:
    """The emblem with a transparent background (its background is pure black)."""
    a = np.array(Image.open(EMBLEM).convert("RGB")).astype(float)
    alpha = np.clip(a.max(axis=2) / 70.0, 0, 1)
    color = np.clip(a / np.maximum(alpha[..., None], 1e-6), 0, 255)   # "un-blend" the soft edges
    img = Image.fromarray(np.dstack([color, alpha * 255]).astype(np.uint8), "RGBA")
    return img.crop(img.getbbox())


def icon(side: int = 1024, fill: float = 0.90) -> Image.Image:
    """Rounded square with the emblem in the middle."""
    yy, xx = np.mgrid[0:side, 0:side]
    r = np.hypot(xx - side / 2, yy - side / 2) / (side / 2)
    background = BACKGROUND + (np.clip(1 - r, 0, 1) ** 2)[..., None] * GLOW
    img = Image.fromarray(background.astype(np.uint8)).convert("RGBA")
    mark = emblem()
    scale = side * fill / mark.width
    mark = mark.resize((int(mark.width * scale), int(mark.height * scale)), Image.LANCZOS)
    img.alpha_composite(mark, ((side - mark.width) // 2, (side - mark.height) // 2 + int(side * 0.02)))
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, side - 1, side - 1), radius=int(side * 0.2), fill=255)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def main():
    dest = ROOT / "assets"
    dest.mkdir(exist_ok=True)
    big = icon()
    big.resize((256, 256), Image.LANCZOS).save(dest / "audyax.png")
    big.save(dest / "audyax.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Icons generated in", dest)


if __name__ == "__main__":
    main()
