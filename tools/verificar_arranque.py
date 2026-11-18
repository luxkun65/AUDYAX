"""
Revisa el resultado de `AUDYAX --prueba-arranque resultado.json` (lo usa GitHub Actions).

    python tools/verificar_arranque.py resultado.json [--ffmpeg-incluido]

Falla (código 1) si falta algo imprescindible.
"""
import json
import sys
from pathlib import Path


def main():
    datos = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(datos, indent=2, ensure_ascii=False))
    errores = []
    for clave in ("ejecutable", "ffmpeg_funciona", "mutagen", "ytmusicapi", "icono", "notificaciones",
                  "bandeja", "arrastrar_soltar", "audio_libreria"):
        if not datos.get(clave):
            errores.append(f"{clave} = {datos.get(clave)!r}")
    if "--ffmpeg-incluido" in sys.argv:
        for clave in ("ffmpeg", "ffprobe"):
            ruta = str(datos.get(clave) or "")
            if "AUDYAX" not in ruta or "ffmpeg" not in ruta.lower():
                errores.append(f"{clave} no es el incluido en el programa: {ruta!r}")
    if errores:
        print("\nFALTA:\n  " + "\n  ".join(errores))
        sys.exit(1)
    print("\nEl ejecutable arranca y trae todo lo necesario.")


if __name__ == "__main__":
    main()
