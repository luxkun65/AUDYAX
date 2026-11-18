"""Player window helpers that don't need a screen: the logo intro animation."""
from audyax.gui import player_window as pw


def test_intro_starts_invisible_and_ends_invisible():
    assert pw.intro_frame(0)["alpha"] == 0
    assert pw.intro_frame(1)["alpha"] == 0
    assert all(h == 0 for h in pw.intro_frame(0)["bars"])
    assert all(abs(h) < 1e-9 for h in pw.intro_frame(1)["bars"])


def test_intro_logo_pops_then_settles():
    scales = [pw.intro_frame(i / 100)["scale"] for i in range(0, 46)]
    assert scales[0] < 0.4
    assert max(scales) > 1.0             # slight overshoot ("pop")
    assert abs(pw.intro_frame(0.6)["scale"] - 1.0) < 1e-9
    assert pw.intro_frame(0.95)["scale"] > 1.0   # moves towards the viewer while fading


def test_intro_is_fully_visible_in_the_middle_and_shows_the_text():
    f = pw.intro_frame(0.5)
    assert f["alpha"] > 0.9 and f["text"] == 1
    assert len(f["bars"]) == pw.INTRO_BARS and all(0 <= h <= 1 for h in f["bars"])
    assert pw.intro_frame(0.2)["text"] == 0


def test_intro_clamps_time_and_mixes_colors():
    assert pw.intro_frame(-3) == pw.intro_frame(0)
    assert pw.intro_frame(7) == pw.intro_frame(1)
    assert pw._mix("#000000", "#ffffff", 0) == "#000000"
    assert pw._mix("#000000", "#ffffff", 1) == "#ffffff"
    assert pw._mix("#000000", "#ff0080", 0.5) == "#800040"
