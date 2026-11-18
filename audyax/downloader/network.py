"""Network options for yt-dlp: speed limit, proxy and (only for age-restricted videos) cookies."""

import os
import re
import shutil
import tempfile
from pathlib import Path

from audyax.core.config import COOKIE_BROWSERS, VALID_PROXY, parallel_downloads

# ==============================================================================
# DOWNLOAD ENGINE (no UI code at all; it talks to the UI by emitting events)
# ==============================================================================
# Messages YouTube uses to ask for age confirmation (cookies are used only in these cases)
AGE_RESTRICTION_RE = re.compile(
    r"confirm your age|age[- ]?restricted|inappropriate for some users|age verification|"
    r"confirmar tu edad|restricci[oó]n de edad", re.IGNORECASE)


def network_options(cfg: dict) -> dict:
    """Speed limit and proxy according to the settings (empty if they are off).
    They are used for ALL requests: downloads, search and reading playlists.
    Cookies do NOT go here: they are only used for age-restricted videos
    (see cookie_options and run_job)."""
    op = {}
    if cfg.get("speed_limit_enabled"):
        try:
            mb = float(str(cfg.get("speed_limit_mb", 0)).replace(",", "."))
        except ValueError:
            mb = 0
        if mb > 0:
            # The limit is for the total: it is split among parallel downloads
            op["ratelimit"] = max(32 * 1024, int(mb * 1024 * 1024 / parallel_downloads(cfg)))
    proxy = (cfg.get("proxy") or "").strip()
    if cfg.get("proxy_enabled") and VALID_PROXY.match(proxy):
        op["proxy"] = proxy
    return op


def cookie_options(cfg: dict) -> dict:
    """YouTube session cookies (empty if the option is off or misconfigured)."""
    if not cfg.get("cookies_enabled"):
        return {}
    source = cfg.get("cookies_source") or "firefox"
    if source == "file":
        if cfg.get("cookies_file") and Path(cfg["cookies_file"]).is_file():
            return {"cookiefile": cfg["cookies_file"]}
        return {}
    if source in COOKIE_BROWSERS:
        return {"cookiesfrombrowser": (source,)}
    return {}


class ReadOnlyCookies:
    """yt-dlp WRITES the cookies file back when it finishes. To leave the user's
    cookies.txt untouched (and avoid several downloads writing it at once), each
    download works with a temporary copy that is deleted at the end."""

    def __init__(self, options: dict):
        self.options, self.snapshot = options, None

    def __enter__(self):
        original = self.options.get("cookiefile")
        if original:
            fd, self.snapshot = tempfile.mkstemp(prefix="audyax_cookies_", suffix=".txt")
            os.close(fd)
            shutil.copyfile(original, self.snapshot)
            self.options["cookiefile"] = self.snapshot
        return self.options

    def __exit__(self, *exc):
        if self.snapshot:
            try:
                os.remove(self.snapshot)
            except OSError:
                pass
        return False
