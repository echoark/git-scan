"""Gates an agent cannot loosen unilaterally.

Every command that loosens a gate (acknowledging judgment matches, making a
clone eligible for relaxed reviews, editing or removing patterns and
exceptions, allowing an email, turning a requirement off or changing the
reviewer, removing or replacing hooks) goes through three things here:

- a one-line **notice** that it loosens a gate;
- a **confirmation**: the person at the terminal says yes, or the caller
  passes ``--yes`` and so takes responsibility non-interactively (an agent
  in a harness whose permission rules make the human approve the command;
  see ``docs/agent-permissions.md``). Without either the command is refused;
- an **audit record**: who, when, what, which repository, how it was
  confirmed, appended to ``audit.log`` beside the user configuration and
  listed by ``git-scan audit``.

A **lock** detects a configuration changed behind the commands' back:
``git-scan lock`` records the hash of the user layer in ``git-scan.lock``
beside it, and a scan or review whose catalog hash differs fails closed
until ``git-scan lock`` is run again, which only works at a terminal. A
command that writes the user layer after an interactive confirmation
updates the lock; one run with ``--yes`` leaves it stale, so a human sees
the change before the next scan passes.

None of this stops an agent that owns the shell from editing the file or
the lock by hand. It makes every loosening explicit, confirmed and logged,
and leaves the real human gate to the harness and to a server-side scan.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .config import user_config_path

NOTICE = "git-scan: this loosens a gate"
AUDIT_FILE = "audit.log"
LOCK_FILE = "git-scan.lock"

# Tests only: stands in for the person at the terminal (True says yes, False
# says no). Reachable in-process only; the CLI has no switch for it.
confirm_override: Optional[bool] = None


class GateRefused(ValueError):
    """A gate-loosening command was not confirmed, or a lock check failed;
    the message says what to do."""


def interactive() -> bool:
    """Whether a person can answer: standard input is a terminal (or a test
    stands in for one)."""
    if confirm_override is not None:
        return True
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def confirm(what: str, yes: bool = False) -> str:
    """Print the notice and get the confirmation for ``what``. Returns how it
    was confirmed, ``--yes`` or ``interactive``; raises GateRefused otherwise."""
    print(f"{NOTICE}: {what}", file=sys.stderr)
    if yes:
        return "--yes"
    if confirm_override is not None:
        if confirm_override:
            return "interactive"
        raise GateRefused("not confirmed; nothing changed")
    if not interactive():
        raise GateRefused(f"refused: {what}. That loosens a gate and nothing confirmed it. "
                          "Run the command at a terminal and answer the question, or pass "
                          "--yes to take responsibility for it; either way it is recorded "
                          "(git-scan audit)")
    try:
        answer = input("Continue? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() in ("y", "yes"):
        return "interactive"
    raise GateRefused("not confirmed; nothing changed")


# --- audit -------------------------------------------------------------------------

def audit_path() -> Path:
    return user_config_path().parent / AUDIT_FILE


def repo_identity(repo_path: str = ".") -> str:
    """The origin URL of the repository, else its absolute path, else ``-``."""
    try:
        res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo_path,
                             capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        res = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo_path,
                             capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "-"


def _who() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or os.environ.get("USERNAME") or "-"


def record(action: str, detail: str, repo_path: str = ".", how: str = "") -> dict:
    """Append one audit record and return it."""
    entry = {
        "time": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "who": _who(),
        "action": action,
        "detail": detail,
        "repo": repo_identity(repo_path),
        "confirmed": how or "-",
    }
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def audit_entries(limit: Optional[int] = None) -> List[dict]:
    """The audit records, oldest first (the last ``limit`` of them)."""
    path = audit_path()
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append({"time": "-", "who": "-", "action": "unreadable", "detail": line,
                            "repo": "-", "confirmed": "-"})
    return entries[-limit:] if limit else entries


def format_entry(e: dict) -> str:
    return (f"{e.get('time', '-')}  {e.get('who', '-')}  {e.get('action', '-')}  "
            f"{e.get('detail', '')}  [{e.get('repo', '-')}; confirmed: {e.get('confirmed', '-')}]")


# --- lock --------------------------------------------------------------------------

def lock_path() -> Path:
    return user_config_path().parent / LOCK_FILE


def catalog_hash() -> Optional[str]:
    """The hash of the user layer's file content, or None when it is missing."""
    path = user_config_path()
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def locked_hash() -> Optional[str]:
    try:
        return lock_path().read_text().strip() or None
    except OSError:
        return None


def lock_status() -> str:
    """``unlocked`` (no lock), ``locked`` (the catalog is the locked one), or
    ``stale`` (the catalog changed, or is missing, since it was locked)."""
    locked = locked_hash()
    if locked is None:
        return "unlocked"
    return "locked" if catalog_hash() == locked else "stale"


def check_lock() -> Optional[str]:
    """None when a scan may run; otherwise the text of the refusal."""
    if lock_status() != "stale":
        return None
    return (f"the user configuration ({user_config_path()}) changed since it was locked "
            f"({lock_path()}). Nothing runs until a person has looked at the change and "
            "locked it again at a terminal: git-scan lock. (git-scan audit lists what "
            "git-scan's own commands changed; anything else was edited by hand.)")


def lock(repo_path: str = ".") -> str:
    """Record the catalog's hash. Only at a terminal: ``--yes`` is not an
    option here, since the lock exists to make a human look."""
    if not interactive():
        raise GateRefused("git-scan lock works only at a terminal, where a person has looked "
                          "at the configuration; there is no --yes for it")
    current = catalog_hash()
    if current is None:
        raise GateRefused(f"no user configuration to lock at {user_config_path()}")
    was = lock_status()
    lock_path().parent.mkdir(parents=True, exist_ok=True)
    lock_path().write_text(current + "\n")
    record("lock", f"catalog locked ({was} before)", repo_path, "interactive")
    return f"locked {user_config_path()} ({current[:12]})"


def unlock(repo_path: str = ".") -> str:
    if not interactive():
        raise GateRefused("git-scan lock --remove works only at a terminal")
    if locked_hash() is None:
        return "no lock to remove"
    lock_path().unlink()
    record("unlock", "catalog lock removed", repo_path, "interactive")
    return f"removed {lock_path()}"


def touch_lock(how: Optional[str] = None) -> Optional[str]:
    """After git-scan itself wrote the user layer: update the lock when a
    person confirmed the change (``how`` is ``interactive``, or the session
    is one), else leave it stale and say so. None when there is no lock."""
    if locked_hash() is None:
        return None
    if how == "interactive" or (how is None and interactive()):
        current = catalog_hash()
        if current:
            lock_path().write_text(current + "\n")
        return "lock updated"
    return ("the catalog lock is now stale: this change was made without a person at the "
            "terminal, so scans and reviews fail until someone runs git-scan lock")
