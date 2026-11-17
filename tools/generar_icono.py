"""Genera assets/audyax.ico (ejecutable de Windows) y assets/audyax.png (ventana y bandeja)
a partir del mismo dibujo de nota musical que usa la interfaz.

    python tools/generar_icono.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from descargador_gui import _dibujar_icono  # noqa: E402

FONDO, NOTA = "#1a0b3b", "#a78bfa"


def icono(lado: int) -> Image.Image:
    S = 4
    n = lado * S
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, n - 1, n - 1), radius=int(n * 0.22), fill=FONDO)
    nota = _dibujar_icono("nota", NOTA, int(n * 0.62))
    im.alpha_composite(nota, ((n - nota.width) // 2, (n - nota.height) // 2 + int(n * 0.02)))
    return im.resize((lado, lado), Image.LANCZOS)


def main():
    destino = RAIZ / "assets"
    destino.mkdir(exist_ok=True)
    grande = icono(256)
    grande.save(destino / "audyax.png")
    grande.save(destino / "audyax.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Iconos generados en", destino)


if __name__ == "__main__":
    main()
