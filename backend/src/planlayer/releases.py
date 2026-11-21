"""Whether a newer Planlayer release is out, for the admins' notice (docs/install.md, Upgrades).

The latest release is read from GitHub's API at most every 12 hours per server process, an
hour after a failure. A failure only means no notice: it is logged, and the admin's page goes on.
``PLANLAYER_UPDATE_CHECK=false`` turns the check off, for servers without outbound access.
"""

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from planlayer import __version__

log = logging.getLogger(__name__)

LATEST_URL = "https://api.github.com/repos/rlei-odes/planlayer/releases/latest"
UPGRADE_URL = "https://github.com/rlei-odes/planlayer/blob/main/docs/install.md#upgrades"
EVERY_S = 12 * 3600
RETRY_S = 3600
TIMEOUT_S = 5
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class Release:
    version: str  # 0.6.0
    notes_url: str  # the release's page


def parse(text: str) -> tuple[int, int, int] | None:
    """The X.Y.Z of a version or tag (v0.6.0, 0.6.0, 0.1.0.dev0), or None."""
    m = _VERSION.match(text)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def newer(release: Release | None, current: str = __version__) -> Release | None:
    """``release`` when it is newer than ``current``. A development build of a version counts as
    that version."""
    if release is None:
        return None
    latest, running = parse(release.version), parse(current)
    return release if latest and running and latest > running else None


def fetch() -> Release | None:
    """The latest release on GitHub (pre-releases and drafts are left out there)."""
    req = urllib.request.Request(
        LATEST_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"Planlayer/{__version__}"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        doc = json.load(r)
    tag, url = doc.get("tag_name"), doc.get("html_url")
    if not isinstance(tag, str) or parse(tag) is None or not isinstance(url, str):
        return None
    return Release(version=tag.removeprefix("v"), notes_url=url)


_lock = threading.Lock()
_cache: tuple[float, Release | None] | None = None  # (when to read again, what was read)


def latest() -> Release | None:
    """The latest release, from the cache while it is fresh."""
    global _cache
    with _lock:
        if _cache is not None and time.monotonic() < _cache[0]:
            return _cache[1]
        try:
            found, wait = fetch(), EVERY_S
        except urllib.error.HTTPError as e:
            if e.code != 404:  # 404: no release published yet
                log.warning("release check failed (no update notice): HTTP %s", e.code)
            found, wait = None, EVERY_S if e.code == 404 else RETRY_S
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            log.warning("release check failed (no update notice): %s", e)
            found, wait = None, RETRY_S
        _cache = (time.monotonic() + wait, found)
        return found
