# locro

<p align="center">
  <img src="locro-logo.png" alt="locro logo" width="200">
</p>

This is a Python wrapper for Chrome's built-in **screen-ai** OCR engine. This engine is *extremely fast* compared to other alternatives (Tesseract, etc.) and *very* accurate (particularly for extracting text; less so when dealing with complex layouts such as tables and forms). However, it is only available through Chrome/Chromium. The magic of this wrapper is that it allows you to call the `screen-ai` library directly from Python (using ctypes), without having to open browser windows.

It works on **Windows** (`chrome_screen_ai.dll`), **Linux** (`libchromescreenai.so`), and **macOS** (`libchromescreenai.so`).

Lastly, it supports both PDFs and images (JPG, PNG, WebP, BMP, TIFF, GIF), including non-Latin scripts (Devanagari, Arabic, Cyrillic, ...) in searchable-PDF output.

## Quick start

To install this library, simply clone it and then install it from the local folder:

```bash
pip install -e .           # install
locro download             # (optional) one-time: copy library + models from Chrome
locro ocr document.pdf     # process a PDF
locro ocr photo.jpg --text # process an image
```

See [GUIDE.md](GUIDE.md) for the full user guide, including installation, CLI, and API documentation.

## Batch & parallel OCR

Point `ocr` at multiple files, a folder, or both -- and add `-j` to OCR several
files at once (each in its own process, since the underlying OCR library
isn't safe to share across threads):

```bash
locro ocr document.pdf                      # single file
locro ocr a.pdf b.pdf c.pdf -j 3            # several files, 3 in parallel
locro ocr ./scans -j 4                      # every PDF/image directly inside a folder
locro ocr ./scans -j 4 -s ./searchable      # + a searchable PDF per file, named <name>_searchable.pdf
```

`-s`/`--searchable-pdf` also writes the usual `_ocr.txt`/`_ocr.json` pair
alongside the searchable PDF -- you don't need a separate run for both. With
multiple input files it's treated as an output *directory* rather than a
single filename.

See [CHROME_SCREEN_AI_DLL.md](CHROME_SCREEN_AI_DLL.md) for technical details on
how the library interface was reverse-engineered.
