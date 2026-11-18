"""Download queue: pause, parallel downloads and exporting the list."""
import csv
import functools
import http.server
import threading
import time
from pathlib import Path

import pytest

import engine as m
from test_engine import make_audio, needs_ffmpeg


# ------------------------------------------------------------------ helpers
def test_parallel_downloads_is_clamped():
    assert m.parallel_downloads({}) == 1
    assert m.parallel_downloads({"parallel_downloads": 2}) == 2
    assert m.parallel_downloads({"parallel_downloads": 99}) == m.MAX_PARALLEL
    assert m.parallel_downloads({"parallel_downloads": 0}) == 1
    assert m.parallel_downloads({"parallel_downloads": "x"}) == 1
    assert m.DEFAULT_CONFIG["parallel_downloads"] == 2


def test_wait_if_paused():
    pause, cancel = threading.Event(), threading.Event()
    m.wait_if_paused(pause, cancel)  # not paused: doesn't wait
    m.wait_if_paused(None, None)

    pause.set()
    begin = time.monotonic()
    threading.Timer(0.5, pause.clear).start()
    m.wait_if_paused(pause, cancel)
    assert time.monotonic() - begin >= 0.4  # waited until resumed

    pause.set()
    threading.Timer(0.3, cancel.set).start()
    with pytest.raises(m.DownloadCancelled):  # cancelling also wakes up the wait
        m.wait_if_paused(pause, cancel)


def test_name_reservations_across_threads(tmp_path):
    reservations = m.NameReservations()
    names = []
    threads = [threading.Thread(target=lambda: names.append(reservations.reserve(tmp_path, "Intro", "X")))
             for _ in range(5)]
    for h in threads:
        h.start()
    for h in threads:
        h.join()
    assert sorted(names) == ["Intro", "Intro (X)", "Intro (X) 2", "Intro (X) 3", "Intro (X) 4"]


def test_unique_name_with_taken(tmp_path):
    assert m.unique_name(tmp_path, "A", "B", {"a"}) == "A (B)"


def test_setup_log_does_not_replace_same_file(tmp_path):
    log = m.setup_log(tmp_path)
    handler = log.handlers[0]
    assert m.setup_log(tmp_path).handlers == [handler]
    other = tmp_path / "other"
    other.mkdir()
    assert m.setup_log(other).handlers[0] is not handler


def test_resolve_splits_playlists_into_songs(tmp_path, monkeypatch):
    entries = [{"id": f"v{i}", "title": f"T{i}", "url": f"https://www.youtube.com/watch?v=v{i}",
                 "thumbnail": ""} for i in range(3)]
    monkeypatch.setattr(m, "analyze_url",
                        lambda url, folder, sub, *a: (folder / "Lista", entries, True, "Lista"))
    plan = m.resolve("https://www.youtube.com/playlist?list=PL1", tmp_path)
    assert plan["per_song"] and plan["default_album"] == "Lista"
    assert plan["urls"] == [e["url"] for e in entries]

    monkeypatch.setattr(m, "analyze_url", lambda url, folder, sub, *a: (folder, entries[:1], False, ""))
    plan = m.resolve("https://youtu.be/v0", tmp_path)
    assert not plan["per_song"] and plan["urls"] == ["https://youtu.be/v0"]


# ------------------------------------------------------------------ export
def test_export_m3u(tmp_path):
    music = tmp_path / "Música"
    (music / "Daft Punk").mkdir(parents=True)
    a = music / "Daft Punk" / "One More Time.mp3"
    a.write_bytes(b"x")
    songs = [{"path": str(a), "title": "One More Time", "artist": "Daft Punk"},
                 {"path": str(music / "no existe.mp3"), "title": "X"},
                 {"path": "", "title": "Sin archivo"}]
    n = m.export_m3u(music / "lista.m3u", songs)
    assert n == 1
    text = (music / "lista.m3u").read_text(encoding="utf-8")
    assert text.splitlines() == ["#EXTM3U", "#EXTINF:-1,Daft Punk - One More Time",
                                  str(Path("Daft Punk") / "One More Time.mp3")]


def test_export_csv(tmp_path):
    rows = [{"num": 1, "status": "Descargada", "title": "Canción ñ", "artist": "A; B",
              "album": "Disco", "year": "2020", "path": "/x.mp3"},
             {"num": 2, "status": "Error", "title": "=HYPERLINK(\"x\")", "reason": "Video unavailable"}]
    dest = tmp_path / "lista.csv"
    assert m.export_csv(dest, rows) == 2
    assert dest.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM: Excel recognizes accents
    read_rows = list(csv.reader(dest.open(encoding="utf-8-sig"), delimiter=";"))
    assert read_rows[0] == [t for _, t in m.CSV_COLUMNS]
    assert read_rows[1][:5] == ["1", "Descargada", "Canción ñ", "A; B", "Disco"]
    assert read_rows[2][2].startswith("'=")  # not turned into a formula
    assert read_rows[2][9] == "Video unavailable"


@needs_ffmpeg
def test_duration_seconds(tmp_path):
    assert m.duration_seconds(make_audio(tmp_path / "a.mp3", seconds=3)) in (3, 4)
    assert m.duration_seconds(tmp_path / "nada.mp3") == -1


# ------------------------------------------------------------------ end to end
@pytest.fixture
def server(tmp_path_factory, monkeypatch):
    """Serves 4 audio files over local HTTP, slowly, to be able to pause and measure concurrency."""
    root = tmp_path_factory.mktemp("srv")
    for i in range(1, 5):
        make_audio(root / f"pista{i}.m4a", seconds=2)

    class Slow(http.server.SimpleHTTPRequestHandler):
        def copyfile(self, source, dest):
            while True:  # ~0.6 s per file
                chunk = source.read(4096)
                if not chunk:
                    break
                dest.write(chunk)
                time.sleep(0.05)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Slow, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _playlist_plan(base, n=4):
    entries = [{"id": f"pista{i}", "title": f"Pista {i}", "artist": "A", "album": "",
                 "url": f"{base}/pista{i}.m4a", "thumbnail": ""} for i in range(1, n + 1)]

    def resolve(url, folder, sub="", log=None, net=None):
        return {"dest": folder / "Lista", "entries": entries, "is_playlist": True,
                "urls": [e["url"] for e in entries],
                "extras": {e["id"]: {"title": e["title"], "artist": "A", "album": ""} for e in entries},
                "default_album": "Lista", "per_song": True}
    return resolve


def _count_concurrency(monkeypatch):
    status = {"active": 0, "peak": 0}
    lock = threading.Lock()
    real = m.YoutubeDL

    class CallCounter(real):
        def download(self, urls):
            with lock:
                status["active"] += 1
                status["peak"] = max(status["peak"], status["active"])
            try:
                return super().download(urls)
            finally:
                with lock:
                    status["active"] -= 1
    monkeypatch.setattr(m, "YoutubeDL", CallCounter)
    return status


@needs_ffmpeg
@pytest.mark.parametrize("n", [1, 2, 3])
def test_parallel_downloads(server, tmp_path, monkeypatch, n):
    monkeypatch.setattr(m, "resolve", _playlist_plan(server))
    status = _count_concurrency(monkeypatch)
    events = []
    cfg = dict(m.DEFAULT_CONFIG, folder=str(tmp_path), audio_format="mp3", parallel_downloads=n)
    m.run_job("playlist", cfg, events.append, threading.Event())
    done_songs = sorted(Path(e[2]["path"]).name for e in events if e[0] == "done")
    assert done_songs == [f"Pista {i}.mp3" for i in range(1, 5)]
    assert status["peak"] == n
    assert not [e for e in events if e[0] == "error"]
    assert [e for e in events if e[0] == "rg"]  # album ReplayGain at the end, as before


@needs_ffmpeg
def test_limit_shared_between_jobs(server, tmp_path, monkeypatch):
    """Two jobs at once with a semaphore of 2 never go over 2 downloads in total."""
    monkeypatch.setattr(m, "resolve", _playlist_plan(server, 2))
    status = _count_concurrency(monkeypatch)
    limit = threading.BoundedSemaphore(2)
    cfg = dict(m.DEFAULT_CONFIG, audio_format="mp3", replaygain=False, parallel_downloads=2)
    threads = [threading.Thread(target=m.run_job,
                              args=("x", dict(cfg, folder=str(tmp_path / str(i))), lambda e: None,
                                    threading.Event(), None, limit)) for i in range(2)]
    for h in threads:
        h.start()
    for h in threads:
        h.join(60)
    assert status["peak"] == 2
    assert len(list(tmp_path.glob("*/Lista/*.mp3"))) == 4


@needs_ffmpeg
def test_pause_and_resume(server, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "resolve", _playlist_plan(server, 2))
    pause, cancel, events = threading.Event(), threading.Event(), []
    pause.set()  # starts paused
    cfg = dict(m.DEFAULT_CONFIG, folder=str(tmp_path), audio_format="mp3", replaygain=False)
    thread = threading.Thread(target=m.run_job, args=("x", cfg, events.append, cancel, pause))
    thread.start()
    time.sleep(2.5)
    assert not [e for e in events if e[0] == "done"]  # doesn't advance while paused
    assert thread.is_alive()
    pause.clear()
    thread.join(60)
    assert len([e for e in events if e[0] == "done"]) == 2


@needs_ffmpeg
def test_cancel_during_pause(server, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "resolve", _playlist_plan(server, 2))
    pause, cancel = threading.Event(), threading.Event()
    pause.set()
    result = {}
    cfg = dict(m.DEFAULT_CONFIG, folder=str(tmp_path), audio_format="mp3", replaygain=False)

    def job():
        try:
            m.run_job("x", cfg, lambda e: None, cancel, pause)
        except m.DownloadCancelled:
            result["cancelled"] = True
    thread = threading.Thread(target=job)
    thread.start()
    time.sleep(1)
    cancel.set()
    thread.join(10)
    assert not thread.is_alive() and result.get("cancelled")
    assert not list(tmp_path.glob("**/*.mp3"))


# ------------------------------------------------------------------ cancelling a single song
def test_wait_if_paused_single_cancel():
    pause, skip = threading.Event(), set()
    m.wait_if_paused(None, None, "a", skip)  # not in skip: continues
    skip.add("a")
    with pytest.raises(m.DownloadCancelled):
        m.wait_if_paused(None, None, "a", skip)
    m.wait_if_paused(None, None, "b", skip)  # the others continue

    pause.set()  # while paused, cancelling a song wakes it up (and only it)
    skip2 = set()
    threading.Timer(0.3, lambda: skip2.add("x")).start()
    with pytest.raises(m.DownloadCancelled):
        m.wait_if_paused(pause, None, "x", skip2)


@needs_ffmpeg
@pytest.mark.parametrize("n", [1, 2])
def test_cancel_individual_songs(server, tmp_path, monkeypatch, n):
    """track1 is stopped mid-download and track3 before starting; the others finish."""
    monkeypatch.setattr(m, "resolve", _playlist_plan(server))
    skip, events = set(), []

    def emit(ev):
        events.append(ev)
        if ev[0] == "status" and ev[1] == "pista1" and str(ev[2]).startswith("Descargando"):
            skip.update({"pista1", "pista3"})
    cfg = dict(m.DEFAULT_CONFIG, folder=str(tmp_path), audio_format="mp3", replaygain=False,
               parallel_downloads=n)
    m.run_job("x", cfg, emit, threading.Event(), None, None, skip)  # doesn't raise
    done_songs = sorted(e[1] for e in events if e[0] == "done")
    assert done_songs == ["pista2", "pista4"]
    assert sorted(p.name for p in (tmp_path / "Lista").glob("*.mp3")) == ["Pista 2.mp3", "Pista 4.mp3"]
    assert not [e for e in events if e[0] == "error"]  # cancelling is not an error
    file = (tmp_path / "descargados.txt").read_text(encoding="utf-8")
    assert "pista1" not in file and "pista3" not in file  # they can be retried


@needs_ffmpeg
def test_cancel_one_while_paused(server, tmp_path, monkeypatch):
    monkeypatch.setattr(m, "resolve", _playlist_plan(server, 2))
    pause, skip, events = threading.Event(), set(), []
    pause.set()
    cfg = dict(m.DEFAULT_CONFIG, folder=str(tmp_path), audio_format="mp3", replaygain=False)
    thread = threading.Thread(target=m.run_job,
                            args=("x", cfg, events.append, threading.Event(), pause, None, skip))
    thread.start()
    time.sleep(1)
    skip.add("pista1")
    time.sleep(0.5)
    pause.clear()
    thread.join(60)
    assert sorted(e[1] for e in events if e[0] == "done") == ["pista2"]


@needs_ffmpeg
def test_retry_keeps_youtube_music_data(server, tmp_path, monkeypatch):
    """Retrying a single YouTube Music song uses its data, not the video's."""
    events = []
    cfg = dict(m.DEFAULT_CONFIG, folder=str(tmp_path), audio_format="mp3", replaygain=False,
               extras={"pista3": {"title": "Canción Buena", "artist": "Artista", "album": "Disco"}})
    m.run_job(f"{server}/pista3.m4a", cfg, events.append, threading.Event())
    done = [e[2] for e in events if e[0] == "done"][0]
    assert (done["title"], done["artist"], done["album"]) == ("Canción Buena", "Artista", "Disco")
    assert Path(done["path"]).name == "Canción Buena.mp3"
