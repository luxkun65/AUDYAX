

def test_lyric_lines_synced_plain_and_empty():
    from audyax.core.lyrics import lyric_lines
    lines, synced = lyric_lines("[ar:Soda]\n[00:05.00]Uno\n[00:01.00]Cero")
    assert synced and lines == [(1.0, "Cero"), (5.0, "Uno")]
    lines, synced = lyric_lines("[ti:Algo]\n\nPrimera\n\n\n\nSegunda\n  \n")
    assert not synced and lines == [(None, "Primera"), (None, ""), (None, "Segunda")]
    assert lyric_lines("") == ([], False)
    assert lyric_lines(None) == ([], False)


def test_fetch_and_save(tmp_path, monkeypatch):
    from audyax.core import lyrics
    song = tmp_path / "a.mp3"
    song.write_bytes(b"x")
    monkeypatch.setattr(lyrics, "find_lyrics", lambda *a, **k: {"synced": "[00:01.00]Hola", "text": "Hola",
                                                              "instrumental": False})
    assert lyrics.fetch_and_save(song, "A", "B", mode="off") == "ok"     # "off" still saves a .lrc
    assert (tmp_path / "a.lrc").read_text(encoding="utf-8").strip() == "[00:01.00]Hola"
    monkeypatch.setattr(lyrics, "find_lyrics", lambda *a, **k: None)
    assert lyrics.fetch_and_save(song, "A", "B") == "none"

    def boom(*a, **k):
        raise OSError("sin conexión")
    monkeypatch.setattr(lyrics, "find_lyrics", boom)
    assert lyrics.fetch_and_save(song, "A", "B") == "error"


def test_automatic_lyrics_are_off_by_default():
    from audyax.core.config import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["auto_lyrics"] is False
