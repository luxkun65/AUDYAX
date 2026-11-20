"""Colors (audyax.core.colors): schemes, cover color, blurred background."""
import colorsys

from PIL import Image

from audyax.core import colors

BASE = {"accent": ("#7c3aed", "#a78bfa"), "bg": ("#f4f1fa", "#0f0a1a"), "red": ("#d63c2c", "#e74c3c"),
        "gray": ("#808080", "#202020"), "single": "#7c3aed"}


def hue(color):
    return colorsys.rgb_to_hls(*colors.hex_to_rgb(color))[0]


def test_hex_round_trip():
    assert colors.rgb_to_hex(colors.hex_to_rgb("#7c3aed")) == "#7c3aed"


def test_default_scheme_changes_nothing():
    pal = colors.scheme_palette(BASE, "Violeta")
    assert pal["accent"] == BASE["accent"] and pal["bg"] == BASE["bg"]


def test_schemes_turn_the_hue_and_keep_lightness():
    pal = colors.scheme_palette(BASE, "Verde")
    assert abs(hue(pal["accent"][0]) - 145 / 360) < 0.01
    l_old = colorsys.rgb_to_hls(*colors.hex_to_rgb(BASE["accent"][1]))[1]
    l_new = colorsys.rgb_to_hls(*colors.hex_to_rgb(pal["accent"][1]))[1]
    assert abs(l_old - l_new) < 0.01
    assert pal["red"] == BASE["red"] and pal["gray"] == BASE["gray"]     # errors and grays stay
    assert isinstance(pal["single"], str)
    assert set(pal) == set(BASE)
    assert len(set(colors.scheme_palette(BASE, s)["accent"] for s in colors.SCHEMES)) == len(colors.SCHEMES)


def test_graphite_is_almost_gray():
    pal = colors.scheme_palette(BASE, "Grafito")
    s = colorsys.rgb_to_hls(*colors.hex_to_rgb(pal["accent"][0]))[2]
    assert s < 0.1


def test_unknown_scheme_is_the_default():
    assert colors.scheme_palette(BASE, "no existe") == colors.scheme_palette(BASE, "Violeta")


def test_cover_hue():
    red = Image.new("RGB", (100, 100), "#d62828")
    assert abs(colors.cover_hue(red) - 0) < 0.03 or colors.cover_hue(red) > 0.97
    mixed = Image.new("RGB", (100, 100), "#111111")            # mostly black with a blue square
    mixed.paste(Image.new("RGB", (40, 40), "#1e6fd9"), (30, 30))
    assert abs(colors.cover_hue(mixed) - hue("#1e6fd9")) < 0.03
    assert colors.cover_hue(Image.new("RGB", (50, 50), "#777777")) is None
    assert colors.cover_hue(None) is None


def test_blurred_background_size_and_darkness():
    img = Image.new("RGB", (300, 300), "#ffffff")
    bg = colors.blurred_background(img, (800, 450))
    assert bg.size == (800, 450)
    assert max(bg.getpixel((400, 225))) < 130                   # darkened
    sharp = colors.blurred_background(Image.new("RGB", (40, 20), "#336699"), (100, 100), blur=0, dim=1)
    assert sharp.size == (100, 100) and sharp.getpixel((50, 50)) == (0x33, 0x66, 0x99)


def test_mean_lighten_and_accent():
    assert colors.mean_color(Image.new("RGB", (10, 10), "#204060")) == "#204060"
    assert colors.lighten("#000000", 0.5) == "#808080"
    assert colors.lighten("#ffffff", -1) == "#000000"
    assert colors.readable_accent(None) == "#e9e4f5"
    assert colorsys.rgb_to_hls(*colors.hex_to_rgb(colors.readable_accent(0.6)))[1] > 0.6
