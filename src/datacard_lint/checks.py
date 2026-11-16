"""The checks. Each one reads the card and the dataset's file list, and returns problems; none of them changes anything.

What Hugging Face already checks is not repeated here: `huggingface_hub`'s RepoCard.validate() sends the YAML header
to the Hub's validator. These are the content problems that pass that check: a section left as the template's
"[More Information Needed]", a section with nothing in it, a card that names a file the dataset does not have, a data
file whose rows do not match its header or the columns the card declares, a link column with empty or non-link rows.
"""

from __future__ import annotations

import csv
import difflib
import io
import posixpath
import re
import sys
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from .card import PLACEHOLDER, Card, section_is_empty
from .sources import HUB, HubSource, Source

ERROR, WARNING, NOTE = "error", "warning", "note"  # a note says what was not checked; it never fails
DATA_MOST_BYTES = 20 * 1024 * 1024  # one data file
DATA_MOST_TOTAL = 60 * 1024 * 1024  # all of them, in one run
DATA_MOST_FILES = 12
# Columns whose name says they hold links. One counts as a link column only when most of its filled cells are links:
# on 2026-11-16, four datasets on the Hub had a 'source' column of names ("AWS Price List", "golf"), not links.
LINK_COLUMNS = {"source", "url", "link", "href", "uri", "source_url", "sourceurl", "source_link", "homepage", "page_url"}
_URL = re.compile(r"^https?://\S+$")
SHOWN = 3  # line numbers shown per problem before "and N more"

# The "hive" profile: the layout HivemindOS agents' cards use (632's attribution-docs-check and dataset-card-lint,
# 547's hf-dataset-card-lint-fixtures). 547 wrote "What could not be checked" where 632 wrote "What I could not check":
# the same section, so either spelling counts.
HIVE_SECTIONS: Dict[str, Sequence[str]] = {
    "What it is": (),
    "Columns": (),
    "How it was made": (),
    "What I could not check": ("What could not be checked",),
    "Licence": ("License",),
}


@dataclass
class Problem:
    id: str
    severity: str
    message: str
    file: Optional[str] = None
    line: Optional[int] = None

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _lines(numbers: List[int]) -> str:
    shown = ", ".join(str(n) for n in numbers[:SHOWN])
    return shown + (f" and {len(numbers) - SHOWN} more" if len(numbers) > SHOWN else "")


# ---- The YAML header ------------------------------------------------------------------------------------------------

def check_header(card: Card, readme: str) -> List[Problem]:
    if not card.has_frontmatter:
        return [Problem("no_metadata", WARNING, "the card has no YAML header (---), so the Hub has no licence, language or task to show or search by", readme, 1)]
    if not card.frontmatter_closed:
        return [Problem("metadata_unclosed", ERROR, "the YAML header opens with --- but never closes", readme, 1)]
    if card.yaml_error:
        return [Problem("metadata_invalid", ERROR, f"the YAML header does not parse: {card.yaml_error}", readme, 1)]
    meta = card.metadata if isinstance(card.metadata, dict) else {}
    if card.metadata is not None and not isinstance(card.metadata, dict):
        return [Problem("metadata_invalid", ERROR, "the YAML header is not a set of keys", readme, 1)]
    out: List[Problem] = []
    licence = meta.get("license")
    if licence in (None, "", []):
        out.append(Problem("no_license", WARNING, "the YAML header names no license, so nobody knows if they may use the data", readme, 1))
    elif licence == "other" and not (meta.get("license_name") or meta.get("license_link")):
        out.append(Problem("license_unnamed", WARNING, "license is 'other' but no license_name or license_link says which", readme, 1))
    return out


# ---- The text ----------------------------------------------------------------------------------------------------------

_CODE = re.compile(r"^\s*(```|~~~).*?^\s*\1.*?$", re.S | re.M)


def _prose(text: str) -> str:
    """Text without its fenced code blocks: a card that quotes the template in code has not left it in."""
    return _CODE.sub("", text)


def check_text(card: Card, readme: str) -> List[Problem]:
    out: List[Problem] = []
    for section in card.sections:
        title = section.heading.text
        count = _prose(section.body).count(PLACEHOLDER)
        if count:
            out.append(Problem("placeholder", WARNING, f"'{title}' still has the template's {PLACEHOLDER}" + (f" ({count} times)" if count > 1 else ""), readme, section.heading.line))
        elif section_is_empty(section):
            out.append(Problem("empty_section", WARNING, f"'{title}' has nothing under it", readme, section.heading.line))
    if not card.sections and _prose(card.text).count(PLACEHOLDER):
        out.append(Problem("placeholder", WARNING, f"the card still has the template's {PLACEHOLDER}", readme, card.body_start_line))
    return out


def _norm(heading: str) -> str:
    return re.sub(r"[\s:.]+$", "", re.sub(r"\s+", " ", heading.strip())).lower()


def check_sections(card: Card, readme: str, required: Dict[str, Sequence[str]]) -> List[Problem]:
    """Each required section is a heading with that text (or one of its other spellings), in any case. A heading close
    to it but not it is reported as misspelled, with what the card has."""
    have = [h for h in card.headings]
    names = [_norm(h.text) for h in have]
    out: List[Problem] = []
    for name, others in required.items():
        wanted = [_norm(name), *(_norm(o) for o in others)]
        if any(n in wanted or any(w in n for w in wanted) for n in names):
            continue
        close = difflib.get_close_matches(wanted[0], names, n=1, cutoff=0.7)
        if close:
            heading = have[names.index(close[0])]
            out.append(Problem("heading_misspelled", ERROR, f"the '{name}' section is spelled '{heading.text}'", readme, heading.line))
        else:
            out.append(Problem("missing_section", ERROR, f"no '{name}' section", readme))
    return out


def check_title(card: Card, readme: str) -> List[Problem]:
    first = next((line for line in card.text.split("\n")[card.body_start_line - 1:] if line.strip()), "")
    if not first.lstrip().startswith("#"):
        return [Problem("no_title", ERROR, "the card's first line after the header is not a heading", readme, card.body_start_line)]
    return []


# ---- Files the card names ----------------------------------------------------------------------------------------------

def glob_regex(pattern: str) -> "re.Pattern[str]":
    """A data_files pattern as the Hub reads it: ** crosses folders, * and ? stay within one, [..] is a class."""
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        elif c == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out += re.escape(c)
                i += 1
            else:
                body = pattern[i + 1:end]
                out += "[" + ("^" + body[1:] if body.startswith("!") else body) + "]"
                i = end + 1
        else:
            out += re.escape(c)
            i += 1
    return re.compile(out + r"\Z")


def _patterns(data_files) -> List[str]:
    if isinstance(data_files, str):
        return [data_files]
    out: List[str] = []
    if isinstance(data_files, dict):
        data_files = [data_files]
    for item in data_files if isinstance(data_files, list) else []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            path = item.get("path")
            out.extend([path] if isinstance(path, str) else [p for p in path or [] if isinstance(p, str)])
    return out


def configs_of(meta) -> List[dict]:
    configs = meta.get("configs") if isinstance(meta, dict) else None
    return [c for c in configs if isinstance(c, dict)] if isinstance(configs, list) else []


def matching(pattern: str, files: Iterable[str]) -> List[str]:
    pattern = pattern[2:] if pattern.startswith("./") else pattern
    rx = glob_regex(pattern)
    return sorted(f for f in files if rx.match(f))


def _listing_cut(source: Source) -> List[Problem]:
    return [Problem("files_not_listed", NOTE, f"the dataset has more files than were listed ({len(source.files):,}), so files the card names were not checked")]


def check_data_files(card: Card, source: Source) -> List[Problem]:
    if not source.complete:
        return _listing_cut(source) if (configs_of(card.metadata) or card.links) else []
    out: List[Problem] = []
    for config in configs_of(card.metadata):
        name = config.get("config_name", "default")
        for pattern in _patterns(config.get("data_files")):
            if not matching(pattern, source.files):
                out.append(Problem("missing_file", ERROR, f"config '{name}' names data_files '{pattern}', and no file in the dataset matches it", source.readme_path, 1))
    return out


def _link_path(target: str, source: Source) -> Optional[str]:
    """The dataset-relative path a link points at, or None when it points elsewhere."""
    if isinstance(source, HubSource):
        prefix = f"{HUB}/datasets/{source.repo_id}/"
        if target.startswith(prefix):
            rest = target[len(prefix):].split("?")[0].split("#")[0]
            parts = rest.split("/", 2)
            if len(parts) == 3 and parts[0] in ("blob", "resolve", "raw"):
                return urllib.parse.unquote(parts[2])
            return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith(("#", "//", "/")):
        return None
    path = urllib.parse.unquote(target.split("#")[0].split("?")[0])
    if not path:
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source.readme_path), path))
    return None if resolved in (".", "") else resolved


def check_links(card: Card, source: Source) -> List[Problem]:
    if not source.complete:
        return []  # said once, by check_data_files
    out: List[Problem] = []
    seen = set()
    for target, line, is_image in card.links:
        path = _link_path(target, source)
        if path is None or path in seen or path.startswith("../"):
            continue
        seen.add(path)
        folder = path.rstrip("/") + "/"
        if path in source.files or any(f.startswith(folder) for f in source.files):
            continue
        what = "an image" if is_image else "a file"
        out.append(Problem("missing_file", ERROR, f"the card links to {what} '{path}' that is not in the dataset", source.readme_path, line))
    return out


# ---- Data files ----------------------------------------------------------------------------------------------------------

# One cell may hold a whole document (fka/awesome-chatgpt-prompts has cells past csv's default 128 KB, 2026-11-16).
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def _rows(raw: bytes, delimiter: str) -> Optional[List[List[str]]]:
    """The file's rows, or None when it is not readable as CSV."""
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except csv.Error:
        return None


def _features(meta, config_name: str) -> Optional[List[str]]:
    info = meta.get("dataset_info") if isinstance(meta, dict) else None
    infos = info if isinstance(info, list) else [info] if isinstance(info, dict) else []
    for item in infos:
        if isinstance(item, dict) and item.get("config_name", "default") == config_name and isinstance(item.get("features"), list):
            names = [f.get("name") for f in item["features"] if isinstance(f, dict) and isinstance(f.get("name"), str)]
            return names or None
    return None


def check_tables(card: Card, source: Source, profile: str = "default", most_files: int = DATA_MOST_FILES) -> List[Problem]:
    """CSV and TSV files: every row as long as its header; link columns hold links; the columns the card declares
    (dataset_info.features) are the ones the file has. At most `most_files` files and 60 MB are read, the files the card's
    configs name first; a note says how many were not."""
    out: List[Problem] = []
    tables = sorted(f for f in source.files if f.lower().endswith((".csv", ".tsv")))
    named = {f for config in configs_of(card.metadata) for p in _patterns(config.get("data_files")) for f in matching(p, tables)}
    tables = sorted(tables, key=lambda f: (f not in named, f))
    headers: Dict[str, List[str]] = {}
    read_files, read_bytes, skipped = 0, 0, 0
    for path in tables:
        size = source.files.get(path)
        if read_files >= most_files or (size is not None and (size > DATA_MOST_BYTES or read_bytes + size > DATA_MOST_TOTAL)):
            skipped += 1
            continue
        raw = source.read(path, DATA_MOST_BYTES)
        if raw is None:
            skipped += 1
            continue
        read_files += 1
        read_bytes += len(raw)
        rows = _rows(raw, "\t" if path.lower().endswith(".tsv") else ",")
        if rows is None:
            out.append(Problem("data_not_read", NOTE, "could not be read as CSV", path))
            continue
        if not rows:
            continue
        header = [h.strip() for h in rows[0]]
        headers[path] = header
        ragged: List[int] = []
        body: List[tuple] = []
        for number, row in enumerate(rows[1:], start=2):
            if not row or (len(row) == 1 and not row[0].strip()):
                continue
            if len(row) != len(header):
                ragged.append(number)
            else:
                body.append((number, row))
        if ragged:
            out.append(Problem("ragged_rows", ERROR, f"{len(ragged)} row(s) do not have the header's {len(header)} cells: line {_lines(ragged)}", path, ragged[0]))
        # The hive layout's rule (632's attribution-docs-check): every row of data.csv names an https source.
        strict = profile == "hive" and path == "data.csv"
        for i, column in enumerate(header):
            if column.lower() not in LINK_COLUMNS and not (strict and column == "source"):
                continue
            values = [(n, row[i].strip()) for n, row in body]
            filled = [(n, v) for n, v in values if v]
            links = [(n, v) for n, v in filled if _URL.match(v) and (not strict or v.startswith("https://"))]
            if not strict and (not filled or len(links) * 2 < len(filled)):
                continue  # a column of names, not links
            empty = [n for n, v in values if not v]
            odd = [n for n, v in filled if (n, v) not in links]
            if empty:
                out.append(Problem("empty_link", ERROR if strict else WARNING, f"column '{column}' is empty in {len(empty)} of {len(values)} row(s): line {_lines(empty)}", path, empty[0]))
            if odd:
                what = "an https link" if strict else "a link, as most of its rows are"
                out.append(Problem("not_a_link", ERROR, f"column '{column}' is not {what} in {len(odd)} row(s): line {_lines(odd)}", path, odd[0]))
    if profile == "hive":
        data = headers.get("data.csv")
        if "data.csv" not in source.files:
            out.append(Problem("missing_file", ERROR, "the hive layout needs a data.csv", "data.csv"))
        elif data is not None and "source" not in data:
            out.append(Problem("no_source_column", ERROR, "data.csv has no 'source' column", "data.csv", 1))
    out.extend(_declared_columns(card, source, headers))
    if skipped:
        out.append(Problem("data_not_read", NOTE, f"{skipped} of {len(tables)} CSV/TSV file(s) were not read (at most {most_files} files, 20 MB each, 60 MB in all; --data-files reads more)"))
    return out


def _declared_columns(card: Card, source: Source, headers: Dict[str, List[str]]) -> List[Problem]:
    out: List[Problem] = []
    configs = configs_of(card.metadata) or [{"config_name": "default", "data_files": None}]
    for config in configs:
        name = config.get("config_name", "default")
        declared = _features(card.metadata, name)
        if not declared:
            continue
        patterns = _patterns(config.get("data_files")) if config.get("data_files") is not None else None
        files = sorted({f for p in patterns for f in matching(p, headers)}) if patterns is not None else sorted(headers)
        for path in files[:3]:
            have = headers[path]
            missing = [c for c in declared if c not in have]
            extra = [c for c in have if c not in declared]
            if missing or extra:
                said = []
                if missing:
                    said.append("declared but not in the file: " + ", ".join(missing[:8]))
                if extra:
                    said.append("in the file but not declared: " + ", ".join(extra[:8]))
                out.append(Problem("columns_mismatch", ERROR, f"config '{name}': " + "; ".join(said), path, 1))
    return out


# ---- All of them ---------------------------------------------------------------------------------------------------------

PROFILES = ("default", "hive")


def run_checks(card: Optional[Card], source: Source, profile: str = "default", require: Sequence[str] = (), data: bool = True, most_files: int = DATA_MOST_FILES) -> List[Problem]:
    readme = source.readme_path
    if card is None:
        return [Problem("no_card", ERROR, f"the dataset has no {readme}: there is no card at all", readme)]
    problems = check_header(card, readme) + check_text(card, readme)
    required: Dict[str, Sequence[str]] = dict(HIVE_SECTIONS) if profile == "hive" else {}
    for name in require:
        required.setdefault(name, ())
    if required:
        problems += check_sections(card, readme, required)
    if profile == "hive":
        problems += check_title(card, readme)
    problems += check_data_files(card, source) + check_links(card, source)
    if data:
        problems += check_tables(card, source, profile, most_files)
    return problems
