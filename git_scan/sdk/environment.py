"""The environment layer: identity patterns derived from this machine.

The name and email git will commit with, the parts of that name, the login
name, the home directory name and the values of a ``.env`` file in the
repository are sensitive strings no catalog lists, because they differ per
machine. git-scan derives them at scan time and gives each a stable id, so
they take the same controls as any other entry (``patterns edit``,
``patterns exceptions``, ``patterns remove``, ``patterns restore``) and
show in ``patterns list`` under the layer ``environment``:

===================  ==========================================  =========
id                   value                                       default
===================  ==========================================  =========
``git-name-full``    the git author name                         finding
``git-name-first``   its first part (two-part names)             judgment
``git-name-last``    its last part (two-part names)              judgment
``git-name-part-N``  the N-th part (names of three parts or more) judgment
``git-email``        the git author email                        finding
``local-username``   the login name (``USER``)                   judgment
``home-dir``         the home directory name, when it differs    judgment
``env-<KEY>``        a value of ``.env`` in the repository root  finding
===================  ==========================================  =========

Name parts, the login name and the home directory name are judgment
entries: they are often ordinary words, so a match is listed for a look
rather than refused. The full name, the email and ``.env`` values are
findings.

The layer sits between the built-in and the user layer, so an override in
the user layer (or a project's) wins, and is stored against the id, as an
override of a built-in is; the derived value itself is never written to a
configuration file. A catalog entry whose pattern equals a derived value,
compared without regard to case, governs: the derived entry is dropped, so
the catalog is the single source of truth once it names something.

Shared system account names (``root``, ``runner``, ``node``...) identify
no person and are skipped, and a name part of two characters or fewer is
too short to mean anything.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# Account names that machines, containers, and CI runners give everyone.
# They identify no person, and most are ordinary words ("root", "node"),
# so treating them as personal would flag prose like "the repo root".
SYSTEM_ACCOUNT_NAMES = frozenset({
    "root", "admin", "administrator", "runner", "ubuntu", "ec2-user",
    "vscode", "codespace", "node", "user", "nobody",
})

LAYER = "environment"
ENV_PREFIX = "env-"
FIXED_IDS = ("git-name-full", "git-email", "local-username", "home-dir")

NAME_PART_NOTE = ("part of the git author name on this machine; a finding when it refers "
                  "to the author, not when the word is used in another sense")
LOGIN_NOTE = ("the login name on this machine; a finding when it identifies the author "
              "(a path, an account), not when the word is used in another sense")
HOME_NOTE = ("the home directory name on this machine; a finding when it identifies the "
             "author (a path, an account), not when the word is used in another sense")

_IDENT = re.compile(r"^(?P<name>.*?)\s*<(?P<email>[^>]*)>")
_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def is_derived_id(id: Optional[str]) -> bool:
    """Whether ``id`` names an environment entry (present on this machine or not)."""
    return bool(id) and (id in FIXED_IDS or id.startswith(("git-name-", ENV_PREFIX)))


def _personal_account_name(name: Optional[str]) -> Optional[str]:
    """The account name, or None when it is a shared system account."""
    if name and name.lower() not in SYSTEM_ACCOUNT_NAMES:
        return name
    return None


def commit_identity(repo_path: str = ".") -> Tuple[str, str]:
    """(name, email) git will author the next commit with.

    ``git var GIT_AUTHOR_IDENT`` applies git's own precedence: the
    ``GIT_AUTHOR_NAME`` / ``GIT_AUTHOR_EMAIL`` environment variables over
    repository config over global config. Reading ``user.name`` alone would
    take the global config's name even when the environment overrides it,
    which is how a hosted environment's placeholder identity ends up treated
    as the user's name. Falls back to config when ``git var`` fails (no
    identity configured at all).
    """
    try:
        result = subprocess.run(
            ["git", "var", "GIT_AUTHOR_IDENT"],
            capture_output=True, text=True, timeout=5, cwd=repo_path,
        )
        m = _IDENT.match(result.stdout.strip()) if result.returncode == 0 else None
        if m:
            return m.group("name").strip(), m.group("email").strip()
    except Exception:
        pass

    def _config(key):
        try:
            r = subprocess.run(["git", "config", key], capture_output=True,
                               text=True, timeout=5, cwd=repo_path)
            return r.stdout.strip()
        except Exception:
            return ""
    return _config("user.name"), _config("user.email")


def _entry(id: str, value: str, category: str, judgment: bool, note: Optional[str] = None) -> dict:
    entry = {"id": id, "pattern": re.escape(value), "category": category}
    if judgment:
        entry["judgment"] = True
    if note:
        entry["note"] = note
    entry["_value"] = value          # for the precedence check; dropped before use
    return entry


def _env_values(repo_path: str) -> List[Tuple[str, str]]:
    env_path = Path(repo_path) / ".env"
    if not env_path.is_file():
        return []
    found = []
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    value = value.strip().strip('"').strip("'")
                    if value and len(value) > 8:
                        found.append((key.strip(), value))
    except Exception:
        pass
    return found


def derived_entries(repo_path: str = ".", governed_by: Iterable[str] = ()) -> List[dict]:
    """The environment layer's pattern entries for this machine and repository.

    ``governed_by`` are the pattern texts of the catalog (user and project
    layers); a derived value one of them equals, compared without regard to
    case and with or without regex escaping, is left out.
    """
    entries: List[dict] = []

    username = _personal_account_name(os.environ.get("USER") or os.environ.get("USERNAME"))
    if username:
        entries.append(_entry("local-username", username, "username", True, LOGIN_NOTE))

    try:
        home_name = _personal_account_name(Path.home().name)
        if home_name and home_name != username:
            entries.append(_entry("home-dir", home_name, "username", True, HOME_NOTE))
    except Exception:
        pass

    git_name, git_email = commit_identity(repo_path)
    if git_name:
        entries.append(_entry("git-name-full", git_name, "name", False))
        parts = git_name.split()
        if len(parts) > 1:
            for i, part in enumerate(parts):
                if len(part) <= 2:
                    continue
                if len(parts) == 2:
                    id = "git-name-first" if i == 0 else "git-name-last"
                else:
                    id = f"git-name-part-{i + 1}"
                entries.append(_entry(id, part, "name", True, NAME_PART_NOTE))
    if git_email:
        entries.append(_entry("git-email", git_email, "email", False))

    for key, value in _env_values(repo_path):
        entries.append(_entry(ENV_PREFIX + (_ID_CHARS.sub("_", key) or "value"), value, "env", False))

    catalog = {t.lower() for t in governed_by if isinstance(t, str)}
    kept = []
    seen = set()
    for e in entries:
        value = e.pop("_value")
        if e["id"] in seen:
            continue
        if value.lower() in catalog or re.escape(value).lower() in catalog:
            continue
        seen.add(e["id"])
        kept.append(e)
    return kept
