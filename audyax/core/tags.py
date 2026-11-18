"""Reading and writing tags (mutagen): title, artist, album, year, genre, cover.
Also renaming files and reading their technical data."""

import base64
import os
import re
from pathlib import Path

from yt_dlp.utils import sanitize_filename

try:
    from mutagen import File as MutagenFile
    from mutagen.flac import Picture
    from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, ID3NoHeaderError
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.oggopus import OggOpus
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False


# ==============================================================================
# TAG EDITOR (already downloaded files)
# ==============================================================================
TAG_FIELDS = ("title", "artist", "album", "year", "genre")


EDITABLE_FORMATS = (".mp3", ".m4a", ".mp4", ".opus")


_ID3 = {"title": TIT2, "artist": TPE1, "album": TALB, "year": TDRC, "genre": TCON} if HAS_MUTAGEN else {}


_MP4 = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "year": "\xa9day", "genre": "\xa9gen"}


_VORBIS = {"title": "title", "artist": "artist", "album": "album", "year": "date", "genre": "genre"}


def is_editable(path) -> bool:
    return HAS_MUTAGEN and Path(path).suffix.lower() in EDITABLE_FORMATS


def _first(value) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "")


def valid_filename(text: str, extension: str = "") -> str:
    """Turns what the user types into a valid file name (without extension):
    removes characters forbidden on Windows (\\ / : * ? " < > |), extra spaces and dots,
    and the extension if it was typed (e.g. "Canción.mp3" -> "Canción")."""
    text = (text or "").strip()
    if extension and text.lower().endswith(extension.lower()):
        text = text[: -len(extension)]
    return sanitize_filename(text).strip().rstrip(". ").strip()


def rename_file(path, new_name: str) -> Path:
    """Renames the file (the extension is kept) and returns the new path.
    Never overwrites another file: if one with that name exists, raises FileExistsError.
    It does allow changing only upper/lower case ("cancion" -> "Canción")."""
    path = Path(path)
    name = valid_filename(new_name, path.suffix)
    if not name:
        raise ValueError("El nombre del archivo no puede quedar vacío.")
    dest = path.with_name(name + path.suffix)
    # The exact text is compared: on Windows "cancion.mp3" == "Cancion.mp3" as paths, so
    # a case-only change would not be applied.
    if dest.name == path.name:
        return path
    if dest.exists() and not os.path.samefile(dest, path):
        raise FileExistsError(f"Ya existe «{dest.name}» en esa carpeta.")
    path.rename(dest)
    return dest


def read_tags(path) -> dict:
    """Title, artist, album, year and genre stored in the file."""
    path = Path(path)
    ext = path.suffix.lower()
    data = dict.fromkeys(TAG_FIELDS, "")
    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return data
        for field, klass in _ID3.items():
            container = tags.get(klass.__name__)
            if container is not None and container.text:
                data[field] = str(container.text[0])
    elif ext in (".m4a", ".mp4"):
        tags = MP4(path).tags or {}
        for field, key in _MP4.items():
            data[field] = _first(tags.get(key))
    elif ext == ".opus":
        audio = OggOpus(path)
        for field, key in _VORBIS.items():
            data[field] = _first(audio.get(key))
    else:
        raise ValueError(f"el formato {ext} no se puede editar")
    data["year"] = data["year"][:4] if re.match(r"\d{4}", data["year"]) else data["year"]
    return data


def read_cover(path):
    """Bytes of the embedded cover, or None if there is none."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            photos = ID3(path).getall("APIC")
            return bytes(photos[0].data) if photos else None
        if ext in (".m4a", ".mp4"):
            covers = (MP4(path).tags or {}).get("covr")
            return bytes(covers[0]) if covers else None
        if ext == ".opus":
            blocks = OggOpus(path).get("metadata_block_picture")
            return Picture(base64.b64decode(blocks[0])).data if blocks else None
    except Exception:
        return None
    return None


def write_tags(path, data: dict, cover: bytes = None):
    """Saves the given fields (an empty text removes the tag) and, if given,
    replaces the cover (JPEG bytes). Doesn't touch the audio or the ReplayGain."""
    if not HAS_MUTAGEN:
        raise RuntimeError("falta mutagen (pip install mutagen)")
    path = Path(path)
    ext = path.suffix.lower()
    fields = {k: (str(v).strip() if v is not None else "") for k, v in data.items()
              if k in TAG_FIELDS}

    if ext == ".mp3":
        audio = MP3(path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        for field, value in fields.items():
            klass = _ID3[field]
            audio.tags.delall(klass.__name__)
            if value:
                audio.tags.add(klass(encoding=3, text=[value]))
        if cover:
            audio.tags.delall("APIC")
            audio.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
        audio.save(v2_version=3)
    elif ext in (".m4a", ".mp4"):
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        for field, value in fields.items():
            key = _MP4[field]
            if value:
                audio.tags[key] = [value]
            elif key in audio.tags:
                del audio.tags[key]
        if cover:
            audio.tags["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
    elif ext == ".opus":
        audio = OggOpus(path)
        for field, value in fields.items():
            key = _VORBIS[field]
            if value:
                audio[key] = [value]
            elif key in audio:
                del audio[key]
        if cover:
            photo = Picture()
            photo.type, photo.mime, photo.desc, photo.data = 3, "image/jpeg", "Cover", cover
            audio["metadata_block_picture"] = [base64.b64encode(photo.write()).decode("ascii")]
        audio.save()
    else:
        raise ValueError(f"el formato {ext} no se puede editar")


def file_info(path) -> dict:
    """Technical data of a downloaded file (format, duration, bitrate, size, sample rate).
    Read-only; modifies nothing. Fields that can't be read are left empty."""
    p = Path(path)
    data = {"audio_format": p.suffix.lstrip(".").upper(), "duration": "", "bitrate": "",
             "sample_rate": "", "size": ""}
    try:
        data["size"] = f"{p.stat().st_size / 1048576:.1f} MB"
    except OSError:
        pass
    if HAS_MUTAGEN:
        try:
            audio = MutagenFile(p)
            if audio is not None and audio.info is not None:
                seg = int(getattr(audio.info, "length", 0) or 0)
                if seg:
                    data["duration"] = f"{seg // 60}:{seg % 60:02d}"
                br = getattr(audio.info, "bitrate", 0) or 0
                if br:
                    data["bitrate"] = f"{round(br / 1000)} kbps"
                sr = getattr(audio.info, "sample_rate", 0) or 0
                if sr:
                    data["sample_rate"] = f"{sr / 1000:g} kHz"
        except Exception:
            pass
    return data


def duration_seconds(path) -> int:
    """Duration in seconds (or -1 if it can't be read, as the M3U format requires)."""
    if HAS_MUTAGEN:
        try:
            audio = MutagenFile(path)
            if audio is not None and audio.info is not None:
                return int(round(audio.info.length))
        except Exception:
            pass
    return -1


def strip_track_number(path: Path):
    """Removes the track number tag from the finished file (e.g. the '63').
    Done at the end so it works wherever that value came from."""
    if not HAS_MUTAGEN:
        return
    ext = path.suffix.lower()
    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return
        if tags.getall("TRCK"):
            tags.delall("TRCK")
            tags.save(v2_version=3)
    elif ext in (".m4a", ".mp4"):
        audio = MP4(path)
        if audio.tags is not None and "trkn" in audio.tags:
            del audio.tags["trkn"]
            audio.save()
    elif ext == ".opus":
        audio = OggOpus(path)
        changed = False
        for key in ("tracknumber", "tracktotal", "totaltracks"):
            if key in audio:
                del audio[key]
                changed = True
        if changed:
            audio.save()
