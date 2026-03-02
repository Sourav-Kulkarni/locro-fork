"""screen-ai-wrapper -- Python wrapper for Chrome's screen-ai OCR."""

from .models import BoundingBox, OcrBlock, OcrLine, OcrPage, OcrResult, OcrWord
from .ocr import ScreenAI

__all__ = [
    "BoundingBox",
    "OcrBlock",
    "OcrLine",
    "OcrPage",
    "OcrResult",
    "OcrWord",
    "ScreenAI",
]
