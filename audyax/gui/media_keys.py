"""Media keys of the keyboard (play/pause, next, previous) for the player, also while AUDYAX
isn't the window in front.

Windows: the keys are registered for the whole system (RegisterHotKey) in a thread of their
own. If another program already took one (some players do), that key is left to it.
Elsewhere the app binds the keys itself (they work while its window has the focus)."""

import sys
import threading

ACTIONS = {0xB3: "play_pause", 0xB0: "next", 0xB1: "previous"}   # VK_MEDIA_* codes
WM_HOTKEY, WM_QUIT, MOD_NOREPEAT = 0x0312, 0x0012, 0x4000


class MediaKeys:
    def __init__(self, on_key):
        """on_key(action) is called from the keys' thread: forward it to the window."""
        self.on_key = on_key
        self._thread = None
        self._thread_id = None
        self.registered = []          # actions that could be registered

    @property
    def running(self) -> bool:
        return self._thread is not None

    def start(self) -> bool:
        if not sys.platform.startswith("win") or self._thread is not None:
            return False
        ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(ready,), daemon=True, name="media-keys")
        self._thread.start()
        ready.wait(2)
        return bool(self.registered)

    def stop(self):
        if self._thread is None:
            return
        try:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        except Exception:
            pass
        self._thread.join(timeout=2)
        self._thread = None

    def _loop(self, ready):
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        ids = {}
        for n, (vk, action) in enumerate(ACTIONS.items(), start=1):
            if user32.RegisterHotKey(None, n, MOD_NOREPEAT, vk):
                ids[n] = action
        self.registered = list(ids.values())
        ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in ids:
                    try:
                        self.on_key(ids[msg.wParam])
                    except Exception:
                        pass
        finally:
            for n in ids:
                user32.UnregisterHotKey(None, n)
            self.registered = []


# keysyms of the media keys under X11 (Linux), for the window's own bindings
TK_KEYS = {"<XF86AudioPlay>": "play_pause", "<XF86AudioPause>": "play_pause",
           "<XF86AudioNext>": "next", "<XF86AudioPrev>": "previous"}
