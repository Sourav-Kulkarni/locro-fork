"""Platform-specific constants and path helpers.

Shared by ``_dll`` and ``_download`` to avoid circular imports.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    LIB_NAME = "chrome_screen_ai.dll"
elif sys.platform == "linux":
    LIB_NAME = "libchromescreenai.so"
else:
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def chrome_component_bases() -> list[Path]:
    """Return candidate base directories for Chrome's screen_ai component."""
    if sys.platform == "win32":
        return [
            Path.home() / "AppData" / "Local" / "Google" / "Chrome"
            / "User Data" / "screen_ai",
        ]
    # Linux: check Google Chrome, then Chromium
    return [
        Path.home() / ".config" / "google-chrome" / "screen_ai",
        Path.home() / ".config" / "chromium" / "screen_ai",
    ]


def default_model_dir() -> Path:
    """Base directory for downloaded / copied models."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "screen_ai_wrapper"
    # XDG convention on Linux (and fallback for Windows without LOCALAPPDATA)
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "screen_ai_wrapper"
    return Path.home() / ".local" / "share" / "screen_ai_wrapper"


def default_model_dir_display() -> str:
    """Human-readable string for --help text showing the default model dir."""
    if sys.platform == "win32":
        return "%LOCALAPPDATA%/screen_ai_wrapper"
    return "~/.local/share/screen_ai_wrapper"
