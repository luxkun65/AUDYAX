"""Library and tools package: play history, audio cutter, YouTube link in the tags."""
import subprocess

import pytest
from test_engine import make_audio, needs_ffmpeg, needs_mutagen

from audyax.core import history, tags, trim


# ------------------------------------------------------------------ history
def test_counts_as_played():
    assert not history.counts_as_played(29, 200) and history.counts_as_played(30, 200)
    assert history.counts_as_played(25, 40) and not history.counts_as_played(15, 40)   # short song: half


def test_history_record_and_lists(tmp_path):
    h = history.History(tmp_path / "h.json")
    h.record("/a.mp3", when=100)
    h.record("/b.mp3", when=200)
    h.record("/b.mp3", when=300)
    h.record("/c.mp3", when=400)
    assert h.count("/b.mp3") == 2 and h.count("/nada.mp3") == 0
    assert h.most_played(2) == ["/b.mp3", "/c.mp3"]            # ties: most recent first
    assert h.recent() == ["/c.mp3", "/b.mp3", "/a.mp3"]
    assert h.recent(existing={"/a.mp3"}) == ["/a.mp3"]
    again = history.History(tmp_path / "h.json")              # saved to disk
    assert again.count("/b.mp3") == 2
    again.forget("/b.mp3")
    assert history.History(tmp_path / "h.json").count("/b.mp3") == 0


def test_history_survives_a_broken_file(tmp_path):
    (tmp_path / "h.json").write_text("{roto", encoding="utf-8")
    h = history.History(tmp_path / "h.json")
    assert h.songs == {}
    h.record("/a.mp3")
    assert history.History(tmp_path / "h.json").count("/a.mp3") == 1


def test_recently_added():
    tracks = [{"path": "a", "mtime": 5}, {"path": "b", "mtime": 9}, {"path": "c", "mtime": 1}]
    assert [t["path"] for t in history.recently_added(tracks, 2)] == ["b", "a"]


# ------------------------------------------------------------------ cutter
def _duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


def test_cut_name(tmp_path):
    song = tmp_path / "Canción.mp3"
    assert trim.cut_name(song).name == "Canción (recorte).mp3"
    trim.cut_name(song).write_bytes(b"x")
    assert trim.cut_name(song).name == "Canción (recorte 2).mp3"


def test_build_command_fades_and_errors(tmp_path):
    cmd = trim.build_command(tmp_path / "a.mp3", tmp_path / "b.mp3", 10, 40, fade_in=2, fade_out=3)
    af = cmd[cmd.index("-af") + 1]
    assert af == "afade=t=in:st=0:d=2.00,afade=t=out:st=27.00:d=3.00"
    assert cmd[cmd.index("-ss") + 1] == "10.000" and cmd[cmd.index("-t") + 1] == "30.000"
    assert "-af" not in trim.build_command(tmp_path / "a.opus", tmp_path / "b.opus", 0, 5)
    with pytest.raises(ValueError):
        trim.build_command(tmp_path / "a.mp3", tmp_path / "b.mp3", 5, 5)


@needs_ffmpeg
@needs_mutagen
def test_trim_keeps_tags_and_length(tmp_path):
    song = make_audio(tmp_path / "Tema.mp3", seconds=6)
    tags.write_tags(song, {"title": "Tema", "artist": "Yo"})
    out = trim.trim(song, 1.0, 4.0, fade_in=0.5, fade_out=0.5)
    assert out.name == "Tema (recorte).mp3" and song.exists()
    assert abs(_duration(out) - 3.0) < 0.15
    assert tags.read_tags(out)["title"] == "Tema"


@needs_ffmpeg
def test_trim_replace_and_failure(tmp_path):
    song = make_audio(tmp_path / "Tema.m4a", seconds=5)
    assert trim.trim(song, 0, 2, replace=True) == song
    assert abs(_duration(song) - 2.0) < 0.15
    assert not list(tmp_path.glob(".*recortando*"))           # the temporary file is gone
    broken = tmp_path / "roto.mp3"
    broken.write_bytes(b"no es audio")
    with pytest.raises(RuntimeError):
        trim.trim(broken, 0, 1)
    assert not (tmp_path / "roto (recorte).mp3").exists()


# ------------------------------------------------------------------ YouTube link
@needs_ffmpeg
@needs_mutagen
def test_read_source_url(tmp_path):
    for ext in ("mp3", "m4a", "opus"):
        src = make_audio(tmp_path / f"src.{ext}", seconds=1)
        out = tmp_path / f"con_url.{ext}"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-c", "copy", "-metadata",
                        "comment=https://www.youtube.com/watch?v=dQw4w9WgXcQ", "-metadata",
                        "purl=https://www.youtube.com/watch?v=dQw4w9WgXcQ", str(out)], check=True)
        assert tags.read_source_url(out) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ", ext
    assert tags.read_source_url(src) == ""
    assert tags.read_source_url(tmp_path / "no_existe.mp3") == ""
