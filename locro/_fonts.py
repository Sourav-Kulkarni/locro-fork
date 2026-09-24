"""Fallback font lookup for non-Latin scripts in searchable-PDF text overlays.

PyMuPDF's built-in ``"helv"`` font (Helvetica) only covers WinAnsi/Latin-1.
Any OCR'd text outside that range -- Devanagari, Arabic, Cyrillic, and other
non-Latin scripts -- gets written with missing glyphs, so the invisible text
layer becomes unsearchable/uncopyable garbage even though the page looks
fine (the visible pixels come from the original scanned image, not the
overlay).  This module finds a system font that actually covers a given
piece of text, so :func:`locro.ocr._overlay_text` can embed it correctly.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

if sys.platform == "win32":
    _CANDIDATES = [
        "C:/Windows/Fonts/Nirmala.ttf",  # pan-Indic: Devanagari, Bengali, Tamil, Telugu, ...
        "C:/Windows/Fonts/Tahoma.ttf",  # Arabic, Hebrew, Thai, Cyrillic, Greek
        "C:/Windows/Fonts/Arial.ttf",
    ]
elif sys.platform == "darwin":
    _CANDIDATES = [
        "/System/Library/Fonts/Supplemental/Kohinoor Devanagari.ttc",
        "/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc",
        "/System/Library/Fonts/Supplemental/Geeza Pro.ttc",  # Arabic
        "/System/Library/Fonts/Supplemental/Thonburi.ttc",  # Thai
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
else:
    _CANDIDATES = [
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]


def _covers(font, text: str) -> bool:
    return all(not ch.isprintable() or ch.isspace() or font.has_glyph(ord(ch)) for ch in text)


class FallbackFontFinder:
    """Finds (and caches) a system font able to render a given string."""

    def __init__(self) -> None:
        self._helv = None
        self._fonts: dict[str, object] = {}
        self._warned = False

    def _font(self, path: str):
        if path not in self._fonts:
            import fitz

            font = None
            if Path(path).is_file():
                try:
                    font = fitz.Font(fontfile=path)
                except Exception:
                    log.debug("Could not load candidate font %s", path, exc_info=True)
            self._fonts[path] = font
        return self._fonts[path]

    def font_for(self, text: str) -> tuple[str | None, str | None]:
        """Return ``(fontname, fontfile)`` able to render ``text``.

        ``(None, None)`` means the default ``"helv"`` font already covers it.
        """
        import fitz

        if self._helv is None:
            self._helv = fitz.Font(fontname="helv")
        if _covers(self._helv, text):
            return None, None

        for path in _CANDIDATES:
            font = self._font(path)
            if font is not None and _covers(font, text):
                return Path(path).stem, path

        if not self._warned:
            log.warning(
                "No system font found covering non-Latin OCR text (e.g. %r); "
                "its searchable-text overlay may not match the visible page. "
                "Install a Unicode font for that script (e.g. Noto Sans) to fix this.",
                text,
            )
            self._warned = True
        return None, None
