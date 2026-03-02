"""CLI for screen-ai-wrapper, powered by Typer."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Annotated, Optional

import typer

from .models import OcrResult
from .ocr import IMAGE_SUFFIXES, ScreenAI

app = typer.Typer(
    name="screen-ai-ocr",
    help="OCR documents and images using Chrome's screen-ai DLL.",
    add_completion=False,
)

SUPPORTED_SUFFIXES = {".pdf", *IMAGE_SUFFIXES}


def _parse_pages(spec: str) -> list[int]:
    """Parse a page specification like ``1-5``, ``1,3,5``, or ``1-3,7,10-12``.

    Returns a sorted list of 1-based page numbers.
    """
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            pages.update(range(lo, hi + 1))
        elif part.isdigit():
            pages.add(int(part))
        else:
            raise typer.BadParameter(f"Invalid page spec: {part!r}")
    return sorted(pages)


# ---------------------------------------------------------------------------
# ocr
# ---------------------------------------------------------------------------


def _write_outputs(
    result: OcrResult, input_path: Path, output_dir: Path | None,
) -> None:
    base = input_path.stem
    out = output_dir or input_path.parent

    txt_path = out / f"{base}_ocr.txt"
    txt_path.write_text(result.to_text(), encoding="utf-8")

    json_path = out / f"{base}_ocr.json"
    json_path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    typer.echo(f"  Text -> {txt_path}")
    typer.echo(f"  JSON -> {json_path}")


@app.command()
def ocr(
    file: Annotated[
        Path,
        typer.Argument(help="PDF or image file to OCR."),
    ],
    output_dir: Annotated[
        Optional[Path],
        typer.Option("-o", "--output-dir", help="Output directory (default: same as input)."),
    ] = None,
    text: Annotated[
        bool,
        typer.Option("--text", help="Print extracted text to stdout instead of writing files."),
    ] = False,
    pages_spec: Annotated[
        Optional[str],
        typer.Option(
            "--pages",
            help="Pages to OCR (PDF only).  Examples: 1  1-10  1,3,5  1-5,10-12",
        ),
    ] = None,
    light: Annotated[
        bool,
        typer.Option("--light", help="Use the smaller/faster OCR model."),
    ] = False,
    searchable_pdf: Annotated[
        Optional[Path],
        typer.Option(
            "--searchable-pdf",
            help="Write a searchable PDF with invisible text overlay (PDF input only).",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Verbose / debug logging."),
    ] = False,
) -> None:
    """OCR a document or image using Chrome's screen-ai."""
    _setup_logging(verbose)

    file = file.resolve()
    if not file.exists():
        typer.echo(f"Error: file not found: {file}", err=True)
        raise typer.Exit(code=1)

    suffix = file.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        typer.echo(
            f"Error: unsupported file type '{file.suffix}' "
            f"(expected {', '.join(sorted(SUPPORTED_SUFFIXES))})",
            err=True,
        )
        raise typer.Exit(code=1)

    pages = _parse_pages(pages_spec) if pages_spec else None

    if pages and suffix != ".pdf":
        typer.echo("Warning: --pages is ignored for image files.", err=True)
        pages = None

    if searchable_pdf and suffix != ".pdf":
        typer.echo("Error: --searchable-pdf requires a PDF input.", err=True)
        raise typer.Exit(code=1)

    ai = ScreenAI(light_mode=light)

    if searchable_pdf:
        result = ai.ocr_to_searchable_pdf(file, searchable_pdf, pages=pages)
        typer.echo(f"  Searchable PDF -> {searchable_pdf}")
    else:
        result = ai.ocr(file, pages=pages)

    page_count = len(result.pages)
    total_blocks = sum(len(p.blocks) for p in result.pages)

    if text:
        typer.echo(result.to_text())
    elif not searchable_pdf:
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
        _write_outputs(result, file, output_dir)

    typer.echo(f"Done. {page_count} page(s), {total_blocks} block(s).")


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


@app.command()
def download(
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Verbose / debug logging."),
    ] = False,
    model_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--model-dir",
            help="Directory to store the component (default: %%LOCALAPPDATA%%/screen_ai_wrapper).",
        ),
    ] = None,
) -> None:
    """Install the screen-ai component (DLL + models).

    Copies from Chrome's local component directory so the wrapper works
    independently of Chrome afterwards.  Chrome must have downloaded the
    Screen AI component at least once (chrome://components).
    """
    _setup_logging(verbose)

    from ._download import download_component

    try:
        result_dir = download_component(target_dir=model_dir)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"Installed to {result_dir}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-8s %(message)s",
    )
    logging.getLogger("PIL").setLevel(logging.WARNING)


def main() -> None:
    app()
