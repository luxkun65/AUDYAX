# AUDYAX
This project aims to develop **Audyax**, a desktop application designed for downloading and managing music from YouTube and YouTube Music. The goal is to provide a tool that allows users to download individual songs or entire playlists, with configurable audio formats and quality settings.

In addition to downloading, the application facilitates music file organization by incorporating metadata such as title, artist, album, and year, as well as embedding cover artwork into downloaded files. It also implements volume normalization and ReplayGain features to provide a more consistent listening experience across different tracks.

The application features a graphical user interface that allows users to view queued songs, monitor download progress, identify successfully downloaded files, and detect potential errors. It also includes options for configuring the destination folder and saving user preferences.

The development is primarily based on Python, using `yt-dlp` as the extraction and download engine, `FFmpeg` for audio processing and conversion, and `Mutagen` for metadata management.

## Features

- Download a single song or a whole playlist (each playlist goes into its own subfolder).
- **YouTube Music playlists and albums** are read the way YouTube Music shows them (with `ytmusicapi`): when the original version of a song was removed or is blocked in your country, YouTube Music silently plays another version; AUDYAX downloads that same playable version and uses YouTube Music's title, artist and album. Radio links (`&list=RD…`) download only that song, not the whole automatic radio.
- **Search by name**: type "artist song" instead of a link, pick the results you want and download them.
- **Clipboard detection**: when the app opens or regains focus, a copied YouTube link is offered with *Paste* / *Download*. Links can also be **dragged and dropped** onto the window (requires `tkinterdnd2`, included in `requirements.txt`).
- Output formats: **MP3**, **M4A**, **Opus** or the **original** audio stream.
- Tags: title, artist, album, year and genre, plus embedded cover art (optionally cropped to a square).
- **ReplayGain** (track and album) written as tags, without touching the audio, or **loudness normalization** (EBU R128, −14 LUFS), which re-encodes the audio. Only one of the two can be active.
- Queue with per-song status (queued / downloading / done / error), thumbnails, progress bar and counters.
- Retry failed songs (they go back to the same playlist subfolder), open link or folder.
- **Queue controls**: **pause / resume** all downloads (the current ones stop where they are and continue later), **cancel a single song** without touching the rest (hover the yellow status icon of a song and click the red ✕, or select it and press *Cancelar* in the detail panel, or right-click it), or cancel everything. A cancelled song can be retried later.
- **Parallel downloads**: 1 to 3 songs at a time (*Configuración → Descargas simultáneas*, 2 by default). Playlists are split into songs so they also download in parallel.
- **Filter and sort the list**: search box (title, artist, album), filter by status (downloaded / in progress / error) and click a column header to sort (click again to reverse, a third time to go back to arrival order). *Select all* and *Limpiar lista* act on the visible rows only, so filtering by "Con error" and pressing *Limpiar lista* removes just the failed ones.
- **Export**: a `.m3u` playlist of the downloaded songs (relative paths, UTF-8) or a `.csv` table of the list (`;`-separated with BOM, opens directly in Excel in Spanish). Both follow the current filter and order.
- **Tag editor**: fix the title, artist, album, year, genre or cover of a downloaded MP3, M4A or Opus file from the detail panel.
- **yt-dlp updates**: YouTube changes often and old yt-dlp versions stop working. AUDYAX checks for a new version on startup (can be turned off in *Configuración*) and updates it with one click; restart the app afterwards.
- Already-downloaded songs are skipped (`descargados.txt`); failures are logged in `faltantes.txt` and `descarga.log`.
- **Optional folder organization** (*Configuración → Organizar descargas*, off by default): keep everything in one folder, or sort songs into `Artist/` or `Artist/Album/` subfolders using their tags. Playlists can still go together in their own folder.
- Light and dark themes; settings are saved in `~/.descargador_musica.json`.

## Requirements

- Python **3.11+**
- [FFmpeg](https://ffmpeg.org/download.html) installed and available in the `PATH`
- On Linux, tkinter: `sudo apt install python3-tk`

## Installation

```bash
git clone https://github.com/luxkun65/AUDYAX.git
cd AUDYAX
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python descargador_gui.py
```

Paste a YouTube or YouTube Music link (song or playlist), choose the destination folder, format and options, and press **Descargar**.

> **Note on updates:** updating yt-dlp from the app uses `pip`, so it works when running from source (ideally inside a virtual environment). The packaged executable includes its own copy of yt-dlp; to update it, download a newer AUDYAX release.

> **Note on M4A:** YouTube already serves AAC audio, so M4A is saved as-is, without re-encoding (no quality loss). The quality selector only applies to M4A when normalization is enabled, because then the audio has to be re-encoded.

## Building an executable

```bash
pip install -r requirements-dev.txt
python build.py
```

The executable is created in `dist/` (`AUDYAX.exe` on Windows). Build it on the same operating system you will run it on. FFmpeg is not bundled: it must be installed on the target machine.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests don't need internet. The ones that generate audio need FFmpeg and are skipped if it isn't installed. They also run automatically on GitHub Actions for every push and pull request.

## Project structure

| File | Purpose |
|---|---|
| `motor_descarga.py` | Download engine: yt-dlp options, post-processing, tags, ReplayGain, settings. No GUI code; it communicates through events, so it can be used from any interface. |
| `descargador_gui.py` | Graphical interface (customtkinter + Pillow). |
| `build.py` | Builds the executable with PyInstaller. |
| `tests/` | Automated tests (pytest). |

## Disclaimer

This tool is intended for personal use. Only download content you have the right to download, and respect YouTube's Terms of Service and the copyright of the artists.

## License

[MIT](LICENSE)
