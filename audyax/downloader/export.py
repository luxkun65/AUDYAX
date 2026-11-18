"""Exporting the song list: .m3u playlist and .csv table."""

import csv
import os
from pathlib import Path

from audyax.core.tags import duration_seconds

# ==============================================================================
# EXPORT THE LIST (.m3u and .csv)
# ==============================================================================
CSV_COLUMNS = [("num", "#"), ("status", "Estado"), ("title", "Título"), ("artist", "Artista"),
                ("album", "Álbum"), ("year", "Año"), ("genre", "Género"), ("url", "Enlace"),
                ("path", "Archivo"), ("reason", "Motivo")]


def export_m3u(dest, songs) -> int:
    """Writes an extended M3U playlist (UTF-8) with the songs that exist.
    Paths are relative to the playlist's folder when possible, so the playlist keeps
    working if the whole music folder is moved. Returns how many songs it wrote."""
    dest = Path(dest)
    base = dest.parent.resolve()
    lines = ["#EXTM3U"]
    for c in songs:
        path = Path(c.get("path") or "")
        if not c.get("path") or not path.is_file():
            continue
        try:
            path_text = os.path.relpath(path.resolve(), base)
        except ValueError:  # another drive on Windows (C: vs D:)
            path_text = str(path.resolve())
        name = " - ".join(x for x in (c.get("artist"), c.get("title")) if x) or path.stem
        lines.append(f"#EXTINF:{duration_seconds(path)},{name}")
        lines.append(path_text)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return (len(lines) - 1) // 2


def _csv_cell(value) -> str:
    text = "" if value is None else str(value)
    # Prevents Excel from treating a title like "=…" as a formula
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_csv(dest, rows) -> int:
    """Writes the list as CSV. Uses ';' and UTF-8 with BOM so Excel in Spanish opens it
    directly in columns and with accents. Returns how many rows it wrote."""
    with open(dest, "w", encoding="utf-8-sig", newline="") as f:
        csv_writer = csv.writer(f, delimiter=";")
        csv_writer.writerow([title for _, title in CSV_COLUMNS])
        for row in rows:
            csv_writer.writerow([_csv_cell(row.get(key, "")) for key, _ in CSV_COLUMNS])
    return len(rows)
