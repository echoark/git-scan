"""Marks on commits, the push check that reads them, and the records the
pre-commit hooks leave for the post-commit hook.

A mark is a short label git-scan records on a commit, in repository-local
metadata it manages itself. Two labels exist and nothing outside this
module writes either:

- ``scanned``: the commit's own changes, message, and identities passed the
  scan. Written by the post-commit hook for a commit made through the
  hooks, and by a scan of existing commits for each clean one.
- ``reviewed``: a privacy review covering this commit and every unpushed
  commit before it was recorded (``review.record``, from the reviewer's own
  result). The push check requires it when the config says so.

Marks are not pushed and do not follow a rewritten commit (amend, rebase,
cherry-pick produce new commits), so a rewritten commit is unmarked until it
is scanned or reviewed again.

The push check, for every commit the remote does not have yet:

- ``scanned`` is a cache, not a requirement. The push gate scans every
  outgoing commit that lacks it (``scanner.push_scan``, one pass over all
  of them) and skips the ones that have it, so a rebase or a history
  rewrite costs one scan at push time rather than a refusal.
- ``reviewed`` on a commit covers it and every unpushed commit in its
  history. A review reads the whole unpushed range, so one record on the
  tip of a batch covers the batch.

Two records bridge the pre-commit hooks and the post-commit hook, both keyed
to the staged tree so they apply to exactly one change: a passing staged
scan records the tree (:func:`record_passed_scan`), and ``git-scan ack``
records the tree whose judgment matches were acknowledged
(:func:`record_ack`). A refused commit message drops the passed-scan record
(:func:`forget_passed_scan`); the post-commit hook marks the new commit when
its tree is the one that passed and consumes both records
(:func:`mark_if_scanned`).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

NOTES_REF = "refs/notes/git-scan"
SCANNED = "scanned"
REVIEWED = "reviewed"
ZERO = "0" * 40
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_PASSED_FILE = "git-scan-passed-tree"
_ACK_FILE = "git-scan-ack-tree"


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


def marks_of(commits: Iterable[str], repo_path: str = ".") -> Dict[str, Set[str]]:
    """The mark labels on each commit (full ids), read in one git call."""
    commits = list(dict.fromkeys(commits))
    found: Dict[str, Set[str]] = {sha: set() for sha in commits}
    if not commits:
        return found
    res = _git(repo_path, "log", "--no-walk=unsorted", "--stdin", f"--notes={NOTES_REF}",
               "--format=%H%x00%N%x00", input="\n".join(commits) + "\n")
    fields = res.stdout.split("\0") if res.returncode == 0 else []
    for i in range(0, len(fields) - 1, 2):
        sha = fields[i].strip()
        if sha in found:
            found[sha] = {ln.strip() for ln in fields[i + 1].splitlines() if ln.strip()}
    return found


def add_marks(label: str, commits: Iterable[str], repo_path: str = ".") -> None:
    """Mark many commits (full ids) with ``label`` in one notes commit."""
    check_label(label)
    commits = list(dict.fromkeys(commits))
    current = marks_of(commits, repo_path)
    todo = [sha for sha in commits if label not in current[sha]]
    if not todo:
        return
    if len(todo) == 1:
        add_mark(label, todo[0], repo_path)
        return
    ident = _out(repo_path, "var", "GIT_COMMITTER_IDENT")
    parent = _out(repo_path, "rev-parse", "--verify", "--quiet", NOTES_REF)
    cmds = [f"commit {NOTES_REF}", f"committer {ident}", "data <<EOM",
            f"git-scan {label}", "EOM"]
    if parent:
        cmds.append(f"from {parent}")
    for sha in todo:
        note = "\n".join(sorted(current[sha] | {label})) + "\n"
        cmds += [f"N inline {sha}", f"data {len(note)}", note]   # labels are ASCII
    _git(repo_path, "fast-import", "--quiet", "--done", input="\n".join(cmds) + "\ndone\n",
         check=True)


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


def forget_passed_scan(repo_path: str = ".") -> None:
    """Drop the record of a passing staged scan (the commit message was
    refused), so a retry with ``--no-verify`` can't inherit the mark."""
    git_dir = _git_dir(repo_path)
    if git_dir:
        (git_dir / _PASSED_FILE).unlink(missing_ok=True)


def _staged_tree(repo_path: str) -> str:
    return _out(repo_path, "write-tree")


def record_ack(repo_path: str = ".") -> str:
    """Remember that the judgment matches of the current staged tree were
    acknowledged. Returns the tree id."""
    tree = _staged_tree(repo_path)
    git_dir = _git_dir(repo_path)
    if tree and git_dir:
        (git_dir / _ACK_FILE).write_text(tree + "\n")
    return tree


def ack_applies(repo_path: str = ".") -> bool:
    """Whether an acknowledgement was recorded for exactly the current staged tree."""
    git_dir = _git_dir(repo_path)
    if not git_dir:
        return False
    record = git_dir / _ACK_FILE
    if not record.exists():
        return False
    acked = record.read_text().strip()
    return bool(acked) and acked == _staged_tree(repo_path)


def forget_ack(repo_path: str = ".") -> None:
    git_dir = _git_dir(repo_path)
    if git_dir:
        (git_dir / _ACK_FILE).unlink(missing_ok=True)


def mark_if_scanned(repo_path: str = ".") -> bool:
    """Post-commit: mark HEAD ``scanned`` if its tree is the one that passed.

    Both staged records (passed scan, acknowledgement) are consumed either
    way, so neither can apply to a later commit.
    """
    git_dir = _git_dir(repo_path)
    if not git_dir:
        return False
    forget_ack(repo_path)
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
    """What a push would send, and what stands in its way.

    ``cached`` are outgoing commits already marked ``scanned``; ``scan`` is
    the scan of the rest (``scanner.push_scan`` fills it); ``unreviewed``
    are the outgoing commits no recorded review covers, when one is required.
    """
    checked: List[str] = field(default_factory=list)
    cached: List[str] = field(default_factory=list)
    unreviewed: List[str] = field(default_factory=list)
    review_required: bool = False
    scan: Optional[Any] = None

    @property
    def unscanned(self) -> List[str]:
        return [s for s in self.checked if s not in set(self.cached)]

    @property
    def ok(self) -> bool:
        return not self.unreviewed and not (self.scan is not None and self.scan.failed)


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


def outgoing_commits(push_lines: Iterable[str], remote: Optional[str] = None,
                     repo_path: str = ".") -> List[str]:
    """The commits a push would send (full ids, newest first).

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
    return _rev_list(repo_path, tips, _exclusions(repo_path, remote, remote_shas))


def push_check(push_lines: Iterable[str], remote: Optional[str] = None,
               require_review: bool = False, repo_path: str = ".") -> PushResult:
    """The commits a push would send: which are already scanned, and which no
    recorded review covers when one is required. (Scanning the rest is
    ``scanner.push_scan``.)
    """
    result = PushResult(review_required=require_review)
    outgoing = outgoing_commits(push_lines, remote, repo_path)
    result.checked = outgoing
    if not outgoing:
        return result

    marks = marks_of(outgoing, repo_path)
    result.cached = [s for s in outgoing if SCANNED in marks[s]]
    if require_review:
        reviewed = [s for s in outgoing if REVIEWED in marks[s]]
        covered = set(_rev_list(repo_path, reviewed, [])) if reviewed else set()
        result.unreviewed = [s for s in outgoing if s not in covered]
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
    """What the push check found, as the pre-push hook prints it.

    The findings of a failed scan are printed by the caller beside this; this
    says what was checked and, for a refusal, what to do.
    """
    scanned_now = len(result.scan.commits) if result.scan is not None else 0
    summary = (f"{len(result.checked)} commit(s) to push: {scanned_now} scanned now, "
               f"{len(result.cached)} already scanned")
    if result.ok:
        tail = "; review recorded" if result.review_required and result.checked else ""
        return f"git-scan: {summary}{tail}"
    lines = [f"git-scan: push refused ({summary})."]
    if result.scan is not None and result.scan.failed:
        lines += ["",
                  "The scan above found issues in commits being pushed. Fix the content in",
                  "those commits (git rebase -i, git commit --amend) and push again; a",
                  "rewritten commit is scanned again on the next push. A 'Judgment' match",
                  "is cleared, after looking at it, with: git-scan ack --commit <sha>"]
    if result.unreviewed:
        lines += ["", f"{len(result.unreviewed)} commit(s) with no privacy review recorded:"]
        for sha in result.unreviewed:
            subject = _out(repo_path, "log", "-1", "--format=%s", sha)
            lines.append(f"  {sha[:12]}  {subject[:60]}")
        lines += ["",
                  "Run the privacy review over the unpushed commits, then record its result:",
                  "  git-scan review record RESULT.json",
                  "A review recorded on the newest commit covers every unpushed commit before it."]
    return "\n".join(lines)
