"""Git identity check: the commit email must be allowed for the remote.

Rules live in the config (``identity:`` list, managed by ``git-scan
identity``). Each rule names a remote prefix and the emails allowed to commit
there:

    identity:
      - remote: github.com/octo-dev          # host, optional owner, optional repo
        emails: ["*+octo-dev@users.noreply.github.com"]
      - remote: git.example-corp.example
        emails: ["*@example-corp.example"]

Matching, in order:

1. A repo-level ``user.email`` is trusted: the commit must use it.
2. The remote is normalized to ``host/owner/repo`` and compared to each
   rule's ``remote`` segment by segment, so ``github.com/echo`` matches
   ``github.com/echo/app`` but never ``github.com/echoes/app``. Every
   matching rule contributes its emails; the commit email must match one
   (``*`` in a rule email matches any characters).
3. No rule matches: the check is skipped and the output says how to add one.
   Identity is enforced only where the user has said what is allowed.

A repo with no remote, or a filesystem-path remote, passes: nothing leaves
the machine. No network calls.
"""

import fnmatch
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from ..utils import CheckResult

NAME = "Git identity"
SET_REPO_EMAIL = "git config user.email <address for this repo>"


def _git(path: Path, *args: str) -> Optional[str]:
    res = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else None


def is_local_remote(url: str) -> bool:
    """A filesystem path (or file:// URL): nothing leaves the machine."""
    return url.startswith(("/", "./", "../", "~", "file://")) or bool(re.match(r"^[A-Za-z]:[\\/]", url))


def normalize_remote(url: str) -> Optional[str]:
    """``host/owner/repo`` for any remote URL form; ``None`` if unreadable.

    Handles ``https://host/o/r.git``, ``ssh://git@host:port/o/r``, and the
    scp form ``git@host:o/r.git``. Host is lowercased; the path keeps its
    case; a trailing ``.git`` is dropped.
    """
    if is_local_remote(url):
        return None
    m = re.match(r"^[a-z][a-z0-9+.-]*://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.*)$", url, re.I)
    if not m:
        m = re.match(r"^(?:[^@/]+@)?([^/:]+):(.*)$", url)
    if not m:
        return None
    host, path = m.group(1).lower(), m.group(2).strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host}/{path}" if path else host


def remote_matches(rule_remote: str, remote: str) -> bool:
    """Segment-wise prefix: the rule must be followed by ``/`` or the end."""
    rule = [s for s in rule_remote.strip("/").split("/") if s]
    parts = remote.split("/")
    if not rule or len(rule) > len(parts):
        return False
    if rule[0].lower() != parts[0].lower():
        return False
    return rule[1:] == parts[1:len(rule)]


def email_matches(rule_email: str, email: str) -> bool:
    """Exact, or with ``*`` standing for any characters."""
    return fnmatch.fnmatchcase(email.lower(), rule_email.lower())


def allowed_emails(rules: List[dict], remote: str) -> tuple:
    """(emails allowed for this remote, the rules that matched)."""
    matched = [r for r in rules if remote_matches(str(r.get("remote", "")), remote)]
    emails = [e for r in matched for e in (r.get("emails") or [])]
    return emails, matched


def check_git_identity(repo_path: str, rules: Optional[List[dict]] = None) -> CheckResult:
    path = Path(repo_path)
    rules = rules or []
    try:
        ident = _git(path, "var", "GIT_AUTHOR_IDENT")
        m = re.search(r"<(.*)>", ident or "")
        if not m:
            return CheckResult(NAME, True, [], skipped=True,
                               info="Not a git repo or can't determine identity")
        email = m.group(1).strip()

        repo_email = _git(path, "config", "--local", "--get", "user.email")
        if repo_email:
            if email != repo_email:
                return CheckResult(NAME, False, [
                    f"Commit email '{email}' differs from the repo's user.email '{repo_email}'",
                    "Something in the environment (e.g. GIT_AUTHOR_EMAIL) overrides it",
                ], info=f"Conflict: {email} vs {repo_email}")
            return CheckResult(NAME, True, info=f"Repo setting: {email}")

        url = _remote_url(path)
        if not url or is_local_remote(url):
            return CheckResult(NAME, True, info=f"No network remote: {email}")
        remote = normalize_remote(url)
        if not remote:
            return CheckResult(NAME, False, [
                f"Can't read the remote '{url}'",
                f"Fix: {SET_REPO_EMAIL}",
            ], info="Unreadable remote")

        emails, matched = allowed_emails(rules, remote)
        if not matched:
            return CheckResult(NAME, True, [], skipped=True, info=(
                f"no identity rule for {remote}; add one with: "
                f"git-scan identity allow {remote.split('/')[0]}/<owner> <email>"))
        if any(email_matches(e, email) for e in emails):
            return CheckResult(NAME, True, info=f"{email} allowed for {matched[0]['remote']}")
        return CheckResult(NAME, False, [
            f"Commit email '{email}' is not allowed for {remote}",
            f"Allowed by rule '{matched[0]['remote']}': {', '.join(emails)}",
            f"Fix: {SET_REPO_EMAIL}, or: git-scan identity allow {matched[0]['remote']} {email}",
        ], info="Email not allowed for this remote")

    except Exception as e:
        return CheckResult(NAME, True, [], skipped=True, info=f"Check failed: {e}")


def _remote_url(path: Path) -> Optional[str]:
    """URL of the branch's upstream remote, else ``origin``, else the first."""
    remote = None
    branch = _git(path, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch:
        remote = _git(path, "config", "--get", f"branch.{branch}.remote")
    remotes = (_git(path, "remote") or "").split()
    if remote not in remotes:
        remote = "origin" if "origin" in remotes else (remotes[0] if remotes else None)
    return _git(path, "remote", "get-url", remote) if remote else None


def run_checks(repo_path: str, config=None, **kwargs) -> List[CheckResult]:
    rules = config.raw.get("identity") or [] if config else []
    return [check_git_identity(repo_path, rules)]
