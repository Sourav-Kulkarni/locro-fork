# screen-ai-wrapper

Python wrapper for Chrome's built-in **screen-ai** OCR engine.
Calls `chrome_screen_ai.dll` directly via ctypes -- no browser needed.

Supports PDFs (multi-page) and images (JPG, PNG).

## Quick start

```bash
pip install -e .
screen-ai-ocr document.pdf
screen-ai-ocr photo.jpg --text
```

See [GUIDE.md](GUIDE.md) for full installation, CLI, and API documentation.

See [CHROME_SCREEN_AI_DLL.md](CHROME_SCREEN_AI_DLL.md) for technical details on
how the DLL interface was reverse-engineered.

## Background

- [Gemini Deep Research notes](https://gemini.google.com/share/959c7fffc748)
