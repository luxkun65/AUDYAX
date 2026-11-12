# AUDYAX
This project aims to develop **Audyax**, a desktop application designed for downloading and managing music from YouTube and YouTube Music. The goal is to provide a tool that allows users to download individual songs or entire playlists, with configurable audio formats and quality settings.

In addition to downloading, the application facilitates music file organization by incorporating metadata such as title, artist, album, and year, as well as embedding cover artwork into downloaded files. It also implements volume normalization and ReplayGain features to provide a more consistent listening experience across different tracks.

The application features a graphical user interface that allows users to view queued songs, monitor download progress, identify successfully downloaded files, and detect potential errors. It also includes options for configuring the destination folder and saving user preferences.

The development is primarily based on Python, using `yt-dlp` as the extraction and download engine, `FFmpeg` for audio processing and conversion, and `Mutagen` for metadata management.
