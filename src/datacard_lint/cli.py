"""datacard-lint: check a dataset card's content before you publish it.

    datacard-lint ./my-dataset                 a local folder with a README.md
    datacard-lint ./my-dataset/README.md       the card itself
    datacard-lint owner/name                   a dataset on the Hugging Face Hub (HF_TOKEN for private ones)

Exit status: 0 nothing failed, 1 a problem failed the check, 2 the card could not be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import __version__
from .card import parse_card
from .checks import ERROR, NOTE, PROFILES, WARNING, Problem, run_checks
from .sources import SourceError, open_source


def _github(problem: Problem) -> str:
    level = {ERROR: "error", WARNING: "warning"}.get(problem.severity, "notice")
    where = ",".join(p for p in (f"file={problem.file}" if problem.file else "", f"line={problem.line}" if problem.line else "") if p)
    message = problem.message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::{level} {where}::datacard-lint {problem.id}: {message}" if where else f"::{level}::datacard-lint {problem.id}: {message}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="datacard-lint", description="Check a dataset card's content: placeholders, empty sections, files it names, and its data files.")
    parser.add_argument("target", help="a folder, a README.md, or a Hugging Face dataset id (owner/name)")
    parser.add_argument("--revision", default="main", help="the Hub branch or commit to read (default main)")
    parser.add_argument("--profile", choices=PROFILES, default="default", help="'hive' also requires the HivemindOS agents' card layout")
    parser.add_argument("--require", action="append", default=[], metavar="HEADING", help="a section the card must have (repeat for more)")
    parser.add_argument("--no-data", action="store_true", help="skip reading CSV/TSV data files")
    parser.add_argument("--data-files", type=int, default=12, metavar="N", help="read at most N CSV/TSV files (default 12)")
    parser.add_argument("--strict", action="store_true", help="warnings fail the check too")
    parser.add_argument("--format", choices=("text", "json", "github"), default="text")
    parser.add_argument("--version", action="version", version=f"datacard-lint {__version__}")
    args = parser.parse_args(argv)

    try:
        source = open_source(args.target, revision=args.revision)
        text = source.readme()
        card = parse_card(text) if text is not None else None
    except SourceError as error:
        print(f"datacard-lint: {error}", file=sys.stderr)
        return 2
    problems = run_checks(card, source, profile=args.profile, require=args.require, data=not args.no_data, most_files=max(0, args.data_files))
    failing = [p for p in problems if p.severity == ERROR or (args.strict and p.severity == WARNING)]

    if args.format == "json":
        print(json.dumps({"target": source.name, "ok": not failing, "problems": [p.as_dict() for p in problems]}, indent=2))
    elif args.format == "github":
        for problem in problems:
            print(_github(problem))
    else:
        for problem in problems:
            where = f"{problem.file}:{problem.line}" if problem.file and problem.line else problem.file or ""
            label = "FAIL" if problem in failing else "NOTE" if problem.severity == NOTE else "WARN"
            print(f"{label} {problem.id}{f' ({where})' if where else ''}: {problem.message}")
        errors = sum(1 for p in problems if p.severity == ERROR)
        warnings = sum(1 for p in problems if p.severity == WARNING)
        print(f"{source.name}: {'ok' if not failing else 'failed'} ({errors} error{'s' * (errors != 1)}, {warnings} warning{'s' * (warnings != 1)})")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
