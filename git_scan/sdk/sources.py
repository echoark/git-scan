"""What a scan reads: changed lines from git diffs, and repository names.

Every content check scans the same list of :class:`Line` items, built once
per scan. Scanning git's own diff output means one git command per source,
findings that name the file and line, and removed lines included: a secret
being deleted is still reported, because it remains in history.

Sources:

- ``staged``: ``git diff --cached`` (what the commit will contain).
- ``unstaged``: ``git diff`` (changes on disk not yet staged). Optional.
- ``untracked``: whole untracked files, read from disk. Optional.
- ``message``: a commit message being written (the ``commit-msg`` hook's
  file, or a draft), in place of the staged diff. Comment lines are dropped
  and a ``--verbose`` diff below the scissors line is ignored, as git itself
  does when it cleans up the message.
- commits: one ``git log -p`` over a range (``A..B``), a single commit, or
  an explicit list of commits, in place of the staged diff. Each commit
  gives its patch against its first parent (merges included, so a
  hand-edited resolution shows), its message, and its author and committer
  emails (``identity`` lines, which only the git identity check reads). The
  commit header (id, dates) is never scanned. Every line carries the commit
  it came from, so findings name it. One process reads the whole range, and
  each commit's own patch is scanned: a secret added in one commit and
  deleted in a later one is reported in both, which a net diff of the range
  would hide.
- names: every tracked file path, branch, and tag, for every scan except a
  message; a bad name is published with every push regardless of what the
  commit touches.

Diffs are taken with no context lines, so unchanged neighbours of a change
are never reported, and with rename detection, so a pure move produces no
content lines.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

DIFF_ARGS = ["-c", "core.quotepath=false", "diff", "-U0", "-M", "--no-color",
             "--no-ext-diff", "--no-prefix"]

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

MESSAGE_FILE = "commit message"
_SCISSORS = "------------------------ >8 ------------------------"

# One record per commit, NUL-separated: id, author email, committer email,
# message, then the patch git prints after the format. NUL cannot occur in
# a message, an email, or a text patch, so no message content can be
# mistaken for a field boundary.
LOG_ARGS = ["-c", "core.quotepath=false", "log", "-p", "--root", "--diff-merges=first-parent",
            "-U0", "-M", "--no-color", "--no-ext-diff", "--no-prefix", "--no-show-signature",
            "--format=%x00%H%x00%ae%x00%ce%x00%B%x00"]


class RevisionError(ValueError):
    """A range or commit git can't read; the message is git's."""


@dataclass(frozen=True)
class Line:
    """One unit of scanned text."""

    text: str
    file: str
    source: str            # staged | unstaged | untracked | commit | message
                           # | file | branch | tag | author | committer
    kind: str              # added | removed | name | identity
    line: Optional[int] = None
    commit: Optional[str] = None   # the commit a history line came from

    @property
    def is_name(self) -> bool:
        return self.kind == "name"

    def where(self) -> str:
        """Location label for a finding."""
        if self.kind == "name":
            label = {"file": "file name", "branch": "branch", "tag": "tag"}[self.source]
            return f"{label}: {self.text}"
        if self.kind == "identity":
            loc = f"{self.source} {self.text}"
        else:
            loc = f"{self.file}:{self.line}"
        if self.kind == "removed":
            loc += " (removed)"
        if self.commit:
            return f"[commit {self.commit[:12]}] {loc}"
        if self.source not in ("staged", "message"):
            loc = f"[{self.source}] {loc}"
        return loc


@dataclass
class ScanInput:
    """Everything one scan reads, built once and shared by all checks."""

    content: List[Line]
    names: List[Line]
    identities: List[Line] = field(default_factory=list)   # author/committer, commits scope
    scope: str = "staged"          # staged | message | commits
    remote: Optional[str] = None   # the remote being pushed to, if known

    @property
    def files(self) -> List[str]:
        seen: dict = {}
        for ln in self.content:
            if ln.source != "message":
                seen.setdefault(ln.file, None)
        return list(seen)

    @property
    def commits(self) -> List[str]:
        """The commits read, in log order (every commit has identity lines)."""
        return list(dict.fromkeys(ln.commit for ln in self.identities))

    def subset(self, commits: Iterable[str]) -> "ScanInput":
        """Only these commits' lines, and no names: for telling which commits are clean."""
        keep = set(commits)
        return ScanInput(content=[ln for ln in self.content if ln.commit in keep], names=[],
                         identities=[ln for ln in self.identities if ln.commit in keep],
                         scope=self.scope, remote=self.remote)

    def by_file(self) -> dict:
        grouped: dict = {}
        for ln in self.content:
            grouped.setdefault(ln.file, []).append(ln)
        return grouped


def _git(repo_path: str, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo_path, capture_output=True,
                         text=True, errors="replace")
    return res.stdout if res.returncode == 0 else ""


def parse_diff(diff_text: str, source: str, commit: Optional[str] = None) -> List[Line]:
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
            lines.append(Line(raw[1:], file, source, "added", new_no, commit))
            new_no += 1
        elif raw.startswith("-"):
            lines.append(Line(raw[1:], file, source, "removed", old_no, commit))
            old_no += 1
    return lines


def staged_lines(repo_path: str) -> List[Line]:
    return parse_diff(_git(repo_path, *DIFF_ARGS, "--cached"), "staged")


def unstaged_lines(repo_path: str) -> List[Line]:
    return parse_diff(_git(repo_path, *DIFF_ARGS), "unstaged")


def _comment_char(repo_path: str) -> str:
    """The comment prefix git strips from an edited message (default ``#``)."""
    for key in ("core.commentString", "core.commentChar"):
        value = _git(repo_path, "config", "--get", key).rstrip("\n")
        if value and value != "auto":
            return value
    return "#"


def message_lines(text: str, comment: str = "#") -> List[Line]:
    """A commit message being written, as git will keep it.

    Lines starting with the comment prefix are dropped, and everything from
    the scissors line on (the diff ``git commit -v`` appends) is ignored.
    """
    lines: List[Line] = []
    for i, t in enumerate(text.splitlines(), 1):
        if comment and t.startswith(comment):
            if t[len(comment):].strip() == _SCISSORS:
                break
            continue
        lines.append(Line(t, MESSAGE_FILE, "message", "added", i))
    return lines


def _log_lines(repo_path: str, revs: List[str], walk: bool) -> List[Line]:
    """Run one ``git log -p`` and turn every commit in it into lines.

    ``walk`` reads ``revs`` as a range (``A..B``, or rev-list arguments);
    otherwise ``revs`` are exactly the commits to read, passed on standard
    input so a long list never meets an argument limit.
    """
    if not walk and not revs:
        return []      # git log with no revisions would read HEAD
    args = LOG_ARGS + ([*revs, "--"] if walk else ["--no-walk=unsorted", "--stdin"])
    res = subprocess.run(["git", *args], cwd=repo_path, capture_output=True, text=True,
                         errors="replace", input=None if walk else "\n".join(revs) + "\n")
    if res.returncode != 0:
        raise RevisionError(res.stderr.strip() or f"git log {' '.join(revs)} failed")
    fields = res.stdout.split("\0")
    lines: List[Line] = []
    for i in range(1, len(fields) - 4, 5):
        sha, author, committer, body, patch = fields[i:i + 5]
        lines.append(Line(author, "author", "author", "identity", commit=sha))
        lines.append(Line(committer, "committer", "committer", "identity", commit=sha))
        lines += [Line(t, MESSAGE_FILE, "message", "added", n, sha)
                  for n, t in enumerate(body.splitlines(), 1)]
        lines += parse_diff(patch, "commit", sha)
    return lines


def range_lines(repo_path: str, rev_range: str) -> List[Line]:
    """Every commit in ``rev_range`` (``A..B``), in one pass."""
    return _log_lines(repo_path, [rev_range], walk=True)


def commits_lines(repo_path: str, commits: Iterable[str]) -> List[Line]:
    """Exactly these commits, in one pass."""
    return _log_lines(repo_path, list(commits), walk=False)


def commit_lines(repo_path: str, commit: str) -> List[Line]:
    """One commit: its patch against its first parent, message, and identities."""
    return commits_lines(repo_path, [commit])


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
            include_untracked: bool = False, commit: Optional[str] = None,
            message: Optional[str] = None, rev_range: Optional[str] = None,
            commits: Optional[List[str]] = None, remote: Optional[str] = None) -> ScanInput:
    """Build the one input a scan reads. ``message``, ``rev_range``,
    ``commit`` and ``commits`` each replace the staged diff."""
    if message is not None:
        return ScanInput(content=message_lines(message, _comment_char(repo_path)), names=[],
                         scope="message")
    if commit or rev_range or commits is not None:
        logged = (range_lines(repo_path, rev_range) if rev_range
                  else commit_lines(repo_path, commit) if commit
                  else commits_lines(repo_path, commits))
        return ScanInput(content=[ln for ln in logged if ln.kind != "identity"],
                         names=name_lines(repo_path),
                         identities=[ln for ln in logged if ln.kind == "identity"],
                         scope="commits", remote=remote)
    content = staged_lines(repo_path)
    if include_unstaged:
        content += unstaged_lines(repo_path)
    if include_untracked:
        content += untracked_lines(repo_path)
    return ScanInput(content=content, names=name_lines(repo_path))
