"""Reading a dataset card: its YAML header, its headings and sections, and the links in its text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

import yaml

PLACEHOLDER = "[More Information Needed]"

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LINK = re.compile(r"(!?)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


@dataclass
class Heading:
    level: int
    text: str
    line: int  # 1-based line number in the whole file


@dataclass
class Section:
    heading: Heading
    body: str  # the text under it, up to the next heading of any level
    has_subsections: bool


@dataclass
class Card:
    text: str
    has_frontmatter: bool = False
    frontmatter_closed: bool = False
    frontmatter_text: str = ""
    metadata: Any = None
    yaml_error: Optional[str] = None
    body_start_line: int = 1
    headings: List[Heading] = field(default_factory=list)
    sections: List[Section] = field(default_factory=list)
    links: List[tuple] = field(default_factory=list)  # (target, line, is_image)


def parse_card(text: str) -> Card:
    """Parse a dataset card's text. Never raises: problems are recorded on the card."""
    text = text.replace("\r\n", "\n").lstrip("﻿")
    card = Card(text=text)
    lines = text.split("\n")
    body_from = 0
    if lines and lines[0].strip() == "---":
        card.has_frontmatter = True
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                card.frontmatter_closed = True
                card.frontmatter_text = "\n".join(lines[1:i])
                body_from = i + 1
                break
        if card.frontmatter_closed:
            try:
                card.metadata = yaml.safe_load(card.frontmatter_text) if card.frontmatter_text.strip() else {}
            except yaml.YAMLError as error:
                card.yaml_error = str(error).splitlines()[0][:200]
        else:
            body_from = len(lines)
    card.body_start_line = body_from + 1

    in_fence = False
    for index in range(body_from, len(lines)):
        line = lines[index]
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match:
            card.headings.append(Heading(len(match.group(1)), match.group(2).strip(), index + 1))
            continue
        for link in _LINK.finditer(_COMMENT.sub("", line)):
            card.links.append((link.group(3), index + 1, link.group(1) == "!"))

    for n, heading in enumerate(card.headings):
        end = card.headings[n + 1].line - 1 if n + 1 < len(card.headings) else len(lines)
        body = "\n".join(lines[heading.line:end])
        nxt = card.headings[n + 1] if n + 1 < len(card.headings) else None
        card.sections.append(Section(heading, body, has_subsections=bool(nxt and nxt.level > heading.level)))
    return card


def section_is_empty(section: Section) -> bool:
    """No words under the heading (comments and whitespace do not count), and no subsections."""
    if section.has_subsections:
        return False
    return not _COMMENT.sub("", section.body).strip()
