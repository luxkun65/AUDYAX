"""Where everything goes in "Reproduciendo", for each layout. Pure geometry (no tkinter), so
it can be tested without a screen.

Layouts:
  balanced  cover on the left, lyrics on the right (the default)
  cover     big cover, lyrics in a narrow column
  lyrics    big lyrics, small cover with the song data on top
  no_lyrics only the cover and the song data, centered
"""

LAYOUTS = {"balanced": "Equilibrado", "cover": "Portada grande", "lyrics": "Letra grande",
           "no_lyrics": "Sin letra"}
DEFAULT_LAYOUT = "balanced"

PAD = 28          # margin around everything
TOP = 56          # row at the top for the layout buttons
GAP = 28          # space between the cover column and the lyrics
CONTROLS = 104    # strip at the bottom with the playback controls (they replace the player bar here)


def compute(layout: str, w: int, h: int, has_next: bool = True) -> dict:
    """Rectangles (x0, y0, x1, y1) and font sizes for a view of w × h pixels.
    Keys: cover (x, y, size), info (x, y, width, anchor "nw"|"n"), next (x, y, width, rows) or
    None, lyrics (x0, y0, x1, y1) or None, controls (x0, y0, x1, y1) — the playback controls at
    the bottom —, fonts {title, artist, album, lyric, current, small}."""
    layout = layout if layout in LAYOUTS else DEFAULT_LAYOUT
    w, h = max(w, 320), max(h, 240 + CONTROLS)
    out = _compute(layout, w, h - CONTROLS, has_next)
    out["controls"] = (PAD, h - CONTROLS, w - PAD, h - 12)
    return out


def _compute(layout, w, h, has_next):
    inner_h = h - TOP - PAD
    if layout == "lyrics":
        size = max(64, min(132, inner_h // 4))
        info_x = PAD + size + 20
        lyrics = (PAD, TOP + size + 24, w - PAD, h - PAD)
        return {"cover": (PAD, TOP, size),
                "info": (info_x, TOP + 6, max(120, w - info_x - PAD), "nw"),
                "next": None, "lyrics": lyrics,
                "fonts": {"title": 20, "artist": 14, "album": 11, "lyric": 18, "current": 26, "small": 10}}
    if layout == "no_lyrics":
        size = max(120, min(inner_h - 150, w - 2 * PAD, 560))
        x = (w - size) // 2
        return {"cover": (x, TOP, size), "info": (w // 2, TOP + size + 18, min(w - 2 * PAD, 700), "n"),
                "next": None, "lyrics": None,
                "fonts": {"title": 24, "artist": 16, "album": 12, "lyric": 14, "current": 19, "small": 10}}
    if layout == "cover":
        size = max(140, min(inner_h - 140, int((w - 2 * PAD - GAP) * 0.62)))
        lyrics_x0 = PAD + size + GAP
        lyrics = (lyrics_x0, TOP, w - PAD, h - PAD) if w - PAD - lyrics_x0 >= 160 else None
        return {"cover": (PAD, TOP, size), "info": (PAD, TOP + size + 16, size, "nw"),
                "next": None, "lyrics": lyrics,
                "fonts": {"title": 22, "artist": 15, "album": 11, "lyric": 12, "current": 15, "small": 10}}
    # balanced
    size = max(120, min(inner_h - (250 if has_next else 140), int(w * 0.32), 380))
    lyrics_x0 = PAD + size + GAP
    lyrics = (lyrics_x0, TOP, w - PAD, h - PAD) if w - PAD - lyrics_x0 >= 200 else None
    info_y = TOP + size + 16
    next_y = info_y + 120
    rows = max(0, (h - PAD - next_y - 26) // 30)
    return {"cover": (PAD, TOP, size), "info": (PAD, info_y, size, "nw"),
            "next": (PAD, next_y, size, min(5, rows)) if has_next and rows > 0 else None,
            "lyrics": lyrics,
            "fonts": {"title": 20, "artist": 14, "album": 11, "lyric": 14, "current": 19, "small": 10}}


def lyric_scroll(target_center: float, line_center: float, current_offset: float, ease: float = 0.25) -> float:
    """Next scroll offset of the lyrics: moves a part of the way towards having the current
    line at `target_center` (smooth scrolling). Returns the new offset."""
    goal = target_center - line_center
    step = (goal - current_offset) * ease
    return goal if abs(goal - current_offset) < 0.5 else current_offset + step
