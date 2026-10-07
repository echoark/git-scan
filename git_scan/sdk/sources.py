"""What a scan reads: changed lines from git diffs, and repository names.

Every content check scans the same list of :class:`Line` items, built once
per scan. Scanning git's own diff output means one git command per source,
findings that name the file and line, and removed lines included: a secret
being deleted is still reported, because it remains in history.

Sources:

- ``staged``: ``git diff --cached`` (what the commit will contain).
- ``unstaged``: ``git diff`` (changes on disk not yet staged). Optional.
- ``untracked``: whole untracked files, read from disk. Optional.
- names: every tracked file path, branch, and tag. Always; a bad name is
  published with every push regardless of what the commit touches.

Diffs are taken with no context lines, so unchanged neighbours of a change
are never reported, and with rename detection, so a pure move produces no
content lines.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

DIFF_ARGS = ["-c", "core.quotepath=false", "diff", "-U0", "-M", "--no-color",
             "--no-ext-diff", "--no-prefix"]

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class Line:
    """One unit of scanned text."""

    text: str
    file: str
    source: str            # staged | unstaged | untracked | file | branch | tag
    kind: str              # added | removed | name
    line: Optional[int] = None

    @property
    def is_name(self) -> bool:
        return self.kind == "name"

    def where(self) -> str:
        """Location label for a finding."""
        if self.kind == "name":
            label = {"file": "file name", "branch": "branch", "tag": "tag"}[self.source]
            return f"{label}: {self.text}"
        loc = f"{self.file}:{self.line}"
        if self.kind == "removed":
            loc += " (removed)"
        if self.source != "staged":
            loc = f"[{self.source}] {loc}"
        return loc


@dataclass
class ScanInput:
    """Everything one scan reads, built once and shared by all checks."""

    content: List[Line]
    names: List[Line]

    @property
    def files(self) -> List[str]:
        seen: dict = {}
        for ln in self.content:
            seen.setdefault(ln.file, None)
        return list(seen)

    def by_file(self) -> dict:
        grouped: dict = {}
        for ln in self.content:
            grouped.setdefault(ln.file, []).append(ln)
        return grouped


def _git(repo_path: str, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo_path, capture_output=True,
                         text=True, errors="replace")
    return res.stdout if res.returncode == 0 else ""


def parse_diff(diff_text: str, source: str) -> List[Line]:
    """Turn unified diff output (``-U0``, ``--no-prefix``) into lines."""
    lines: List[Line] = []
    file: Optional[str] = None
    old_no = new_no = 0
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            file = None
            continue
        if raw.startswith(("--- ", "+++ ")):
            # "---" names the old file, "+++" the new one; either may be
            # /dev/null (new or deleted file). Keep whichever is real.
            name = raw[4:]
            if name != "/dev/null":
                file = name
            continue
        if raw.startswith("rename to "):
            file = raw[len("rename to "):]
            continue
        m = _HUNK.match(raw)
        if m:
            old_no, new_no = int(m.group(1)), int(m.group(3))
            continue
        if file is None or raw.startswith("\\ "):
            continue
        if raw.startswith("+"):
            lines.append(Line(raw[1:], file, source, "added", new_no))
            new_no += 1
        elif raw.startswith("-"):
            lines.append(Line(raw[1:], file, source, "removed", old_no))
            old_no += 1
    return lines


def staged_lines(repo_path: str) -> List[Line]:
    return parse_diff(_git(repo_path, *DIFF_ARGS, "--cached"), "staged")


def unstaged_lines(repo_path: str) -> List[Line]:
    return parse_diff(_git(repo_path, *DIFF_ARGS), "unstaged")


def untracked_lines(repo_path: str) -> List[Line]:
    out = _git(repo_path, "-c", "core.quotepath=false", "ls-files", "--others",
               "--exclude-standard")
    lines: List[Line] = []
    for name in out.splitlines():
        path = Path(repo_path) / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, t in enumerate(text.splitlines(), 1):
            lines.append(Line(t, name, "untracked", "added", i))
    return lines


def name_lines(repo_path: str) -> List[Line]:
    def names(cmd: Iterable[str], source: str) -> List[Line]:
        return [Line(n, n, source, "name") for n in _git(repo_path, *cmd).splitlines() if n]
    return (
        names(["-c", "core.quotepath=false", "ls-files"], "file")
        + names(["for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes"], "branch")
        + names(["for-each-ref", "--format=%(refname:short)", "refs/tags"], "tag")
    )


def collect(repo_path: str, include_unstaged: bool = False,
            include_untracked: bool = False) -> ScanInput:
    content = staged_lines(repo_path)
    if include_unstaged:
        content += unstaged_lines(repo_path)
    if include_untracked:
        content += untracked_lines(repo_path)
    return ScanInput(content=content, names=name_lines(repo_path))
