"""
Checks the result of `AUDYAX --self-test result.json` (used by GitHub Actions).

    python tools/check_startup.py result.json [--bundled-ffmpeg]

Fails (exit code 1) if something essential is missing.
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
    if "--bundled-ffmpeg" in sys.argv:
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
