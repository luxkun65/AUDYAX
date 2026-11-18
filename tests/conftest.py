import sys
from pathlib import Path

import pytest

# Allows importing the app's modules from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def no_third_party_internet(monkeypatch):
    """Tests don't query the real MusicBrainz or LRCLIB (it would be slow and flaky):
    by default they find nothing. The tests for those modules simulate their responses."""
    import lyrics
    import metadata
    monkeypatch.setattr(metadata, "search", lambda *a, **k: None)
    monkeypatch.setattr(lyrics, "find_lyrics", lambda *a, **k: None)
