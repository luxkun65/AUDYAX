"""Colors of AUDYAX (part 2, appearance package): color schemes, the main color of a cover
(dynamic colors) and the blurred background of "Reproduciendo". No UI code here.

A palette is a dict like style.C: key -> (light mode color, dark mode color). Schemes are
made by turning the hue of the default (violet) palette, so every scheme keeps the same
lightness and contrast the design was made with."""

import colorsys

# Hue of the default accent (#7c3aed). Every scheme rotates the palette from it.
BASE_HUE = 262 / 360

# name -> target hue (0..1); None = gray ("Grafito")
SCHEMES = {
    "Violeta": BASE_HUE,
    "Azul": 221 / 360,
    "Turquesa": 182 / 360,
    "Verde": 145 / 360,
    "Ámbar": 32 / 360,
    "Rojo": 354 / 360,
    "Rosa": 325 / 360,
    "Grafito": None,
}
DEFAULT_SCHEME = "Violeta"
KEEP = {"red"}            # colors with a meaning of their own (errors) don't change


def hex_to_rgb(color: str):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_to_hex(rgb) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def _shift(color: str, hue_delta: float, saturation: float = 1.0) -> str:
    h, l, s = colorsys.rgb_to_hls(*hex_to_rgb(color))
    if s < 0.04:                       # grays stay gray
        return color
    return rgb_to_hex(colorsys.hls_to_rgb((h + hue_delta) % 1.0, l, min(1.0, s * saturation)))


def palette_for_hue(base: dict, hue, saturation: float = 1.0) -> dict:
    """`base` turned to `hue` (0..1). hue=None makes it gray (saturation 8 %)."""
    if hue is None:
        delta, saturation = 0.0, 0.08
    else:
        delta = (hue - BASE_HUE) % 1.0
    out = {}
    for key, value in base.items():
        if key in KEEP:
            out[key] = value
        elif isinstance(value, (tuple, list)):
            out[key] = tuple(_shift(v, delta, saturation) for v in value)
        else:
            out[key] = _shift(value, delta, saturation)
    return out


def scheme_palette(base: dict, scheme: str) -> dict:
    return palette_for_hue(base, SCHEMES.get(scheme, BASE_HUE))


def cover_hue(image):
    """Hue (0..1) of the most visible lively color of a cover, or None if it's gray/black/white.
    Each pixel counts by how saturated and bright it is; the busiest hue range wins."""
    if image is None:
        return None
    small = image.convert("RGB").resize((48, 48))
    bins = [0.0] * 36
    sums = [0.0] * 36
    raw = small.tobytes()
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.25 or v < 0.2:
            continue
        weight = s * v
        k = int(h * 36) % 36
        bins[k] += weight
        sums[k] += h * weight
    total = sum(bins)
    if total < 48 * 48 * 0.03:          # almost no color: black and white cover
        return None
    k = max(range(36), key=lambda i: bins[i] + 0.5 * (bins[i - 1] + bins[(i + 1) % 36]))
    return sums[k] / bins[k] if bins[k] else k / 36


def blurred_background(image, size, blur: float = 40, dim: float = 0.42):
    """The cover (or any image) filling `size` (w, h), very blurred and darkened, for the
    background of "Reproduciendo". With blur=0 the picture stays sharp (own background)."""
    from PIL import Image, ImageEnhance, ImageFilter
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    img = image.convert("RGB")
    scale = max(w / img.width, h / img.height)               # cover the area, crop the rest
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    img = img.crop((left, top, left + w, top + h))
    if blur:
        small = img.resize((max(1, w // 8), max(1, h // 8)), Image.BILINEAR)   # blur fast at 1/8
        small = small.filter(ImageFilter.GaussianBlur(blur / 8))
        img = small.resize((w, h), Image.BILINEAR)
    return ImageEnhance.Brightness(img).enhance(dim)


def dynamic_background(cover, size):
    """Background made of the cover's color (not the picture): a deep diagonal gradient of
    its main hue, dark enough for light text. Gray covers give a gradient of their average
    color. Changes with every song."""
    from PIL import Image
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    hue = cover_hue(cover)
    if hue is not None:
        top = colorsys.hls_to_rgb(hue, 0.30, 0.62)
        bottom = colorsys.hls_to_rgb((hue + 0.04) % 1.0, 0.09, 0.55)
    else:
        r, g, b = hex_to_rgb(mean_color(cover)) if cover is not None else (0.2, 0.2, 0.22)
        h0, l0, s0 = colorsys.rgb_to_hls(r, g, b)
        top = colorsys.hls_to_rgb(h0, min(0.30, max(0.18, l0)), s0)
        bottom = colorsys.hls_to_rgb(h0, 0.07, s0)
    # small gradient, scaled up: smooth and fast at any size
    gw, gh = 64, max(2, min(64, round(64 * h / w)) if w >= h else 64)
    small = Image.new("RGB", (gw, gh))
    px = small.load()
    for y in range(gh):
        for x in range(gw):
            t = min(1.0, (x / max(1, gw - 1)) * 0.55 + (y / max(1, gh - 1)) * 0.45)
            px[x, y] = tuple(round(255 * (a + (b - a) * t)) for a, b in zip(top, bottom))
    return small.resize((w, h), Image.BILINEAR)


def mean_color(image) -> str:
    """Average color of an image, as "#rrggbb" (for panels on top of the background)."""
    r, g, b = image.convert("RGB").resize((1, 1), 4).getpixel((0, 0))   # 4 = BOX filter
    return rgb_to_hex((r / 255, g / 255, b / 255))


def lighten(color: str, amount: float) -> str:
    """Moves a color towards white (amount > 0) or black (amount < 0)."""
    r, g, b = hex_to_rgb(color)
    target = 1.0 if amount > 0 else 0.0
    a = abs(amount)
    return rgb_to_hex((r + (target - r) * a, g + (target - g) * a, b + (target - b) * a))


def readable_accent(hue) -> str:
    """A bright color of `hue` that reads well on a dark background."""
    if hue is None:
        return "#e9e4f5"
    return rgb_to_hex(colorsys.hls_to_rgb(hue, 0.72, 0.85))
