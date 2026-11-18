"""Player library (audyax.core.library): scan, cache, groups, search and sort."""
import os

import pytest
from test_engine import make_audio, needs_ffmpeg, needs_mutagen

from audyax.core import library, tags


def _song(path, **fields):
    make_audio(path, seconds=1)
    if fields:
        tags.write_tags(path, fields)
    return path


@pytest.fixture
def music(tmp_path):
    folder = tmp_path / "Musica"
    (folder / "Rock").mkdir(parents=True)
    (folder / ".oculta").mkdir()
    _song(folder / "Rock" / "a.mp3", title="De Música Ligera", artist="Soda Stereo", album="Canción Animal",
          year="1990")
    _song(folder / "Rock" / "b.m4a", title="Canción Animal", artist="Soda Stereo", album="Canción Animal",
          year="1990")
    _song(folder / "c.opus", title="Lamento Boliviano", artist="Enanitos Verdes", album="Big Bang")
    _song(folder / "sin_etiquetas.mp3")
    _song(folder / ".oculta" / "x.mp3", title="No debe salir")
    (folder / "notas.txt").write_text("no es audio", encoding="utf-8")
    return folder


def _lib(folder, tmp_path):
    return library.Library(folder, cache_path=tmp_path / "cache.json")


@needs_ffmpeg
@needs_mutagen
def test_scan_reads_tags_and_skips_hidden_and_non_audio(music, tmp_path):
    lib = _lib(music, tmp_path)
    stats = lib.scan()
    assert stats == {"added": 4, "updated": 0, "removed": 0, "total": 4}
    by_title = {t["title"]: t for t in lib.tracks}
    assert set(by_title) == {"De Música Ligera", "Canción Animal", "Lamento Boliviano", "sin_etiquetas"}
    a = by_title["De Música Ligera"]
    assert (a["artist"], a["album"], a["year"]) == ("Soda Stereo", "Canción Animal", "1990")
    assert 0.5 < a["duration"] < 2
    assert by_title["sin_etiquetas"]["artist"] == ""  # the title falls back to the file name


@needs_ffmpeg
@needs_mutagen
def test_second_scan_uses_the_cache_and_sees_changes(music, tmp_path, monkeypatch):
    _lib(music, tmp_path).scan()
    lib = _lib(music, tmp_path)
    assert lib.load() and len(lib.tracks) == 4      # instant, from the cache
    read = []
    original = library.read_track
    monkeypatch.setattr(library, "read_track", lambda p: read.append(p) or original(p))
    assert lib.scan() == {"added": 0, "updated": 0, "removed": 0, "total": 4}
    assert read == []                                 # nothing changed: no file is opened

    (music / "c.opus").unlink()
    _song(music / "Rock" / "d.mp3", title="Persiana Americana", artist="Soda Stereo", album="Signos")
    tags.write_tags(music / "Rock" / "a.mp3", {"title": "Música Ligera"})
    st = os.stat(music / "Rock" / "a.mp3")
    os.utime(music / "Rock" / "a.mp3", (st.st_atime, st.st_mtime + 5))   # some filesystems keep the second
    assert lib.scan() == {"added": 1, "updated": 1, "removed": 1, "total": 4}
    assert {t["title"] for t in lib.tracks} >= {"Música Ligera", "Persiana Americana"}


def test_cache_of_another_folder_is_ignored(tmp_path):
    lib = library.Library(tmp_path / "uno", cache_path=tmp_path / "cache.json")
    lib.tracks = [{"path": "x.mp3", "title": "x"}]
    lib.save()
    assert library.Library(tmp_path / "uno", cache_path=tmp_path / "cache.json").load()
    assert not library.Library(tmp_path / "otra", cache_path=tmp_path / "cache.json").load()
    (tmp_path / "cache.json").write_text("{roto", encoding="utf-8")
    assert not library.Library(tmp_path / "uno", cache_path=tmp_path / "cache.json").load()


def test_missing_folder_gives_an_empty_library(tmp_path):
    lib = _lib(tmp_path / "no_existe", tmp_path)
    assert lib.scan()["total"] == 0 and lib.tracks == []


@needs_ffmpeg
def test_scan_can_be_cancelled_without_changing_anything(music, tmp_path):
    lib = _lib(music, tmp_path)
    assert lib.scan(cancel=lambda: True) is None
    assert lib.tracks == [] and not (tmp_path / "cache.json").exists()


@needs_ffmpeg
def test_progress_reaches_the_total(music, tmp_path):
    seen = []
    _lib(music, tmp_path).scan(progress=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (4, 4)


def _t(path, title="", artist="", album="", year="", duration=0):
    return {"path": path, "title": title, "artist": artist, "album": album, "year": year,
            "genre": "", "duration": duration, "mtime": 0, "size": 0}


@pytest.fixture
def lib(tmp_path):
    lib = library.Library(tmp_path, cache_path=tmp_path / "c.json")
    lib.tracks = [
        _t("/m/1.mp3", "Ciudad de la Furia", "Soda Stereo", "Doble Vida", "1988", 300),
        _t("/m/2.mp3", "En la Ciudad", "soda stereo", "Doble Vida", "1988", 200),
        _t("/m/3.mp3", "Mil Horas", "Los Abuelos de la Nada", "Vasos y Besos", "1983", 180),
        _t("/m/r/4.mp3", "Feat", "Otro", "Éxitos", "", 100),
        _t("/m/r/5.mp3", "Feat 2", "Otro más", "exitos", "2001", 100),
        _t("/m/r/6.mp3", "Feat 3", "Tercero", "Exitos", "", 100),
        _t("/m/sueltas/7.mp3", "Suelta", "", "", "", 0),
    ]
    return lib


def test_artists_are_grouped_ignoring_case(lib):
    artists = lib.artists()
    assert artists[0] == ("Los Abuelos de la Nada", 1, 1)
    assert ("Soda Stereo", 2, 1) in artists
    assert artists[-1] == (library.NO_ARTIST, 1, 1)  # unknown always last
    assert len(lib.tracks_of_artist("SODA STEREO")) == 2


def test_albums_group_by_name_and_pick_their_artist(lib):
    albums = {a["album"]: a for a in lib.albums()}
    assert albums["Doble Vida"]["artist"] == "Soda Stereo" and albums["Doble Vida"]["count"] == 2
    exitos = next(a for a in lib.albums() if library.normalize(a["album"]) == "exitos")
    assert exitos["count"] == 3 and exitos["artist"] == library.VARIOUS_ARTISTS
    assert exitos["year"] == "2001"
    assert lib.albums()[-1]["album"] == library.NO_ALBUM
    assert [t["path"] for t in lib.tracks_of_album(exitos["key"])] == ["/m/r/4.mp3", "/m/r/5.mp3", "/m/r/6.mp3"]


def test_search_ignores_accents_case_and_word_order(lib):
    assert [t["path"] for t in library.search(lib.tracks, "ciudad soda")] == ["/m/1.mp3", "/m/2.mp3"]
    assert [t["path"] for t in library.search(lib.tracks, "ÉXITOS feat 2")] == ["/m/r/5.mp3"]
    assert len(library.search(lib.tracks, "  ")) == len(lib.tracks)
    assert library.search(lib.tracks, "nada que ver xyz") == []


def test_sort_by_column_with_natural_tiebreak(lib):
    by_artist = [t["path"] for t in library.sort_tracks(lib.tracks, "artist")]
    assert by_artist[0] == "/m/3.mp3" and by_artist[-1] == "/m/sueltas/7.mp3"  # empty last
    assert by_artist.index("/m/1.mp3") < by_artist.index("/m/2.mp3")
    by_duration = library.sort_tracks(lib.tracks, "duration", reverse=True)
    assert by_duration[0]["path"] == "/m/1.mp3"
    assert library.sort_tracks(lib.tracks, "year")[0]["year"] == "1983"


def test_format_duration():
    assert library.format_duration(0) == "—"
    assert library.format_duration(65.4) == "1:05"
    assert library.format_duration(3725) == "1:02:05"
