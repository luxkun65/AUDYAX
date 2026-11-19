"""Play queue of the player (audyax.player.queue): order, shuffle, repeat, play next."""
import random

from audyax.player.queue import PlayQueue


def songs(*names):
    return [{"id": n, "path": f"/m/{n}.mp3", "title": n} for n in names]


def ids(items):
    return [i["id"] for i in items]


def test_set_starts_at_the_chosen_song_and_walks_in_order():
    q = PlayQueue()
    assert q.set(songs("a", "b", "c"), start=1)["id"] == "b"
    assert q.next()["id"] == "c"
    assert q.next() is None and q.current["id"] == "c"   # end of the queue: stays on the last
    assert q.previous()["id"] == "b"
    assert q.previous()["id"] == "a"
    assert q.previous() is None
    assert PlayQueue().set([]) is None


def test_repeat_all_wraps_around_both_ways():
    q = PlayQueue(repeat="all")
    q.set(songs("a", "b"), start=1)
    assert q.next()["id"] == "a"
    assert q.previous()["id"] == "b"


def test_repeat_one_replays_only_when_the_song_ends_by_itself():
    q = PlayQueue(repeat="one")
    q.set(songs("a", "b"))
    assert q.next(auto=True)["id"] == "a"
    assert q.next()["id"] == "b"          # the "next" button skips it
    assert q.next()["id"] == "a"          # and wraps like repeat all


def test_cycle_repeat():
    q = PlayQueue()
    assert [q.cycle_repeat() for _ in range(3)] == ["all", "one", "off"]
    assert PlayQueue(repeat="raro").repeat == "off"


def test_shuffle_keeps_the_current_song_first_and_plays_everything_once():
    q = PlayQueue(shuffle=True, rng=random.Random(3))
    names = [str(n) for n in range(20)]
    q.set(songs(*names), start=7)
    assert q.current["id"] == "7" and q.index == 0
    assert sorted(ids(q.items)) == sorted(names)
    assert ids(q.items) != names                      # actually shuffled


def test_turning_shuffle_on_and_off_keeps_the_current_song():
    q = PlayQueue(rng=random.Random(1))
    q.set(songs(*"abcdefgh"), start=2)
    q.set_shuffle(True)
    assert q.current["id"] == "c" and q.index == 0
    assert sorted(ids(q.upcoming())) == sorted("abdefgh")
    q.next()
    playing = q.current["id"]
    q.set_shuffle(False)
    assert ids(q.items) == list("abcdefgh") and q.current["id"] == playing


def test_play_next_and_add():
    q = PlayQueue()
    q.set(songs("a", "b", "c"))
    q.play_next(songs("x", "y"))
    q.add(songs("z"))
    assert ids(q.items) == ["a", "x", "y", "b", "c", "z"]
    q.add(songs("a"))                           # the same song twice is fine
    assert ids(q.items).count("a") == 2
    q.set_shuffle(True)
    q.set_shuffle(False)                        # what was inserted keeps its place
    assert ids(q.items) == ["a", "x", "y", "b", "c", "z", "a"]


def test_play_next_with_nothing_playing_goes_first():
    q = PlayQueue()
    q.play_next(songs("x"))
    assert q.current is None and q.next()["id"] == "x"
    q2 = PlayQueue()
    q2.add(songs("a"))
    assert q2.next()["id"] == "a"


def test_remove_keeps_what_comes_next():
    q = PlayQueue()
    q.set(songs("a", "b", "c", "d"), start=1)
    q.remove(0)                         # before the current one
    assert q.current["id"] == "b"
    q.remove(q.index)                   # the current one: the next is still "c"
    assert q.next()["id"] == "c"
    q.remove(99)                        # out of range: nothing
    assert ids(q.items) == ["c", "d"]


def test_clear_upcoming_keeps_the_current_song():
    q = PlayQueue()
    q.set(songs("a", "b", "c"), start=1)
    q.clear_upcoming()
    assert ids(q.items) == ["a", "b"] and q.current["id"] == "b" and q.next() is None
    empty = PlayQueue()
    empty.add(songs("a"))
    empty.clear_upcoming()
    assert len(empty) == 0


def test_jump():
    q = PlayQueue()
    q.set(songs("a", "b", "c"))
    assert q.jump(2)["id"] == "c"
    assert q.jump(9)["id"] == "c"


def test_entries_are_copies():
    original = songs("a")
    q = PlayQueue()
    q.set(original)
    q.current["title"] = "cambiado"
    assert original[0]["title"] == "a"


def test_peek_next_looks_ahead_without_moving():
    q = PlayQueue()
    q.set(songs("a", "b", "c"))
    assert q.peek_next()["id"] == "b" and q.peek_next(1)["id"] == "c" and q.peek_next(2) is None
    assert q.current["id"] == "a"
    q.repeat = "all"
    assert q.peek_next(2)["id"] == "a"
    q.repeat = "one"
    assert q.peek_next(5)["id"] == "a"
    assert q.advance()["id"] == "a"
    assert PlayQueue().peek_next() is None
