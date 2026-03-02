# screen-ai-wrapper

Python wrapper for Chrome's built-in **screen-ai** OCR engine.
Calls the screen-ai shared library directly via ctypes -- no browser needed.

Works on **Windows** (`chrome_screen_ai.dll`) and **Linux** (`libchromescreenai.so`).

Supports PDFs (multi-page) and images (JPG, PNG, WebP, BMP, TIFF, GIF).

## Quick start

```bash
pip install -e .
screen-ai-ocr download            # one-time: copy library + models from Chrome
screen-ai-ocr ocr document.pdf
screen-ai-ocr ocr photo.jpg --text
```

See [GUIDE.md](GUIDE.md) for full installation, CLI, and API documentation.

See [CHROME_SCREEN_AI_DLL.md](CHROME_SCREEN_AI_DLL.md) for technical details on
how the library interface was reverse-engineered.

## Background

- [Gemini Deep Research notes](https://gemini.google.com/share/959c7fffc748)
