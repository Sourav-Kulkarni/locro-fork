"""Download / install the screen-ai component (library + models).

Two strategies:

1. **Copy from Chrome** -- if Chrome has already downloaded the component,
   copy it to the package's own directory so the wrapper works even if
   Chrome is later removed.

2. **Omaha protocol** -- query Google's component-update servers directly.
   Currently Google's server targets this component only to Chrome itself,
   so this path may return ``noupdate``.  It is kept as a fallback in case
   the restriction is lifted in the future.
"""

from __future__ import annotations

import io
import logging
import platform
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ._platform import LIB_NAME, chrome_component_bases, default_model_dir

log = logging.getLogger(__name__)

COMPONENT_ID = "mfhmdacoffpmifoibamicehhklffanao"
UPDATE_URL = "https://update.googleapis.com/service/update2"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def get_default_model_dir() -> Path:
    """Base directory for downloaded / copied models."""
    return default_model_dir()


def find_local_model_dir() -> Path | None:
    """Return the newest locally-stored version directory, or None."""
    base = get_default_model_dir()
    if not base.exists():
        return None
    for v in sorted(base.iterdir(), reverse=True):
        if v.is_dir() and (v / LIB_NAME).exists():
            return v
    return None


def _find_chrome_component_dir() -> Path | None:
    """Find screen-ai inside Chrome's user-data directory."""
    for base in chrome_component_bases():
        if not base.exists():
            continue
        for v in sorted(base.iterdir(), reverse=True):
            if v.is_dir() and (v / LIB_NAME).exists():
                return v
    return None


# ---------------------------------------------------------------------------
# Copy from Chrome
# ---------------------------------------------------------------------------


def copy_from_chrome(target_dir: Path | None = None) -> Path:
    """Copy the screen-ai component from Chrome's local directory.

    Returns the path to the installed version directory.

    Raises FileNotFoundError if Chrome hasn't downloaded the component.
    """
    src = _find_chrome_component_dir()
    if src is None:
        raise FileNotFoundError(
            "screen-ai component not found in Chrome's directory.\n"
            "Open Chrome, visit chrome://components, find 'Screen AI',\n"
            "and click 'Check for update', then try again."
        )

    version = src.name
    base = target_dir or get_default_model_dir()
    dest = base / version

    if (dest / LIB_NAME).exists():
        log.info("Already installed at %s", dest)
        return dest

    log.info("Copying from %s -> %s", src, dest)
    shutil.copytree(src, dest, dirs_exist_ok=True)
    log.info("Installed screen-ai %s", version)
    return dest


# ---------------------------------------------------------------------------
# Omaha protocol (fallback -- currently blocked by server-side targeting)
# ---------------------------------------------------------------------------


@dataclass
class UpdateInfo:
    version: str
    url: str
    size: int
    sha256: str


def _omaha_platform() -> tuple[str, str]:
    """Return (platform, os_version) for the Omaha request."""
    if sys.platform == "win32":
        return "win", platform.version()
    return "linux", platform.release()


def check_for_update() -> UpdateInfo:
    """Query Google's update server for the latest screen-ai version.

    Note: Google currently targets this component only to Chrome clients,
    so this may raise RuntimeError("noupdate").
    """
    plat, os_ver = _omaha_platform()
    request_body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<request protocol="3.1" updater="chromium"'
        ' prodversion="130.0.6723.91" ismachine="0" dedup="cr"'
        ' acceptformat="crx2,crx3">'
        f'<os platform="{plat}" version="{os_ver}" arch="x86_64"/>'
        f'<app appid="{COMPONENT_ID}" version="0.0.0.0"'
        ' installsource="ondemand">'
        "<updatecheck/>"
        "</app>"
        "</request>"
    ).encode("utf-8")

    req = urllib.request.Request(
        UPDATE_URL,
        data=request_body,
        headers={"Content-Type": "application/xml"},
    )

    log.info("Checking for updates...")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()

    root = ET.fromstring(body)
    uc = root.find(".//updatecheck")
    if uc is None:
        raise RuntimeError("Unexpected response: no <updatecheck> element")

    status = uc.get("status", "")
    if status == "noupdate":
        raise RuntimeError(
            "Google's server returned 'noupdate' for this component.\n"
            "The screen-ai component is currently only served to Chrome.\n"
            "Use 'locro download' to copy from a local Chrome install."
        )
    if status != "ok":
        raise RuntimeError(f"Update check failed: status={status}")

    url_el = uc.find(".//url")
    if url_el is None:
        raise RuntimeError("No download URL in response")
    codebase = url_el.get("codebase", "")

    manifest = uc.find(".//manifest")
    version = manifest.get("version", "unknown") if manifest is not None else "unknown"

    pkg = uc.find(".//package")
    if pkg is None:
        raise RuntimeError("No package info in response")

    return UpdateInfo(
        version=version,
        url=codebase + pkg.get("name", ""),
        size=int(pkg.get("size", "0")),
        sha256=pkg.get("hash_sha256", ""),
    )


def download_from_server(
    target_dir: Path | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download the component directly from Google's servers.

    Returns the installed version directory.
    May fail if Google restricts serving to Chrome-only.
    """
    info = check_for_update()
    log.info("Latest version: %s  (%d MB)", info.version, info.size // (1024 * 1024))

    base = target_dir or get_default_model_dir()
    version_dir = base / info.version
    lib_path = version_dir / LIB_NAME

    if lib_path.exists():
        log.info("Already installed at %s", version_dir)
        return version_dir

    log.info("Downloading %s ...", info.url)
    req = urllib.request.Request(info.url)
    with urllib.request.urlopen(req, timeout=600) as resp:
        total = int(resp.headers.get("Content-Length", info.size))
        data = bytearray()
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if on_progress:
                on_progress(len(data), total)

    log.info("Downloaded %d bytes", len(data))

    # CRX3 = magic + header + ZIP.  Python's zipfile finds the EOCD
    # from the tail, so it handles prepended data fine.
    version_dir.mkdir(parents=True, exist_ok=True)
    log.info("Extracting to %s ...", version_dir)
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
        zf.extractall(version_dir)

    if not lib_path.exists():
        raise RuntimeError(
            f"Extraction finished but {LIB_NAME} not found in {version_dir}"
        )

    log.info("Installed screen-ai %s", info.version)
    return version_dir


# ---------------------------------------------------------------------------
# Public API: try best available method
# ---------------------------------------------------------------------------


def download_component(target_dir: Path | None = None) -> Path:
    """Install the screen-ai component using the best available method.

    1. Copies from Chrome's local directory if available.
    2. Falls back to Omaha download (may fail due to server restrictions).

    Returns the path to the version directory containing the library.
    """
    # Try copying from Chrome first
    try:
        return copy_from_chrome(target_dir)
    except FileNotFoundError:
        log.info("Chrome component not found locally, trying server download...")

    # Fallback: try Omaha
    return download_from_server(target_dir)
