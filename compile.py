#!/usr/bin/env python3
"""Compile one AGTOL LaTeX document to PDF and optional DOCX/Markdown."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parent

AUXILIARY_SUFFIXES = (
    ".acn",
    ".acr",
    ".alg",
    ".aux",
    ".bbl",
    ".bcf",
    ".blg",
    ".brf",
    ".dvi",
    ".fdb_latexmk",
    ".fls",
    ".idx",
    ".ilg",
    ".ind",
    ".lof",
    ".log",
    ".lot",
    ".out",
    ".run.xml",
    ".synctex.gz",
    ".toc",
    ".xdv",
)

POEM_TITLE_RE = re.compile(r"\\poemtitle\{([^{}]*)\}")

PANDOC_POEM_FILTER = r'''
local function has_class(div, wanted)
  for _, class in ipairs(div.classes) do
    if class == wanted then
      return true
    end
  end
  return false
end

function Div(div)
  if has_class(div, "poem") or has_class(div, "stanza") then
    return div.content
  end
end
'''

PANDOC_MARKDOWN_TEMPLATE = r'''# $title$

$for(author)$*$author$*
$endfor$
$body$
'''


class BuildError(RuntimeError):
    """A concise error suitable for command-line output."""


def require_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise BuildError(f"required command not found on PATH: {name}")
    return executable


def run_command(command: Sequence[str], working_directory: Path) -> None:
    completed = subprocess.run(
        command,
        cwd=str(working_directory),
        text=True,
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        output = completed.stdout.rstrip()
        message = f"command failed ({completed.returncode}): {shlex.join(command)}"
        if output:
            message = f"{message}\n\n{output}"
        raise BuildError(message)


def roman(number: int) -> str:
    numerals = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    result = []
    for value, numeral in numerals:
        while number >= value:
            result.append(numeral)
            number -= value
    return "".join(result)


def pandoc_source(source: Path) -> str:
    """Make poemscol titles explicit without changing the author's source."""
    text = source.read_text(encoding="utf-8")
    automatic_title = 0

    def replace_title(match: re.Match[str]) -> str:
        nonlocal automatic_title
        title = match.group(1).strip()
        if title == r"\step":
            automatic_title += 1
            title = roman(automatic_title)
        return rf"\subsection*{{{title}}}"

    return POEM_TITLE_RE.sub(replace_title, text)


def clean_auxiliary_files(directory: Path, stem: str) -> None:
    for suffix in AUXILIARY_SUFFIXES:
        auxiliary = directory / f"{stem}{suffix}"
        if auxiliary.is_file():
            auxiliary.unlink()


def resolve_source(directory_arg: str, file_arg: str) -> tuple[Path, Path]:
    directory = (ROOT / directory_arg).resolve()
    try:
        directory.relative_to(ROOT)
    except ValueError as exc:
        raise BuildError(f"directory must be inside {ROOT}: {directory_arg}") from exc
    if not directory.is_dir():
        raise BuildError(f"directory not found: {directory}")

    file_path = Path(file_arg)
    if file_path.is_absolute() or len(file_path.parts) != 1:
        raise BuildError("the TeX file must be a filename inside the selected directory")
    source = directory / file_path
    if source.suffix.lower() != ".tex":
        raise BuildError(f"expected a .tex file: {file_arg}")
    if not source.is_file():
        raise BuildError(f"TeX file not found: {source}")
    return directory, source


def build(directory: Path, source: Path, build_docx: bool, build_md: bool) -> None:
    latexmk = require_tool("latexmk")
    pandoc = require_tool("pandoc") if build_docx or build_md else None
    stem = source.stem

    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{stem}_build_", dir=str(directory)
        ) as temporary_dir:
            staging = Path(temporary_dir)
            staged_pdf = staging / f"{stem}.pdf"

            print(f"Compiling {source.name} to PDF")
            run_command(
                [
                    latexmk,
                    "-pdf",
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-file-line-error",
                    f"-outdir={staging}",
                    f"-jobname={stem}",
                    source.name,
                ],
                directory,
            )
            if not staged_pdf.is_file():
                raise BuildError(f"latexmk did not create {staged_pdf.name}")

            generated = [(staged_pdf, directory / staged_pdf.name)]

            if build_docx or build_md:
                converted_source = staging / source.name
                poem_filter = staging / "poems.lua"
                converted_source.write_text(pandoc_source(source), encoding="utf-8")
                poem_filter.write_text(PANDOC_POEM_FILTER, encoding="utf-8")

                if build_docx:
                    staged_docx = staging / f"{stem}.docx"
                    print(f"Converting {source.name} to DOCX")
                    run_command(
                        [
                            pandoc,
                            str(converted_source),
                            "--from=latex",
                            "--to=docx",
                            "--lua-filter",
                            str(poem_filter),
                            "--resource-path",
                            str(directory),
                            "--output",
                            str(staged_docx),
                        ],
                        directory,
                    )
                    if not staged_docx.is_file():
                        raise BuildError(f"pandoc did not create {staged_docx.name}")
                    generated.append((staged_docx, directory / staged_docx.name))

                if build_md:
                    staged_md = staging / f"{stem}.md"
                    markdown_template = staging / "substack.md"
                    markdown_template.write_text(
                        PANDOC_MARKDOWN_TEMPLATE, encoding="utf-8"
                    )
                    print(f"Converting {source.name} to clean Markdown")
                    run_command(
                        [
                            pandoc,
                            str(converted_source),
                            "--from=latex",
                            "--to=gfm",
                            "--standalone",
                            "--wrap=none",
                            "--lua-filter",
                            str(poem_filter),
                            "--template",
                            str(markdown_template),
                            "--resource-path",
                            str(directory),
                            "--output",
                            str(staged_md),
                        ],
                        directory,
                    )
                    if not staged_md.is_file():
                        raise BuildError(f"pandoc did not create {staged_md.name}")
                    generated.append((staged_md, directory / staged_md.name))

            for staged_file, destination in generated:
                os.replace(staged_file, destination)
    finally:
        clean_auxiliary_files(directory, stem)

    print(f"PDF:  {directory / f'{stem}.pdf'}")
    if build_docx:
        print(f"DOCX: {directory / f'{stem}.docx'}")
    if build_md:
        print(f"MD:   {directory / f'{stem}.md'}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile a TeX file located in an AGTOL subdirectory."
    )
    parser.add_argument("directory", help="subdirectory, for example Paris")
    parser.add_argument("tex_file", help="TeX filename, for example Paris.tex")
    parser.add_argument(
        "--docx", action="store_true", help="also generate a DOCX file"
    )
    parser.add_argument(
        "--md",
        action="store_true",
        help="also generate clean Markdown for rendering and copy/paste",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        directory, source = resolve_source(args.directory, args.tex_file)
        build(directory, source, args.docx, args.md)
        return 0
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nBuild interrupted; temporary files cleaned.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
