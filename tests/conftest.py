import sys
from pathlib import Path

import pytest

# Permite importar motor_descarga desde la raíz del proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def no_third_party_internet(monkeypatch):
    """Las pruebas no consultan MusicBrainz ni LRCLIB de verdad (sería lento e inestable):
    por defecto no encuentran nada. Las pruebas de esos módulos simulan sus respuestas."""
    import lyrics
    import metadata
    monkeypatch.setattr(metadata, "search", lambda *a, **k: None)
    monkeypatch.setattr(lyrics, "find_lyrics", lambda *a, **k: None)
