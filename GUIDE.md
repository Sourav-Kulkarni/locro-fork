# locro -- Usage Guide

## Prerequisites

1. **Windows 10/11 (64-bit)** or **Linux (64-bit x86)**.

2. **Python 3.12+**.

## Installation

Clone the repository and install in editable mode:

```bash
git clone <repo-url>
cd screen-ai-wrapper
pip install -e .
```

This installs the `locro` command and the `locro` Python
package along with all dependencies (Pillow, PyMuPDF, typer).

## Getting the screen-ai component

The shared library and ML models (~107 MB on Windows, ~255 MB on Linux) need
to be available before OCR will work.

### Option A: Use Chrome's component directly (zero setup)

If Chrome (or Chromium) is installed and has already downloaded the Screen AI
component, the wrapper finds it automatically.

**Windows:**

```
%LOCALAPPDATA%\Google\Chrome\User Data\screen_ai\<version>\chrome_screen_ai.dll
```

**Linux:**

```
~/.config/google-chrome/screen_ai/<version>/libchromescreenai.so
~/.config/chromium/screen_ai/<version>/libchromescreenai.so
```

If the path doesn't exist, open Chrome, visit `chrome://components`, find
**Screen AI** and click *Check for update*.

### Option B: Copy from Chrome (standalone)

```bash
locro download
```

This copies the library and model files from Chrome's local directory into the
package's own directory so the wrapper works independently of Chrome.

| Platform | Default destination |
|----------|---------------------|
| Windows  | `%LOCALAPPDATA%\locro\<version>\` |
| Linux    | `~/.local/share/locro/<version>/` |

You can specify a custom destination:

```bash
locro download --model-dir /path/to/models
```

The wrapper checks Chrome's directory first, then falls back to the
copied/downloaded location.

## CLI usage

After installation the `locro` command is available on your PATH.

### OCR an image

```bash
locro ocr photo.jpg
```

Produces `photo_ocr.txt` (plain text) and `photo_ocr.json` (structured) in the
same directory as the input file.

Supported image formats: **JPG, PNG, WebP, BMP, TIFF, GIF** -- anything Pillow
can decode.

### OCR a PDF

```bash
locro ocr document.pdf
```

Each page is rendered to an image internally and OCR'd.  The outputs contain
all pages.

### Select specific pages (PDF only)

```bash
locro ocr document.pdf --pages 1          # first page only
locro ocr document.pdf --pages 1-10       # pages 1 through 10
locro ocr document.pdf --pages 1,3,5      # pages 1, 3, and 5
locro ocr document.pdf --pages 1-5,10-12  # ranges and individual pages
```

### Light mode (faster, lower quality)

```bash
locro ocr scan.png --light
```

Uses a smaller model for faster inference at the cost of some accuracy.

### Create a searchable PDF

```bash
locro ocr document.pdf --searchable-pdf document_searchable.pdf
```

The output PDF looks identical to the input but has an invisible text layer
overlaid, making it selectable and searchable.  Can be combined with `--pages`:

```bash
locro ocr big.pdf --pages 1-50 --searchable-pdf big_searchable.pdf
```

### Specify an output directory

```bash
locro ocr scan.png -o results/
```

### Print text to stdout

```bash
locro ocr scan.png --text
```

Useful for piping into other tools:

```bash
locro ocr invoice.pdf --text | grep "Total"
```

### Verbose logging

```bash
locro ocr document.pdf -v
```

Shows library loading, image resize, and per-page statistics.

## Library / API usage

### Copying the component programmatically

```python
from locro import download_component

model_dir = download_component()  # copies from Chrome, returns Path
```

### Initialising the OCR engine

```python
from locro import ScreenAI

ai = ScreenAI()
```

The constructor auto-discovers the library (Chrome's directory, then the
locally-copied location) and initialises the OCR pipeline.

Optional constructor arguments:

```python
from pathlib import Path

ai = ScreenAI(
    model_dir=Path("/path/to/screen_ai/140.20"),
    light_mode=True,   # use the smaller/faster model
)
```

### OCR a file (PDF or image)

```python
result = ai.ocr("invoice.pdf")

# Plain text (pages separated by form-feed \f)
print(result.to_text())

# Structured dict (suitable for JSON serialisation)
import json
print(json.dumps(result.to_dict(), indent=2))
```

### OCR specific pages of a PDF

```python
result = ai.ocr("huge.pdf", pages=range(1, 101))   # first 100 pages
result = ai.ocr("huge.pdf", pages=[1])              # first page only
result = ai.ocr("huge.pdf", pages=[1, 3, 5])        # pages 1, 3, 5
```

### Create a searchable PDF

```python
result = ai.ocr_to_searchable_pdf(
    "scanned.pdf",
    "scanned_searchable.pdf",
    pages=range(1, 11),  # optional
)
```

The output PDF retains the original appearance but has an invisible text
layer overlaid, making it selectable and searchable in any PDF viewer.

### OCR a PIL Image directly

```python
from PIL import Image

img = Image.open("photo.jpg")
page = ai.ocr_pil_image(img)
print(page.text)
```

### Inspecting results

The `OcrResult` object contains a list of `OcrPage`s, each with `OcrBlock`s,
`OcrLine`s, and `OcrWord`s:

```python
for page in result.pages:
    print(f"--- Page {page.page_number} ({page.width}x{page.height}) ---")
    for block in page.blocks:
        for line in block.lines:
            print(line.text)
            for word in line.words:
                bb = word.bounding_box
                if bb:
                    print(f"  '{word.text}' at ({bb.x},{bb.y}) "
                          f"{bb.width}x{bb.height} "
                          f"conf={word.confidence:.2f}")
```

### Properties

```python
major, minor = ai.version        # e.g. (140, 20)
max_dim = ai.max_image_dimension  # e.g. 2048
```

## Output formats

### Plain text (`*_ocr.txt`)

Pages are separated by a form-feed character (`\f`).  Within a page, blocks
are separated by blank lines, and lines within a block are separated by
newlines.

### Structured JSON (`*_ocr.json`)

```json
{
  "pages": [
    {
      "page_number": 1,
      "width": 1377,
      "height": 2048,
      "blocks": [
        {
          "block_type": "paragraph",
          "lines": [
            {
              "text": "Hello world",
              "words": [
                {
                  "text": "Hello",
                  "confidence": 0.98,
                  "bounding_box": { "x": 50, "y": 100, "width": 120, "height": 30 }
                }
              ],
              "bounding_box": { "x": 50, "y": 100, "width": 300, "height": 30 }
            }
          ]
        }
      ]
    }
  ]
}
```

Bounding-box coordinates are in pixels, relative to the image that was actually
OCR'd (which may have been resized to fit within the library's maximum dimension).
The page's `width` and `height` reflect this OCR'd size.
