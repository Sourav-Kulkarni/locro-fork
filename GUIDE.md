# screen-ai-wrapper -- Usage Guide

## Prerequisites

1. **Windows 10/11 (64-bit)** -- the wrapper calls `chrome_screen_ai.dll` which is a Windows DLL.

2. **Google Chrome installed** -- the DLL and its model files ship as a Chrome
   component.  Chrome downloads them automatically; you just need to have
   Chrome installed and to have opened it at least once.

3. **Python 3.12+**.

### Verifying the screen-ai component

The DLL lives under Chrome's user-data directory:

```
%LOCALAPPDATA%\Google\Chrome\User Data\screen_ai\<version>\chrome_screen_ai.dll
```

If that path doesn't exist, open Chrome, visit `chrome://components`, find
**Screen AI** and click *Check for update*.

## Installation

Clone the repository and install in editable mode:

```bash
git clone <repo-url>
cd screen-ai-wrapper
pip install -e .
```

This installs the `screen-ai-ocr` command and the `screen_ai_wrapper` Python
package along with all dependencies (Pillow, PyMuPDF, typer).

## CLI usage

After installation the `screen-ai-ocr` command is available on your PATH.

### OCR an image

```bash
screen-ai-ocr photo.jpg
```

Produces `photo_ocr.txt` (plain text) and `photo_ocr.json` (structured) in the
same directory as the input file.

### OCR a PDF

```bash
screen-ai-ocr document.pdf
```

Each page is rendered to an image internally and OCR'd.  The outputs contain
all pages.

### Specify an output directory

```bash
screen-ai-ocr scan.png -o results/
```

### Print text to stdout

```bash
screen-ai-ocr scan.png --text
```

Useful for piping into other tools:

```bash
screen-ai-ocr invoice.pdf --text | grep "Total"
```

### Verbose logging

```bash
screen-ai-ocr document.pdf -v
```

Shows DLL loading, image resize, and per-page statistics.

## Library / API usage

```python
from screen_ai_wrapper import ScreenAI

ai = ScreenAI()
```

The constructor auto-discovers the DLL and initialises the OCR pipeline.
You can pass a custom `model_dir` if needed:

```python
from pathlib import Path
ai = ScreenAI(model_dir=Path(r"C:\custom\path\to\screen_ai\140.20"))
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
OCR'd (which may have been resized to fit within the DLL's maximum dimension).
The page's `width` and `height` reflect this OCR'd size.
