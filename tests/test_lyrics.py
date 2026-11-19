

def test_lyric_lines_synced_plain_and_empty():
    from audyax.core.lyrics import lyric_lines
    lines, synced = lyric_lines("[ar:Soda]\n[00:05.00]Uno\n[00:01.00]Cero")
    assert synced and lines == [(1.0, "Cero"), (5.0, "Uno")]
    lines, synced = lyric_lines("[ti:Algo]\n\nPrimera\n\n\n\nSegunda\n  \n")
    assert not synced and lines == [(None, "Primera"), (None, ""), (None, "Segunda")]
    assert lyric_lines("") == ([], False)
    assert lyric_lines(None) == ([], False)
