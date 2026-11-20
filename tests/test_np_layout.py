"""Layouts of "Reproduciendo" (audyax.gui.np_layout): pure geometry."""
import pytest

from audyax.gui import np_layout as L


def inside(rect, w, h):
    x0, y0, x1, y1 = rect
    return 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h


@pytest.mark.parametrize("size", [(820, 520), (1100, 700), (1920, 1080), (640, 440)])
@pytest.mark.parametrize("layout", list(L.LAYOUTS))
def test_everything_fits_and_nothing_overlaps(layout, size):
    w, h = size
    r = L.compute(layout, w, h)
    x, y, s = r["cover"]
    cover = (x, y, x + s, y + s)
    assert inside(cover, w, h)
    if r["lyrics"]:
        assert inside(r["lyrics"], w, h)
        lx0, ly0, lx1, ly1 = r["lyrics"]
        assert lx0 >= cover[2] or ly0 >= cover[3]          # lyrics never on top of the cover
    if layout == "no_lyrics":
        assert r["lyrics"] is None and r["next"] is None
    controls = r["controls"]                               # playback controls at the bottom
    assert inside(controls, w, max(h, 240 + L.CONTROLS))
    assert cover[3] <= controls[1] and (not r["lyrics"] or r["lyrics"][3] <= controls[1])


def test_layout_sizes_differ_as_promised():
    w, h = 1280, 760
    balanced, big_cover, big_lyrics = (L.compute(k, w, h) for k in ("balanced", "cover", "lyrics"))
    assert big_cover["cover"][2] > balanced["cover"][2] > big_lyrics["cover"][2]
    width = lambda r: r["lyrics"][2] - r["lyrics"][0]
    assert width(big_lyrics) > width(balanced) > width(big_cover)
    assert big_lyrics["fonts"]["current"] > balanced["fonts"]["current"] > big_cover["fonts"]["current"]


def test_unknown_layout_is_balanced_and_next_is_optional():
    assert L.compute("raro", 1000, 700) == L.compute("balanced", 1000, 700)
    assert L.compute("balanced", 1000, 700, has_next=False)["next"] is None


def test_narrow_view_drops_the_lyrics_column():
    assert L.compute("balanced", 330, 700)["lyrics"] is None


def test_lyric_scroll_eases_and_arrives():
    off = 0.0
    for _ in range(60):
        off = L.lyric_scroll(200, 500, off)
    assert off == -300
    assert -300 < L.lyric_scroll(200, 500, 0) < 0


def test_monitor_rect_only_on_windows():
    import sys
    from audyax.gui import desktop
    if not sys.platform.startswith("win"):
        assert desktop.monitor_rect(object()) is None
