"""Marks on commits, and the push check that requires them.

A mark is a short label recorded on a commit. git-scan writes one mark
itself, ``scanned``, on every commit whose pre-commit scan passed. Any other
check (a manual review, an automated one) can require its own mark: the
push check is told which labels to require (``push.require_marks`` in a
config layer) and the check writes its mark with ``git-scan mark <label>``.

Storage: git notes under ``refs/notes/git-scan``, one label per line. Notes
are not pushed by default, so marks stay on the machine that wrote them.
Notes are not carried to rewritten commits either (amend, rebase,
cherry-pick produce new commits), so a rewritten commit is unmarked until
it is scanned or marked again. Callers never touch the notes directly:
:func:`add_mark` writes and :func:`push_check` reads.

Coverage, checked for every commit the remote does not have yet:

- ``scanned`` is per commit. A scan reads one commit's changes, so a mark
  on a later commit says nothing about an earlier one.
- A required mark covers the marked commit and every unpushed commit in its
  history. A review reads the whole unpushed range, so one mark on the tip
  of a batch covers the batch.

How ``scanned`` is written: the pre-commit hook runs before the commit
exists, so a passing scan of the staged changes records the staged tree in
the repository's git directory (:func:`record_passed_scan`); the post-commit
hook marks the new commit when its tree is the one that passed
(:func:`mark_if_scanned`). A commit made without the hook can be scanned
afterwards with ``git-scan run --commit <rev>``, which marks it on a pass.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

NOTES_REF = "refs/notes/git-scan"
SCANNED = "scanned"
ZERO = "0" * 40
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_PASSED_FILE = "git-scan-passed-tree"


class MarkError(ValueError):
    """A mark operation can't be done; the message says why."""


def _git(repo_path: str, *args: str, input: Optional[str] = None,
         check: bool = False) -> subprocess.CompletedProcess:
    res = subprocess.run(["git", *args], cwd=repo_path, capture_output=True,
                         text=True, input=input)
    if check and res.returncode != 0:
        raise MarkError(res.stderr.strip() or f"git {' '.join(args)} failed")
    return res


def _out(repo_path: str, *args: str) -> str:
    res = _git(repo_path, *args)
    return res.stdout.strip() if res.returncode == 0 else ""


def valid_label(label: str) -> bool:
    return bool(_LABEL.fullmatch(label))


def check_label(label: str) -> None:
    if not valid_label(label):
        raise MarkError(f"'{label}' is not a valid mark label: use letters, digits, "
                         "'.', '_' or '-', starting with a letter or digit")


def marks_on(commit: str, repo_path: str = ".") -> Set[str]:
    """The mark labels on one commit."""
    note = _out(repo_path, "notes", f"--ref={NOTES_REF}", "show", commit)
    return {ln.strip() for ln in note.splitlines() if ln.strip()}


def add_mark(label: str, rev: str = "HEAD", repo_path: str = ".") -> str:
    """Mark ``rev`` with ``label``. Returns the full commit id."""
    check_label(label)
    sha = _out(repo_path, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if not sha:
        raise MarkError(f"'{rev}' is not a commit in {Path(repo_path).resolve()}")
    labels = marks_on(sha, repo_path) | {label}
    _git(repo_path, "notes", f"--ref={NOTES_REF}", "add", "-f", "-m",
         "\n".join(sorted(labels)), sha, check=True)
    return sha


# --- scanned -------------------------------------------------------------------

def _git_dir(repo_path: str) -> Optional[Path]:
    d = _out(repo_path, "rev-parse", "--absolute-git-dir")
    return Path(d) if d else None


def record_passed_scan(repo_path: str = ".") -> None:
    """Remember the staged tree a passing pre-commit scan read."""
    tree = _out(repo_path, "write-tree")
    git_dir = _git_dir(repo_path)
    if tree and git_dir:
        (git_dir / _PASSED_FILE).write_text(tree + "\n")


def mark_if_scanned(repo_path: str = ".") -> bool:
    """Post-commit: mark HEAD ``scanned`` if its tree is the one that passed.

    The record is consumed either way, so it can't mark a later commit.
    """
    git_dir = _git_dir(repo_path)
    if not git_dir:
        return False
    record = git_dir / _PASSED_FILE
    if not record.exists():
        return False
    passed = record.read_text().strip()
    record.unlink()
    if passed and passed == _out(repo_path, "rev-parse", "HEAD^{tree}"):
        add_mark(SCANNED, "HEAD", repo_path)
        return True
    return False


# --- push check ------------------------------------------------------------------

@dataclass
class PushResult:
    """Commits being pushed that lack a mark, by commit id."""
    checked: List[str] = field(default_factory=list)
    missing: Dict[str, List[str]] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing


def _rev_list(repo_path: str, tips: Iterable[str], exclude: Iterable[str]) -> List[str]:
    tips = list(tips)
    if not tips:
        return []
    args = ["rev-list", *tips, "--not", *exclude]
    return [s for s in _out(repo_path, *args).splitlines() if s]


def _exclusions(repo_path: str, remote: Optional[str], remote_shas: Iterable[str]) -> List[str]:
    """What the remote already has: its tracking refs, and the tips git reported."""
    ex = []
    if remote and _out(repo_path, "remote").splitlines().count(remote):
        ex.append(f"--remotes={remote}")
    for sha in remote_shas:
        if sha != ZERO and _out(repo_path, "cat-file", "-t", sha) == "commit":
            ex.append(sha)
    return ex


def push_check(push_lines: Iterable[str], remote: Optional[str] = None,
               required: Iterable[str] = (), repo_path: str = ".") -> PushResult:
    """Check the commits a push would send.

    ``push_lines`` are what git gives a pre-push hook on standard input:
    ``<local ref> <local sha> <remote ref> <remote sha>`` per ref. Deletions
    send nothing and are skipped.
    """
    tips, remote_shas = [], []
    for line in push_lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        _, local_sha, _, remote_sha = parts
        if local_sha == ZERO:
            continue
        tips.append(local_sha)
        remote_shas.append(remote_sha)

    required = [n for n in dict.fromkeys(required) if n != SCANNED]
    result = PushResult(required=[SCANNED, *required])
    exclude = _exclusions(repo_path, remote, remote_shas)
    outgoing = _rev_list(repo_path, tips, exclude)
    result.checked = outgoing
    if not outgoing:
        return result

    marks = {sha: marks_on(sha, repo_path) for sha in outgoing}
    covered: Dict[str, Set[str]] = {SCANNED: {s for s in outgoing if SCANNED in marks[s]}}
    for label in required:
        marked = [s for s in outgoing if label in marks[s]]
        covered[label] = set(_rev_list(repo_path, marked, exclude))

    for sha in outgoing:
        lacking = [n for n in result.required if sha not in covered[n]]
        if lacking:
            result.missing[sha] = lacking
    return result


def current_branch_push(remote: Optional[str] = None, repo_path: str = ".") -> tuple:
    """What a push of the current branch would send, as pre-push input.

    For checking by hand: returns ``(lines, remote)`` for :func:`push_check`,
    with the branch's upstream as what the remote already has.
    """
    head = _out(repo_path, "rev-parse", "HEAD")
    branch = _out(repo_path, "symbolic-ref", "--quiet", "--short", "HEAD")
    remote = (remote or (_out(repo_path, "config", f"branch.{branch}.remote") if branch else "")
              or "origin")
    upstream = _out(repo_path, "rev-parse", "--verify", "--quiet", "@{upstream}") or ZERO
    lines = [f"HEAD {head} refs/heads/{branch or 'HEAD'} {upstream}"] if head else []
    return lines, remote


def format_push_result(result: PushResult, repo_path: str = ".") -> str:
    """The refusal message: which commits, which marks, what to run."""
    if result.ok:
        return f"git-scan: {len(result.checked)} commit(s) checked, all marked"
    lines = [f"git-scan: {len(result.missing)} commit(s) missing marks; push refused.", ""]
    for sha, labels in result.missing.items():
        subject = _out(repo_path, "log", "-1", "--format=%s", sha)
        lines.append(f"  {sha[:12]}  {subject[:60]}")
        for n in labels:
            if n == SCANNED:
                fix = f"git-scan run --commit {sha[:12]}"
            else:
                fix = f"run the '{n}' check, then: git-scan mark {n}"
            lines.append(f"      {n:<16} {fix}")
    others = [n for n in result.required if n != SCANNED]
    lines += ["",
              "'scanned' is per commit: git-scan run --commit scans one commit's changes "
              "and marks it if clean."]
    if others:
        lines.append("Other marks cover the marked commit and everything unpushed "
                     "before it, so marking the newest commit covers the batch.")
    return "\n".join(lines)
