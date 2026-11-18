import sys
from pathlib import Path

import pytest

# Permite importar motor_descarga desde la raíz del proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def sin_internet_de_terceros(monkeypatch):
    """Las pruebas no consultan MusicBrainz ni LRCLIB de verdad (sería lento e inestable):
    por defecto no encuentran nada. Las pruebas de esos módulos simulan sus respuestas."""
    import letras
    import metadatos
    monkeypatch.setattr(metadatos, "buscar", lambda *a, **k: None)
    monkeypatch.setattr(letras, "buscar_letra", lambda *a, **k: None)
