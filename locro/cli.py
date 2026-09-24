"""CLI for locro, powered by Typer."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated, Optional

import typer
from tqdm import tqdm

from ._platform import default_model_dir_display
from .ocr import IMAGE_SUFFIXES, ScreenAI

app = typer.Typer(
    name="locro",
    help="OCR documents and images using Chrome's screen-ai library.",
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)

SUPPORTED_SUFFIXES = {".pdf", *IMAGE_SUFFIXES}

# Internal protocol for reporting per-page progress from a `-j` subprocess
# worker back to the parent, which renders the real tqdm bar (the worker's
# own stdout is a pipe, not a terminal, so it can't render one itself).
_PROGRESS_ENV = "LOCRO_MACHINE_PROGRESS"
_PROGRESS_PREFIX = "@@locro-progress@@"


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


def _validate_file(file: Path, *, pages_spec: Optional[str], searchable_pdf: bool) -> Path:
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

    if pages_spec and suffix != ".pdf":
        typer.echo(f"Warning: --pages is ignored for image file {file.name}.", err=True)

    if searchable_pdf and suffix != ".pdf":
        typer.echo(f"Error: --searchable-pdf requires a PDF input ({file.name}).", err=True)
        raise typer.Exit(code=1)

    return file


def _count_pages(file: Path, pages: list[int] | None) -> int:
    """Estimate how many pages will actually be OCR'd, for sizing a progress bar."""
    if file.suffix.lower() != ".pdf":
        return 1
    if pages is not None:
        return len(pages)
    try:
        import fitz  # PyMuPDF

        with fitz.open(file) as doc:
            return len(doc)
    except Exception:
        return 1


def _ocr_one(
    ai: ScreenAI,
    file: Path,
    *,
    output_dir: Path | None,
    text: bool,
    pages: list[int] | None,
    searchable_pdf: Path | None,
    on_page: Callable[[int, int], None] | None = None,
) -> str:
    """Run OCR on a single file and return a human-readable report."""
    if file.suffix.lower() != ".pdf":
        pages = None

    t0 = time.monotonic()
    lines: list[str] = []

    if searchable_pdf:
        result = ai.ocr_to_searchable_pdf(file, searchable_pdf, pages=pages, on_page=on_page)
        lines.append(f"  Searchable PDF -> {searchable_pdf}")
    else:
        result = ai.ocr(file, pages=pages, on_page=on_page)

    page_count = len(result.pages)
    total_blocks = sum(len(p.blocks) for p in result.pages)

    if text:
        lines.append(result.to_text())
    else:
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
        base = file.stem
        out = output_dir or file.parent
        txt_path = out / f"{base}_ocr.txt"
        txt_path.write_text(result.to_text(), encoding="utf-8")
        json_path = out / f"{base}_ocr.json"
        json_path.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        lines.append(f"  Text -> {txt_path}")
        lines.append(f"  JSON -> {json_path}")

    elapsed = time.monotonic() - t0
    time_str = f"{elapsed / 60:.1f} minutes" if elapsed >= 60 else f"{elapsed:.1f} seconds"
    lines.append(f"Done. {page_count} page(s), {total_blocks} block(s). Total time: {time_str}.")
    return "\n".join(lines)


def _searchable_pdf_for(searchable_pdf: Path | None, file: Path, *, batch: bool) -> Path | None:
    """Resolve the per-file --searchable-pdf output path.

    With multiple input files, ``--searchable-pdf`` is treated as an output
    *directory* (since one fixed path can't serve every file).
    """
    if searchable_pdf is None:
        return None
    if not batch:
        return searchable_pdf
    searchable_pdf.mkdir(parents=True, exist_ok=True)
    return searchable_pdf / f"{file.stem}_searchable.pdf"


def _subprocess_args(
    file: Path,
    *,
    output_dir: Path | None,
    text: bool,
    pages_spec: Optional[str],
    light: bool,
    searchable_pdf: Path | None,
    verbose: bool,
) -> list[str]:
    args = [sys.executable, "-m", "locro.cli", "ocr", str(file)]
    if output_dir:
        args += ["-o", str(output_dir)]
    if text:
        args += ["--text"]
    if pages_spec:
        args += ["-p", pages_spec]
    if light:
        args += ["--light"]
    if searchable_pdf:
        args += ["-s", str(searchable_pdf)]
    if verbose:
        args += ["-v"]
    return args


def _expand_inputs(paths: list[Path]) -> list[Path]:
    """Expand any directories in ``paths`` into the supported files they contain."""
    expanded: list[Path] = []
    for p in paths:
        rp = p.resolve()
        if rp.is_dir():
            found = sorted(
                f for f in rp.iterdir()
                if f.is_file() and f.suffix.lower() in SUPPORTED_SUFFIXES
            )
            if not found:
                typer.echo(f"Warning: no supported files found in {rp}", err=True)
            expanded.extend(found)
        else:
            expanded.append(p)
    return expanded


@app.command()
def ocr(
    files: Annotated[
        list[Path],
        typer.Argument(
            help="PDF/image file(s) to OCR, and/or directories of them "
                 "(non-recursive). Pass several for batch processing.",
        ),
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
            "-p", "--pages",
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
            "-s", "--searchable-pdf",
            help="Also write a searchable PDF with invisible text overlay (PDF input "
                 "only), alongside the usual .txt/.json output. With multiple input "
                 "files, this is treated as an output directory.",
        ),
    ] = None,
    jobs: Annotated[
        int,
        typer.Option(
            "-j", "--jobs",
            help="OCR this many files in parallel (each in its own process). "
                 "Only takes effect with multiple input files; default is sequential.",
        ),
    ] = 1,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Verbose / debug logging."),
    ] = False,
) -> None:
    """OCR one or more documents/images using Chrome's screen-ai."""
    _setup_logging(verbose)

    files = _expand_inputs(files)
    if not files:
        typer.echo("Error: no input files.", err=True)
        raise typer.Exit(code=1)

    batch = len(files) > 1
    resolved = [
        _validate_file(f, pages_spec=pages_spec, searchable_pdf=searchable_pdf is not None)
        for f in files
    ]

    t0 = time.monotonic()

    if batch and jobs > 1:
        _run_parallel(
            resolved,
            output_dir=output_dir,
            text=text,
            pages_spec=pages_spec,
            light=light,
            searchable_pdf=searchable_pdf,
            jobs=jobs,
            verbose=verbose,
        )
    else:
        machine_progress = os.environ.get(_PROGRESS_ENV) == "1"
        pages = _parse_pages(pages_spec) if pages_spec else None
        ai = ScreenAI(light_mode=light)
        for file in resolved:
            if batch:
                typer.echo(f"=== {file.name} ===")
            sp = _searchable_pdf_for(searchable_pdf, file, batch=batch)

            on_page: Callable[[int, int], None] | None
            bar = None
            if machine_progress:
                # Running as a -j subprocess worker: no real terminal to draw
                # a bar on, so just emit a marker line the parent can parse.
                def on_page(n: int, total: int) -> None:
                    typer.echo(f"{_PROGRESS_PREFIX} {n}/{total}", err=False)
            elif not verbose and sys.stderr.isatty():
                bar = tqdm(
                    total=_count_pages(file, pages), desc=file.name, unit="pg",
                    leave=False, disable=None,
                )
                on_page = lambda n, total, _bar=bar: _bar.update(1)  # noqa: E731
            else:
                on_page = None

            try:
                report = _ocr_one(
                    ai, file, output_dir=output_dir, text=text, pages=pages,
                    searchable_pdf=sp, on_page=on_page,
                )
            finally:
                if bar is not None:
                    bar.close()
            typer.echo(report)

    if batch:
        elapsed = time.monotonic() - t0
        time_str = f"{elapsed / 60:.1f} minutes" if elapsed >= 60 else f"{elapsed:.1f} seconds"
        typer.echo(f"\nAll {len(resolved)} file(s) done. Total time: {time_str}.")


def _run_parallel(
    files: list[Path],
    *,
    output_dir: Path | None,
    text: bool,
    pages_spec: Optional[str],
    light: bool,
    searchable_pdf: Path | None,
    jobs: int,
    verbose: bool,
) -> None:
    """OCR multiple files in parallel, one subprocess per file.

    Screen-AI's native library keeps process-global state (loaded model
    files, callbacks) and gives no thread-safety guarantees, so we can't
    just OCR files concurrently on threads or share one ``ScreenAI``
    instance across them.  Separate OS processes -- each loading its own
    copy of the library -- sidestep that entirely.
    """
    if searchable_pdf is not None:
        searchable_pdf.mkdir(parents=True, exist_ok=True)

    n = len(files)
    pages = _parse_pages(pages_spec) if pages_spec else None
    typer.echo(f"OCRing {n} files with -j{jobs}...")

    # -v wants the raw, unfiltered log stream from each file, which doesn't
    # play well with progress bars re-drawing on top of it -- fall back to
    # the simple "dump everything once it's done" behavior in that case.
    # Likewise skip bars when stderr isn't a real terminal (redirected to a
    # file, CI, etc.) -- they'd just emit escape-code noise.
    show_bars = not verbose and sys.stderr.isatty()

    def run_one(idx: int, file: Path, bar: "tqdm | None") -> tuple[Path, int, str]:
        sp = _searchable_pdf_for(searchable_pdf, file, batch=True)
        args = _subprocess_args(
            file,
            output_dir=output_dir,
            text=text,
            pages_spec=pages_spec,
            light=light,
            searchable_pdf=sp,
            verbose=verbose,
        )
        env = dict(os.environ)
        if bar is not None:
            env[_PROGRESS_ENV] = "1"

        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
        )
        kept: list[str] = []
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            if bar is not None and line.startswith(_PROGRESS_PREFIX):
                done_str, _, total_str = line[len(_PROGRESS_PREFIX):].strip().partition("/")
                try:
                    bar.n = min(int(done_str), int(total_str))
                    bar.refresh()
                except ValueError:
                    pass
            else:
                kept.append(line)
        proc.wait()
        if bar is not None:
            bar.n = bar.total
            bar.refresh()
        return file, proc.returncode, "\n".join(kept).strip()

    bars: list["tqdm | None"] = (
        [
            tqdm(
                total=_count_pages(f, pages), desc=f.name, unit="pg",
                position=i, leave=True, disable=None,
            )
            for i, f in enumerate(files)
        ]
        if show_bars
        else [None] * n
    )

    completed = 0
    failed = 0
    try:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(run_one, i, f, bars[i]): f for i, f in enumerate(files)
            }
            for future in as_completed(futures):
                file, returncode, output = future.result()
                completed += 1
                write = bars[0].write if show_bars and bars else typer.echo
                if returncode != 0:
                    failed += 1
                    write(f"\n=== [{completed}/{n}] {file.name} (FAILED) ===")
                    if output:
                        write(output)
                elif not show_bars:
                    write(f"\n=== [{completed}/{n}] {file.name} ===")
                    if output:
                        write(output)
                # else: the bar itself is the success report; stay quiet.
    finally:
        for bar in bars:
            if bar is not None:
                bar.close()

    if failed:
        typer.echo(f"\n{failed} of {n} file(s) failed.", err=True)
        raise typer.Exit(code=1)


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
            help=f"Directory to store the component (default: {default_model_dir_display()}).",
        ),
    ] = None,
) -> None:
    """Install the screen-ai component (library + models).

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
# export
# ---------------------------------------------------------------------------


@app.command()
def export(
    output: Annotated[
        Optional[Path],
        typer.Option(
            "-o", "--output",
            help="Output zip path (default: ~/Dropbox/bin/screen-ai-{platform}.zip).",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Verbose / debug logging."),
    ] = False,
) -> None:
    """Export the installed screen-ai component as a zip file.

    Creates a portable zip that can be used as an alternative installation
    source (e.g. via Dropbox) on machines where ``locro download`` cannot
    find Chrome's component.
    """
    _setup_logging(verbose)

    from ._download import export_to_zip

    try:
        result = export_to_zip(zip_path=output)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"Exported to {result}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _setup_logging(verbose: bool) -> None:
    # Non-verbose runs show a progress bar per file instead of the raw
    # per-page/per-call log lines -- WARNING keeps those two from clobbering
    # each other on the same stream.  -v opts back into the full firehose.
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)-8s %(message)s",
    )
    logging.getLogger("PIL").setLevel(logging.WARNING)


def main() -> None:
    # On Windows, stdout/stderr default to the console's legacy codepage
    # (e.g. cp1252), which can't represent most OCR'd non-Latin scripts
    # (Devanagari, Cyrillic, ...) and raises UnicodeEncodeError on --text /
    # piped output.  Force UTF-8 so printing never crashes.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    app()


if __name__ == "__main__":
    main()
