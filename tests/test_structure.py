"""Project structure: the core and the downloader stay UI-free, and old settings are migrated."""
import json
import subprocess
import sys
from pathlib import Path

from audyax.core import config

ROOT = Path(__file__).resolve().parent.parent


def test_core_and_downloader_do_not_import_the_ui():
    code = ("import sys\n"
            "import audyax.core.config, audyax.core.tags, audyax.core.replaygain, audyax.core.lyrics\n"
            "import audyax.core.metadata, audyax.core.system, audyax.downloader.engine\n"
            "import audyax.downloader.youtube, audyax.downloader.updates, audyax.downloader.export\n"
            "import audyax.downloader.watched, audyax.player.engine, audyax.core.library\n"
            "import audyax.player.queue, audyax.core.playlists, audyax.core.colors\n"
            "ui = [m for m in sys.modules if m.split('.')[0] in ('tkinter', 'customtkinter', 'audyax.gui')]\n"
            "ui += [m for m in sys.modules if m.startswith('audyax.gui')]\n"
            "print(ui)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


def test_old_settings_are_migrated(tmp_path, monkeypatch):
    old = {"carpeta": "/musica", "formato": "opus", "organizar": "artista_album", "letras": "incrustada",
           "cookies_origen": "archivo", "descargas_simultaneas": 3, "replaygain": False,
           "vigiladas": [{"url": "u", "nombre": "Rock", "carpeta": "/musica", "nuevas": 2}]}
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / ".audyax.json")
    monkeypatch.setattr(config, "OLD_CONFIG_PATH", tmp_path / ".descargador_musica.json")
    config.OLD_CONFIG_PATH.write_text(json.dumps(old), encoding="utf-8")
    cfg = config.load_config()
    assert (cfg["folder"], cfg["audio_format"], cfg["organize"], cfg["lyrics"]) == \
        ("/musica", "opus", "artist_album", "embedded")
    assert (cfg["cookies_source"], cfg["parallel_downloads"], cfg["replaygain"]) == ("file", 3, False)
    assert cfg["watched"] == [{"url": "u", "name": "Rock", "folder": "/musica", "new_count": 2}]
    assert "carpeta" not in cfg
    assert json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))["folder"] == "/musica"  # saved in the new file


def test_new_settings_file_wins_over_the_old_one(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / ".audyax.json")
    monkeypatch.setattr(config, "OLD_CONFIG_PATH", tmp_path / ".descargador_musica.json")
    config.OLD_CONFIG_PATH.write_text(json.dumps({"carpeta": "/vieja"}), encoding="utf-8")
    config.CONFIG_PATH.write_text(json.dumps({"folder": "/nueva"}), encoding="utf-8")
    assert config.load_config()["folder"] == "/nueva"
