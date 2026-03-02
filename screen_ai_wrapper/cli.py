"""CLI for screen-ai-wrapper, powered by Typer."""

from __future__ import annotations

import json
import logging
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
        typer.Argument(help="PDF, JPG, or PNG file to OCR."),
    ],
    output_dir: Annotated[
        Optional[Path],
        typer.Option("-o", "--output-dir", help="Output directory (default: same as input)."),
    ] = None,
    text: Annotated[
        bool,
        typer.Option("--text", help="Print extracted text to stdout instead of writing files."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Verbose / debug logging."),
    ] = False,
) -> None:
    """OCR a document or image using Chrome's screen-ai."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-8s %(message)s",
    )
    logging.getLogger("PIL").setLevel(logging.WARNING)

    file = file.resolve()
    if not file.exists():
        typer.echo(f"Error: file not found: {file}", err=True)
        raise typer.Exit(code=1)

    if file.suffix.lower() not in SUPPORTED_SUFFIXES:
        typer.echo(
            f"Error: unsupported file type '{file.suffix}' (expected PDF, JPG, or PNG)",
            err=True,
        )
        raise typer.Exit(code=1)

    ai = ScreenAI()
    result = ai.ocr(file)

    page_count = len(result.pages)
    total_blocks = sum(len(p.blocks) for p in result.pages)

    if text:
        typer.echo(result.to_text())
    else:
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
        _write_outputs(result, file, output_dir)
        typer.echo(f"Done. {page_count} page(s), {total_blocks} block(s).")


def main() -> None:
    app()
