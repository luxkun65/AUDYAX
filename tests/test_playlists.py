"""The player's own playlists (audyax.core.playlists): .m3u8 files in <library>/Listas."""
import os

import pytest

from audyax.core import playlists as pl


@pytest.fixture
def lib(tmp_path):
    (tmp_path / "Soda").mkdir()
    for n in ("a", "b", "c"):
        (tmp_path / "Soda" / f"{n}.mp3").write_bytes(b"x")
    return tmp_path


def t(lib, n, **extra):
    return {"path": str(lib / "Soda" / f"{n}.mp3"), **extra}


def test_create_read_and_list(lib):
    path = pl.create_playlist(lib, "Para correr", [t(lib, "b", title="B", artist="Soda", duration=61.4),
                                                  t(lib, "a")])
    assert path == lib / "Listas" / "Para correr.m3u8"
    text = path.read_text(encoding="utf-8")
    relative = os.path.join("..", "Soda", "b.mp3")       # ..\\Soda\\b.mp3 on Windows
    assert text.startswith(f"#EXTM3U\n#EXTINF:61,Soda - B\n{relative}\n")   # relative paths
    assert "#EXTINF:-1,a\n" in text
    assert pl.read_playlist(path) == [str((lib / "Soda" / "b.mp3").resolve()), str((lib / "Soda" / "a.mp3").resolve())]
    pl.create_playlist(lib, "álbum favorito")
    assert [n for n, _ in pl.list_playlists(lib)] == ["álbum favorito", "Para correr"]


def test_names_are_safe_and_unique(lib):
    assert pl.safe_name('  Rock: "70s/80s"?  ') == "Rock 70s 80s"
    assert pl.safe_name("con") == "con_"
    with pytest.raises(ValueError):
        pl.create_playlist(lib, "  ")
    pl.create_playlist(lib, "Rock")
    with pytest.raises(ValueError):
        pl.create_playlist(lib, "rock")          # same name ignoring case


def test_rename_and_delete(lib):
    path = pl.create_playlist(lib, "Rock", [t(lib, "a")])
    path = pl.rename_playlist(lib, path, "ROCK")    # only the case changes
    assert path.name == "ROCK.m3u8" and pl.read_playlist(path)
    pl.create_playlist(lib, "Pop")
    with pytest.raises(ValueError):
        pl.rename_playlist(lib, path, "pop")
    new = pl.rename_playlist(lib, path, "Clásicos")
    assert not path.exists() and new.exists()
    pl.delete_playlist(new)
    pl.delete_playlist(new)                        # already gone: no error
    assert [n for n, _ in pl.list_playlists(lib)] == ["Pop"]
    assert (lib / "Soda" / "a.mp3").exists()       # songs are never deleted


def test_reads_m3u_from_other_programs(lib, tmp_path):
    other = tmp_path / "Listas" / "vieja.m3u"
    other.parent.mkdir()
    absolute = str((lib / "Soda" / "c.mp3").resolve())
    other.write_bytes(("#EXTM3U\r\n# comentario\r\n\r\n../Soda/a.mp3\r\n" + absolute +
                       "\r\nhttp://radio.example/stream\r\nCanción.mp3\r\n").encode("latin-1"))
    songs = pl.read_playlist(other)
    assert songs[:2] == [str((lib / "Soda" / "a.mp3").resolve()), absolute]
    assert songs[2].endswith("Canción.mp3") and len(songs) == 3   # latin-1 read, URL skipped
    assert pl.read_playlist(tmp_path / "no_existe.m3u8") == []


def test_missing_songs_are_kept(lib):
    path = pl.create_playlist(lib, "X", [t(lib, "a"), {"path": str(lib / "borrada.mp3")}])
    assert len(pl.read_playlist(path)) == 2


def test_write_is_atomic(lib, monkeypatch):
    path = pl.create_playlist(lib, "X", [t(lib, "a")])
    before = path.read_text(encoding="utf-8")

    def boom(*_a):
        raise OSError("disco lleno")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        pl.write_playlist(path, [t(lib, "b")])
    assert path.read_text(encoding="utf-8") == before


def test_no_folder_no_lists(tmp_path):
    assert pl.list_playlists(tmp_path / "nada") == []
