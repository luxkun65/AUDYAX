"""Play queue of the player: the songs that will play, in order, with shuffle and repeat.
No UI code here.

An item is a dict (id, path, title, artist, source…). Each one is copied when it enters the
queue, so the same song can be in it twice and every entry is still told apart."""

import random

REPEAT_MODES = ("off", "all", "one")   # no repeat / repeat the queue / repeat the song


class PlayQueue:
    def __init__(self, shuffle: bool = False, repeat: str = "off", rng=None):
        self.items = []          # play order (shuffled when shuffle is on)
        self._original = []      # the same entries in the order they were added
        self.index = -1          # position of the current song in `items` (-1 = none)
        self.shuffle = bool(shuffle)
        self.repeat = repeat if repeat in REPEAT_MODES else "off"
        self._rng = rng or random.Random()

    # ---------------------------------------------------------------- reading
    @property
    def current(self):
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    def upcoming(self):
        return self.items[self.index + 1:]

    def __len__(self):
        return len(self.items)

    # ---------------------------------------------------------------- filling
    def set(self, items, start: int = 0):
        """Replaces the queue with `items` (e.g. the list as it's shown) and makes
        items[start] the current song. With shuffle on, that song goes first and the rest
        are shuffled. Returns the current item (or None if `items` is empty)."""
        self._original = [dict(i) for i in items]
        if not self._original:
            self.items, self.index = [], -1
            return None
        start = max(0, min(start, len(self._original) - 1))
        if self.shuffle:
            first = self._original[start]
            rest = [i for i in self._original if i is not first]
            self._rng.shuffle(rest)
            self.items, self.index = [first] + rest, 0
        else:
            self.items, self.index = list(self._original), start
        return self.current

    def play_next(self, items):
        """Puts `items` right after the current song (in that order)."""
        new = [dict(i) for i in items]
        if not new:
            return
        if self.current is None:
            self.items, self._original = new + self.items, new + self._original
            self.index = -1   # nothing playing: the first of them is "next"
            return
        cur = self.current
        self.items[self.index + 1:self.index + 1] = new
        pos = next(k for k, i in enumerate(self._original) if i is cur)
        self._original[pos + 1:pos + 1] = new

    def add(self, items):
        """Adds `items` at the end of the queue."""
        new = [dict(i) for i in items]
        self.items += new
        self._original += new

    def remove(self, position: int):
        """Removes the entry at `position` of the play order. Removing the current song
        doesn't stop it: the next one is still the one that came after it."""
        if not 0 <= position < len(self.items):
            return
        entry = self.items.pop(position)
        self._original = [i for i in self._original if i is not entry]
        if position <= self.index:
            self.index -= 1

    def clear_upcoming(self):
        """Empties what's left after the current song (the current one keeps playing)."""
        cur = self.current
        self.items = self.items[:self.index + 1]
        keep = {id(i) for i in self.items}
        self._original = [i for i in self._original if id(i) in keep]
        if cur is None:
            self.items, self._original, self.index = [], [], -1

    # ---------------------------------------------------------------- moving
    def next(self, auto: bool = False):
        """The song after the current one, or None at the end of the queue.
        auto=True means the song ended by itself: with repeat "one" it plays again.
        (The "next" button skips it even with repeat "one".)"""
        if not self.items:
            return None
        if auto and self.repeat == "one" and self.current is not None:
            return self.current
        if self.index + 1 < len(self.items):
            self.index += 1
        elif self.repeat in ("all", "one"):
            self.index = 0
        else:
            return None
        return self.current

    def position_after(self, position: int, auto: bool = True):
        """The position that follows `position` (same rules as next()), or None."""
        if not self.items:
            return None
        if auto and self.repeat == "one" and 0 <= position < len(self.items):
            return position
        if position + 1 < len(self.items):
            return position + 1
        if self.repeat in ("all", "one"):
            return 0
        return None

    def peek_next(self, ahead: int = 0):
        """The song that will sound after the current one and `ahead` more (for gapless
        playback, which prepares songs in advance). Moves nothing."""
        pos = self.index
        for _ in range(ahead + 1):
            pos = self.position_after(pos, auto=True)
            if pos is None:
                return None
        return self.items[pos]

    def advance(self):
        """The prepared song started sounding: same as next(auto=True)."""
        return self.next(auto=True)

    def previous(self):
        if not self.items:
            return None
        if self.index > 0:
            self.index -= 1
        elif self.repeat in ("all", "one"):
            self.index = len(self.items) - 1
        else:
            return None
        return self.current

    def jump(self, position: int):
        if 0 <= position < len(self.items):
            self.index = position
        return self.current

    # ---------------------------------------------------------------- modes
    def set_shuffle(self, on: bool):
        """Shuffle on: the current song stays and everything else is shuffled after it.
        Off: back to the original order, continuing from the current song."""
        on = bool(on)
        if on == self.shuffle:
            return
        self.shuffle = on
        cur = self.current
        if on:
            rest = [i for i in self._original if i is not cur]
            self._rng.shuffle(rest)
            self.items = ([cur] if cur is not None else []) + rest
            self.index = 0 if cur is not None else -1
        else:
            self.items = list(self._original)
            self.index = next((k for k, i in enumerate(self.items) if i is cur), -1)

    def cycle_repeat(self) -> str:
        """off -> all -> one -> off. Returns the new mode."""
        self.repeat = REPEAT_MODES[(REPEAT_MODES.index(self.repeat) + 1) % len(REPEAT_MODES)]
        return self.repeat
