"""High-level OCR interface -- the main public API."""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from PIL import Image

from ._dll import ScreenAIDll, find_screen_ai_dir
from ._protobuf import LineResult, parse_visual_annotation
from .models import BoundingBox, OcrBlock, OcrLine, OcrPage, OcrResult, OcrWord

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


class ScreenAI:
    """Direct interface to Chrome's screen-ai OCR.

    Loads ``chrome_screen_ai.dll`` via ctypes -- no browser needed.

    >>> ai = ScreenAI()
    >>> result = ai.ocr("scan.pdf")
    >>> print(result.to_text())
    """

    def __init__(self, model_dir: Path | None = None):
        if model_dir is None:
            model_dir = find_screen_ai_dir()
        self._dll = ScreenAIDll(model_dir)
        if not self._dll.init_ocr():
            raise RuntimeError("Failed to initialize screen-ai OCR pipeline")
        self._max_dim = self._dll.get_max_image_dimension()
        log.info("OCR ready (max dimension: %d)", self._max_dim)

    # -- Public API ---------------------------------------------------------

    @property
    def version(self) -> tuple[int, int]:
        return self._dll.get_version()

    @property
    def max_image_dimension(self) -> int:
        return self._max_dim

    def ocr(self, file: str | Path) -> OcrResult:
        """OCR a PDF or image file.  Returns structured :class:`OcrResult`."""
        path = Path(file)
        if path.suffix.lower() == ".pdf":
            return self._ocr_pdf(path)
        return self._ocr_image_file(path)

    def ocr_pil_image(self, img: Image.Image) -> OcrPage:
        """OCR a single PIL Image.  Returns one :class:`OcrPage`."""
        lines, size = self._perform_ocr(img)
        return _lines_to_page(lines, page_number=1, img_size=size)

    # -- Internals ----------------------------------------------------------

    def _perform_ocr(
        self, img: Image.Image,
    ) -> tuple[list[LineResult], tuple[int, int]]:
        """Resize, convert to BGRA, call DLL, parse protobuf."""
        w, h = img.size
        if max(w, h) > self._max_dim:
            scale = self._max_dim / max(w, h)
            nw, nh = int(w * scale), int(h * scale)
            log.info("Resizing %dx%d -> %dx%d", w, h, nw, nh)
            img = img.resize((nw, nh), Image.LANCZOS)

        img = img.convert("RGBA")
        width, height = img.size

        raw = self._dll.perform_ocr(img.tobytes("raw", "BGRA"), width, height)
        if raw is None:
            return [], (width, height)
        return parse_visual_annotation(raw), (width, height)

    def _ocr_image_file(self, path: Path) -> OcrResult:
        img = Image.open(path)
        lines, size = self._perform_ocr(img)
        page = _lines_to_page(lines, page_number=1, img_size=size)
        return OcrResult(pages=[page])

    def _ocr_pdf(self, path: Path) -> OcrResult:
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:
            raise ImportError(
                "PyMuPDF is required for PDF OCR: pip install PyMuPDF"
            ) from exc

        doc = fitz.open(path)
        pages: list[OcrPage] = []
        for i in range(len(doc)):
            pg = doc[i]
            scale = min(1.0, self._max_dim / max(pg.rect.width, pg.rect.height))
            pix = pg.get_pixmap(dpi=int(72 * scale * 2))
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            lines, size = self._perform_ocr(img)
            ocr_page = _lines_to_page(lines, page_number=i + 1, img_size=size)
            pages.append(ocr_page)
            log.info(
                "Page %d: %d blocks, %d lines", i + 1,
                len(ocr_page.blocks),
                sum(len(b.lines) for b in ocr_page.blocks),
            )
        return OcrResult(pages=pages)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lines_to_page(
    lines: list[LineResult], page_number: int, img_size: tuple[int, int],
) -> OcrPage:
    blocks_map: dict[int, list[LineResult]] = defaultdict(list)
    for ln in lines:
        blocks_map[ln.block_id].append(ln)

    blocks: list[OcrBlock] = []
    for block_id in sorted(blocks_map):
        ocr_lines: list[OcrLine] = []
        for ln in blocks_map[block_id]:
            words = [
                OcrWord(
                    text=w.text,
                    confidence=w.confidence or None,
                    bounding_box=BoundingBox(x=w.x, y=w.y, width=w.width, height=w.height)
                    if w.width else None,
                )
                for w in ln.words
            ]
            ocr_lines.append(OcrLine(
                text=ln.text, words=words,
                bounding_box=BoundingBox(x=ln.x, y=ln.y, width=ln.width, height=ln.height)
                if ln.width else None,
            ))
        blocks.append(OcrBlock(block_type="paragraph", lines=ocr_lines))

    return OcrPage(
        page_number=page_number, blocks=blocks,
        width=img_size[0], height=img_size[1],
    )
