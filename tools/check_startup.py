"""
Revisa el resultado de `AUDYAX --prueba-arranque resultado.json` (lo usa GitHub Actions).

    python tools/verificar_arranque.py resultado.json [--ffmpeg-incluido]

Falla (código 1) si falta algo imprescindible.
"""
import json
import sys
from pathlib import Path


def main():
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2, ensure_ascii=False))
    errors = []
    for key in ("executable", "ffmpeg_works", "mutagen", "ytmusicapi", "icon", "notifications",
                  "tray", "drag_and_drop", "audio_library"):
        if not data.get(key):
            errors.append(f"{key} = {data.get(key)!r}")
    if "--ffmpeg-incluido" in sys.argv:
        for key in ("ffmpeg", "ffprobe"):
            path = str(data.get(key) or "")
            if "AUDYAX" not in path or "ffmpeg" not in path.lower():
                errors.append(f"{key} no es el incluido en el programa: {path!r}")
    if errors:
        print("\nFALTA:\n  " + "\n  ".join(errors))
        sys.exit(1)
    print("\nEl ejecutable arranca y trae todo lo necesario.")


if __name__ == "__main__":
    main()
