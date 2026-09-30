"""
Parse locro OCR output (one JSON file per book) into per-book CSV
(line-level, including normalized line bounding boxes) and JSON (word/token-level) outputs.

Input layout:
    ocr_results/<book_name>_ocr.json

Each file follows locro's own schema:
    { "pages": [ { "page_number": ..., "width": ..., "height": ...,
      "blocks": [ { "block_type": ..., "lines": [ { "text": ...,
      "bounding_box": {...}, "words": [ { "text": ..., "confidence": ...,
      "bounding_box": {"x", "y", "width", "height", "angle"} }, ... ] },
      ... ] }, ... ] }, ... ] }

Output layout (written next to ocr_results, in parsed_output_locro/):
    parsed_output_locro/<book_name>/<book_name>_lines.csv
    parsed_output_locro/<book_name>/<book_name>_words.json
    parsed_output_locro/<book_name>/<book_name>_words_trimmed.json
"""

import argparse
import csv
import json
import os
import re
import unicodedata
from collections import OrderedDict

DEVANAGARI_RANGE = (0x0900, 0x097F)
DEVANAGARI_CATEGORIES = {"Lo", "Lm", "Mn", "Mc"}
TRAILING_HYPHENS = "-‐‑‒–—―"


def is_devanagari_word(word):
    """True if every character is a Devanagari letter/mark (not punctuation,
    digits, or characters from other scripts)."""
    if not word:
        return False
    for ch in word:
        cp = ord(ch)
        if not (DEVANAGARI_RANGE[0] <= cp <= DEVANAGARI_RANGE[1]):
            return False
        if unicodedata.category(ch) not in DEVANAGARI_CATEGORIES:
            return False
    return True


def strip_line_wrap_hyphen(word):
    """Strip a trailing hyphen left over from an OCR line-wrap split, e.g.
    'दाणव-' -> 'दाणव'."""
    return word.rstrip(TRAILING_HYPHENS)


def natural_sort_key(filename):
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", filename)
    ]


def list_json_files(ocr_results_path):
    files = [
        f for f in os.listdir(ocr_results_path)
        if f.lower().endswith(".json") and os.path.isfile(os.path.join(ocr_results_path, f))
    ]
    return sorted(files, key=natural_sort_key)


def infer_book_name(json_file):
    """Strip the '.json' extension and a trailing '_ocr' suffix, if present."""
    stem = re.sub(r"\.json$", "", json_file, flags=re.IGNORECASE)
    stem = re.sub(r"_ocr$", "", stem, flags=re.IGNORECASE)
    return stem


def normalized_bbox(bbox, page_width, page_height, ndigits=4):
    if not bbox or not page_width or not page_height:
        return None
    x = bbox.get("x", 0)
    y = bbox.get("y", 0)
    w = bbox.get("width", 0)
    h = bbox.get("height", 0)
    result = {
        "x_min": round(x / page_width, ndigits),
        "y_min": round(y / page_height, ndigits),
        "x_max": round((x + w) / page_width, ndigits),
        "y_max": round((y + h) / page_height, ndigits),
    }
    angle = bbox.get("angle")
    if angle is not None:
        result["angle"] = round(angle, ndigits)
    return result


def write_words_json(f, book_name, words):
    """Write {"book_name": ..., "words": {word: [occurrence, ...]}} with the
    outer structure indented but each occurrence object compact on one line,
    to keep the file both readable and reasonably small."""
    f.write("{\n")
    f.write(f'  "book_name": {json.dumps(book_name, ensure_ascii=False)},\n')
    f.write('  "words": {\n')

    word_items = list(words.items())
    for wi, (word, occurrences) in enumerate(word_items):
        f.write(f'    {json.dumps(word, ensure_ascii=False)}: [\n')
        for oi, occurrence in enumerate(occurrences):
            line = json.dumps(occurrence, ensure_ascii=False, separators=(", ", ": "))
            comma = "," if oi < len(occurrences) - 1 else ""
            f.write(f"      {line}{comma}\n")
        comma = "," if wi < len(word_items) - 1 else ""
        f.write(f"    ]{comma}\n")

    f.write("  }\n")
    f.write("}\n")


def process_book(json_file, ocr_results_dir, out_dir):
    book_name = infer_book_name(json_file)
    file_path = os.path.join(ocr_results_dir, json_file)
    with open(file_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    csv_rows = []
    words = OrderedDict()

    for page in doc.get("pages", []):
        page_number = page.get("page_number")
        page_width = page.get("width")
        page_height = page.get("height")

        # Flatten blocks -> lines, in document order, so lines are numbered
        # consistently across the whole page (not restarted per block).
        lines = [line for block in page.get("blocks", []) for line in block.get("lines", [])]

        for line_idx, line in enumerate(lines, start=1):
            line_text = " ".join(line.get("text", "").splitlines())
            line_bbox = normalized_bbox(line.get("bounding_box"), page_width, page_height) or {}
            csv_rows.append({
                "book_name": book_name,
                "page_number": page_number,
                "line_number": line_idx,
                "line_data": line_text,
                "x_min": line_bbox.get("x_min", ""),
                "y_min": line_bbox.get("y_min", ""),
                "x_max": line_bbox.get("x_max", ""),
                "y_max": line_bbox.get("y_max", ""),
                "angle": line_bbox.get("angle", ""),
            })

        for line_idx, line in enumerate(lines, start=1):
            for word_idx, word in enumerate(line.get("words", []), start=1):
                word_text = word.get("text", "").strip()
                if not word_text:
                    continue

                bbox = normalized_bbox(word.get("bounding_box"), page_width, page_height)

                entry = {
                    "page_number": page_number,
                    "line_number": line_idx,
                    "word_number_in_line": word_idx,
                    "bbox": bbox,
                    "confidence": word.get("confidence"),
                }

                words.setdefault(word_text, []).append(entry)

    os.makedirs(out_dir, exist_ok=True)

    csv_path = os.path.join(out_dir, f"{book_name}_lines.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=[
                "book_name", "page_number", "line_number", "line_data",
                "x_min", "y_min", "x_max", "y_max", "angle",
            ],
        )
        writer.writeheader()
        writer.writerows(csv_rows)

    words_path = os.path.join(out_dir, f"{book_name}_words.json")
    with open(words_path, "w", encoding="utf-8") as f:
        write_words_json(f, book_name, words)

    trimmed_words = OrderedDict()
    for word, occurrences in words.items():
        normalized = strip_line_wrap_hyphen(word)
        if not is_devanagari_word(normalized):
            continue
        trimmed_words.setdefault(normalized, []).extend(occurrences)

    trimmed_words_path = os.path.join(out_dir, f"{book_name}_words_trimmed.json")
    with open(trimmed_words_path, "w", encoding="utf-8") as f:
        write_words_json(f, book_name, trimmed_words)

    return {
        "book_name": book_name,
        "csv_path": csv_path,
        "words_path": words_path,
        "trimmed_words_path": trimmed_words_path,
        "n_lines": len(csv_rows),
        "n_words": len(words),
        "n_words_trimmed": len(trimmed_words),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ocr-results", default="ocr_results",
        help="Path to the ocr_results directory (default: ocr_results)",
    )
    parser.add_argument(
        "--output", default="parsed_output_locro",
        help="Path to write per-book parsed output (default: parsed_output_locro)",
    )
    parser.add_argument(
        "--book", default=None,
        help="Only process the json file whose inferred book name matches this "
             "(default: all books)",
    )
    args = parser.parse_args()

    json_files = list_json_files(args.ocr_results)
    if args.book:
        json_files = [f for f in json_files if infer_book_name(f) == args.book]

    for json_file in json_files:
        book_name = infer_book_name(json_file)
        out_dir = os.path.join(args.output, book_name)
        result = process_book(json_file, args.ocr_results, out_dir)
        print(
            f"[done] {json_file} -> book_name={result['book_name']!r} "
            f"lines={result['n_lines']} words={result['n_words']} "
            f"words_trimmed={result['n_words_trimmed']}"
        )
        print(f"        csv:           {result['csv_path']}")
        print(f"        words:         {result['words_path']}")
        print(f"        words_trimmed: {result['trimmed_words_path']}")


if __name__ == "__main__":
    main()
