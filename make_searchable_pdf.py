"""
Build searchable PDFs from the original scans plus a parsed *_words.json file
(as written by docai_parse_ocr.py / locro_parse_ocr.py).

Each word is placed as invisible text (render_mode=3) at its normalized bbox,
in reading order (line_number, word_number_in_line), on top of the original page.

Input:
    <scans>/<book_name>.pdf
    <parsed>/<book_name>/<book_name>_words.json

Output:
    <output>/<book_name>_searchable.pdf
"""

import argparse
import json
import multiprocessing
import os
import queue
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

import fitz
from tqdm import tqdm


FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

# Fonts to try for non-Latin-1 text, in order: the bundled Noto Sans first, then
# system Devanagari fonts (Windows).
DEVANAGARI_FONTS = [
    os.path.join(FONTS_DIR, "NotoSans-VariableFont_wdth,wght.ttf"),
    "C:/Windows/Fonts/Nirmala.ttf",
    "C:/Windows/Fonts/Nirmala.ttc",
    "C:/Windows/Fonts/mangal.ttf",
]

_font_cache = {}
_glyph_cache = {}


def _load_font(path):
    if path not in _font_cache:
        try:
            _font_cache[path] = fitz.Font(fontfile=path) if os.path.isfile(path) else None
        except Exception:
            _font_cache[path] = None
    return _font_cache[path]


def _fonts():
    """Candidate fonts in priority order (Latin-1 helv first)."""
    if "chain" not in _font_cache:
        chain = [fitz.Font("helv")]
        chain += [f for f in (_load_font(p) for p in DEVANAGARI_FONTS) if f]
        _font_cache["chain"] = chain
    return _font_cache["chain"]


def _covers(font_idx, ch):
    key = (font_idx, ch)
    if key not in _glyph_cache:
        _glyph_cache[key] = bool(_fonts()[font_idx].has_glyph(ord(ch)))
    return _glyph_cache[key]


def font_for(text):
    """Return (font, text) where font can render text. Characters no font
    covers (e.g. stray Khmer punctuation from OCR noise) are dropped."""
    chain = _fonts()
    for idx, font in enumerate(chain):
        if all(ch.isspace() or _covers(idx, ch) for ch in text):
            return font, text
    # No single font covers everything: use the best-covering font and drop the rest.
    idx = max(range(len(chain)), key=lambda i: sum(_covers(i, c) for c in text if not c.isspace()))
    return chain[idx], "".join(c for c in text if c.isspace() or _covers(idx, c))


def load_words_by_page(words_json_path):
    with open(words_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pages = defaultdict(list)
    for word, occurrences in data["words"].items():
        for occ in occurrences:
            if occ.get("bbox") and occ.get("page_number") is not None:
                pages[occ["page_number"]].append((
                    occ.get("line_number") or 0,
                    occ.get("word_number_in_line") or 0,
                    word,
                    occ["bbox"],
                ))
    for items in pages.values():
        items.sort(key=lambda t: (t[0], t[1]))
    return pages


def overlay_page(page, items):
    width, height = page.rect.width, page.rect.height
    tw = fitz.TextWriter(page.rect)
    n = 0
    for _line, _idx, text, bbox in items:
        x0, x1 = bbox["x_min"] * width, bbox["x_max"] * width
        y0, y1 = bbox["y_min"] * height, bbox["y_max"] * height
        if x1 <= x0 or y1 <= y0:
            continue
        font, text = font_for(text)
        if not text.strip():
            continue
        size = max(1.0, (y1 - y0) * 0.8)
        # Shrink to fit if the text would be wider than its box.
        try:
            text_w = font.text_length(text, fontsize=size)
            if text_w > (x1 - x0):
                size = max(1.0, size * (x1 - x0) / text_w)
        except Exception:
            pass
        # trailing space keeps words separated on copy/extract
        tw.append(fitz.Point(x0, y1 - size * 0.1), text + " ", font=font, fontsize=size)
        n += 1
    tw.write_text(page, render_mode=3)  # invisible
    return n


def process_book(book_name, scans_dir, parsed_dir, out_dir, on_event=None):
    """on_event(kind, n): called with ('start', total_pages), ('page', 1) per page
    processed, and ('end', 0) after the PDF has been saved."""
    pdf_path = os.path.join(scans_dir, f"{book_name}.pdf")
    words_path = os.path.join(parsed_dir, book_name, f"{book_name}_words.json")
    if not os.path.isfile(pdf_path):
        return None, f"source pdf not found: {pdf_path}"
    if not os.path.isfile(words_path):
        return None, f"words json not found: {words_path}"

    pages = load_words_by_page(words_path)
    doc = fitz.open(pdf_path)
    n_words = 0
    if on_event:
        on_event("start", len(doc))
    for page_number in range(1, len(doc) + 1):
        items = pages.get(page_number)
        if items:
            n_words += overlay_page(doc[page_number - 1], items)
        if on_event:
            on_event("page", 1)

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{book_name}_searchable.pdf")
    doc.save(out_path, garbage=3, deflate=True)
    if on_event:
        on_event("end", 0)
    return {"out_path": out_path, "n_pages": len(doc), "n_words": n_words,
            "max_ocr_page": max(pages) if pages else 0}, None


def _run_book(task):
    book, scans, parsed, output, events = task
    on_event = (lambda kind, n: events.put((book, kind, n))) if events is not None else None
    try:
        return book, *process_book(book, scans, parsed, output, on_event)
    except Exception as e:  # keep one bad book from aborting the batch
        if on_event:
            on_event("end", 0)
        return book, None, f"failed: {e!r}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scans", default="scans", help="Folder with original <book>.pdf files")
    parser.add_argument("--parsed", default="parsed_output_docai",
                        help="Parsed output folder (default: parsed_output_docai)")
    parser.add_argument("--output", default="searchable_docai",
                        help="Where to write searchable PDFs (default: searchable_docai)")
    parser.add_argument("--book", default=None, help="Only this book (default: all in --parsed)")
    parser.add_argument("-j", "--jobs", type=int, default=min(4, os.cpu_count() or 1),
                        help="Books to process in parallel (default: min(4, CPU count))")
    args = parser.parse_args()

    books = [args.book] if args.book else sorted(
        d for d in os.listdir(args.parsed) if os.path.isdir(os.path.join(args.parsed, d)))
    positions = {b: i for i, b in enumerate(books)}
    bars = {}

    def handle(book, kind, n):
        if kind == "start":
            bars[book] = tqdm(total=n, desc=book, unit="pg",
                              position=positions[book] if parallel else 0,
                              leave=True, disable=None)
        elif book in bars:
            if kind == "page":
                bars[book].update(n)
            elif kind == "end":
                bars[book].close()

    parallel = args.jobs > 1 and len(books) > 1
    results = []
    if parallel:
        with multiprocessing.Manager() as mgr, ProcessPoolExecutor(max_workers=args.jobs) as pool:
            events = mgr.Queue()
            futures = [pool.submit(_run_book, (b, args.scans, args.parsed, args.output, events))
                       for b in books]
            while not all(f.done() for f in futures) or not events.empty():
                try:
                    handle(*events.get(timeout=0.1))
                except queue.Empty:
                    pass
            results = [f.result() for f in futures]
    else:
        class _Direct:  # same interface as the queue, handled in-process
            put = staticmethod(lambda ev: handle(*ev))

        results = [_run_book((b, args.scans, args.parsed, args.output, _Direct())) for b in books]

    for book, result, err in results:
        _report(book, result, err)


def _report(book, result, err):
    if err:
        tqdm.write(f"[skip] {book}: {err}")
        return
    tqdm.write(f"[done] {book} pages={result['n_pages']} words={result['n_words']} "
               f"-> {result['out_path']}")
    if result["max_ocr_page"] != result["n_pages"]:
        tqdm.write(f"        warning: OCR covers up to page {result['max_ocr_page']} "
                   f"but PDF has {result['n_pages']} pages")


if __name__ == "__main__":
    main()
