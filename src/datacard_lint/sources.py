"""Where a card and its dataset's files come from: a local folder, or a dataset on the Hugging Face Hub."""

from __future__ import annotations

import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, Optional, Tuple

HUB = "https://huggingface.co"
_HUB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$|^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SKIP_DIRS = {".git", ".hg", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}


class SourceError(Exception):
    """The card or its files could not be read."""


class Source:
    """A dataset's card text, the paths of its files, and their sizes."""

    name: str
    readme_path: str = "README.md"
    files: Dict[str, Optional[int]]  # path relative to the dataset root -> size in bytes, when known
    # False when the file list stopped short (a Hub dataset past LIST_MOST_PAGES pages): a file not in it may still exist.
    complete: bool = True

    def readme(self) -> Optional[str]:
        raise NotImplementedError

    def read(self, path: str, most: int) -> Optional[bytes]:
        """A file's bytes, or None when it is larger than `most` or cannot be read."""
        raise NotImplementedError


class LocalSource(Source):
    def __init__(self, path: str):
        target = pathlib.Path(path)
        if target.is_file():
            self.root = target.parent
            self.readme_path = target.name
        elif target.is_dir():
            self.root = target
            self.readme_path = "README.md"
        else:
            raise SourceError(f"{path} is not a file or a folder")
        self.name = str(target)
        self.files = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for filename in filenames:
                full = pathlib.Path(dirpath) / filename
                rel = full.relative_to(self.root).as_posix()
                try:
                    self.files[rel] = full.stat().st_size
                except OSError:
                    self.files[rel] = None

    def readme(self) -> Optional[str]:
        """The card's text, or None when there is no card."""
        path = self.root / self.readme_path
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")

    def read(self, path: str, most: int) -> Optional[bytes]:
        full = self.root / path
        try:
            if full.stat().st_size > most:
                return None
            return full.read_bytes()
        except OSError:
            return None


# An opener answers a URL with its body and its response headers (lower-case names).
Opener = Callable[[str, Dict[str, str]], Tuple[bytes, Dict[str, str]]]
_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?')


def _urlopen(url: str, headers: Dict[str, str]) -> Tuple[bytes, Dict[str, str]]:
    request = urllib.request.Request(url, headers={"user-agent": "datacard-lint", **headers})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read(), {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as error:
        raise SourceError(f"{url} answered {error.code}") from None
    except urllib.error.URLError as error:
        raise SourceError(f"{url} could not be reached: {error.reason}") from None


# Pages of 1,000 files read from the Hub, at most. allenai/c4 has more (2026-11-16): past this, nothing is called missing.
LIST_MOST_PAGES = 50


class HubSource(Source):
    """A dataset repository on the Hugging Face Hub, read over its public API. HF_TOKEN is used when set
    (for private or gated datasets); nothing is ever written."""

    def __init__(self, repo_id: str, revision: str = "main", opener: Optional[Opener] = None, token: Optional[str] = None):
        repo_id = repo_id.strip().strip("/")
        if repo_id.startswith("hf://datasets/"):
            repo_id = repo_id[len("hf://datasets/"):]
        elif repo_id.startswith(f"{HUB}/datasets/"):
            repo_id = repo_id[len(f"{HUB}/datasets/"):].split("/tree/")[0].split("/blob/")[0]
        if not _HUB_ID.match(repo_id):
            raise SourceError(f"{repo_id!r} is not a Hugging Face dataset id")
        self.repo_id = repo_id
        self.revision = revision
        self.name = f"hf://datasets/{repo_id}"
        self._open = opener or _urlopen
        token = token if token is not None else os.environ.get("HF_TOKEN", "")
        self._headers = {"authorization": f"Bearer {token}"} if token else {}
        self.files = self._list_files()

    def _list_files(self) -> Dict[str, Optional[int]]:
        files: Dict[str, Optional[int]] = {}
        quoted = urllib.parse.quote(self.revision, safe="")
        url: Optional[str] = f"{HUB}/api/datasets/{self.repo_id}/tree/{quoted}?recursive=true&expand=false"
        pages = 0
        while url and pages < LIST_MOST_PAGES:
            pages += 1
            raw, headers = self._open(url, self._headers)
            try:
                entries = json.loads(raw)
            except ValueError:
                raise SourceError(f"{url} did not answer with a file list") from None
            if not isinstance(entries, list):
                raise SourceError(f"{url} did not answer with a file list")
            for entry in entries:
                if isinstance(entry, dict) and entry.get("type") == "file" and isinstance(entry.get("path"), str):
                    size = entry.get("size")
                    files[entry["path"]] = size if isinstance(size, int) else None
            # The Hub pages a long listing: the next page is in the Link header.
            nxt = _NEXT.search(headers.get("link", ""))
            url = nxt.group(1) if nxt and nxt.group(1).startswith(HUB) else None
        self.complete = url is None
        return files

    def _resolve(self, path: str) -> str:
        return f"{HUB}/datasets/{self.repo_id}/resolve/{urllib.parse.quote(self.revision, safe='')}/{urllib.parse.quote(path)}"

    def readme(self) -> Optional[str]:
        if "README.md" not in self.files:
            return None
        return self._open(self._resolve("README.md"), self._headers)[0].decode("utf-8", errors="replace")

    def read(self, path: str, most: int) -> Optional[bytes]:
        size = self.files.get(path)
        if size is None or size > most:
            return None
        try:
            return self._open(self._resolve(path), self._headers)[0]
        except SourceError:
            return None


def open_source(target: str, revision: str = "main") -> Source:
    """A local path when one exists there; otherwise a Hub dataset id (`owner/name`, `hf://datasets/...` or its URL)."""
    if os.path.exists(target):
        return LocalSource(target)
    return HubSource(target, revision=revision)
