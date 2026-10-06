"""Git identity check - the commit email must be safe for the repo's remote.

Rules, in order (all local, no network):

1. The repo sets its own ``user.email``: trusted. The commit must use it.
2. Inherited email, github.com remote: the email must be the noreply
   address of an account the GitHub CLI is logged into for github.com.
3. Inherited email, any other host: the email's domain must be the host's
   domain or a parent of it (``work@example.com`` for
   ``git.example.com``), or the noreply address of an account the GitHub
   CLI is logged into for that host.
4. Otherwise: fail, with what to set.

"Inherited" means the email comes from global/system config (or the
environment), not from the repo's own ``.git/config``. A repo with no
remote passes: nothing leaves the machine.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Set

import yaml

from ..utils import CheckResult

NAME = "Git identity"
GITHUB = "github.com"
SET_REPO_EMAIL = "git config user.email <address for this repo>"

_NOREPLY = re.compile(r"^(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.(.+)$", re.I)


def _git(path: Path, *args: str) -> Optional[str]:
    res = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else None


def remote_host(url: str) -> Optional[str]:
    """Host of a git remote URL (https, ssh://, or scp-style)."""
    m = re.match(r"^[a-z][a-z0-9+.-]*://(?:[^@/]+@)?([^/:]+)", url, re.I)
    if not m:
        m = re.match(r"^(?:[^@/]+@)?([^/:]+):", url)
    return m.group(1).lower() if m else None


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


def gh_logins(host: str) -> Set[str]:
    """Accounts the GitHub CLI is logged into for ``host``, from its local file."""
    if os.environ.get("GH_CONFIG_DIR"):
        base = Path(os.environ["GH_CONFIG_DIR"])
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        base = Path(xdg) / "gh"
    try:
        data = yaml.safe_load((base / "hosts.yml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return set()
    entry = data.get(host) or {}
    logins = set((entry.get("users") or {}).keys())
    if entry.get("user"):
        logins.add(entry["user"])
    return {str(login).lower() for login in logins}


def noreply_login(email: str, host: str) -> Optional[str]:
    """The login in a ``users.noreply.<host>`` address, else ``None``."""
    m = _NOREPLY.match(email)
    if m and m.group(2).lower() == host:
        return m.group(1).lower()
    return None


def domain_matches(email: str, host: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    return "." in domain and (host == domain or host.endswith("." + domain))


def check_git_identity(repo_path: str) -> CheckResult:
    path = Path(repo_path)
    try:
        ident = _git(path, "var", "GIT_AUTHOR_IDENT")
        m = re.search(r"<(.*)>", ident or "")
        if not m:
            return CheckResult(NAME, True, [], skipped=True,
                               info="Not a git repo or can't determine identity")
        email = m.group(1).strip()

        # Rule 1: the repo's own setting is trusted.
        repo_email = _git(path, "config", "--local", "--get", "user.email")
        if repo_email:
            if email != repo_email:
                return CheckResult(NAME, False, [
                    f"Commit email '{email}' differs from the repo's user.email '{repo_email}'",
                    "Something in the environment (e.g. GIT_AUTHOR_EMAIL) overrides it",
                ], info=f"Conflict: {email} vs {repo_email}")
            return CheckResult(NAME, True, info=f"Repo setting: {email}")

        url = _remote_url(path)
        if not url:
            return CheckResult(NAME, True, info=f"No remote: {email}")
        host = remote_host(url)
        if not host:
            return CheckResult(NAME, False, [
                f"Can't read the host from remote '{url}'",
                f"Fix: {SET_REPO_EMAIL}",
            ], info="Unknown remote host")

        logins = gh_logins(host)
        login = noreply_login(email, host)
        if login and login in logins:
            return CheckResult(NAME, True, info=f"Noreply of {login}@{host}")

        if host == GITHUB:
            # Rule 2.
            if not logins:
                findings = [
                    f"Inherited email '{email}' on {host}, and the GitHub CLI "
                    f"isn't logged into {host}",
                    f"Fix: gh auth login --hostname {host}",
                    f"Or: {SET_REPO_EMAIL}",
                ]
            elif login:
                findings = [
                    f"'{email}' is the noreply address of '{login}', which the "
                    f"GitHub CLI isn't logged into ({', '.join(sorted(logins))} are)",
                    f"Fix: gh auth login --hostname {host}",
                    f"Or: {SET_REPO_EMAIL}",
                ]
            else:
                findings = [
                    f"Inherited email '{email}' on {host}: only a noreply address "
                    f"of a logged-in account ({', '.join(sorted(logins))}) is "
                    "allowed without a repo setting",
                    f"Fix: {SET_REPO_EMAIL}",
                ]
            return CheckResult(NAME, False, findings, info="Unverified email")

        # Rule 3.
        if domain_matches(email, host):
            return CheckResult(NAME, True, info=f"Domain matches {host}: {email}")
        return CheckResult(NAME, False, [
            f"Inherited email '{email}' doesn't match the remote host {host}",
            f"Fix: {SET_REPO_EMAIL}",
        ], info="Domain mismatch")

    except Exception as e:
        return CheckResult(NAME, True, [], skipped=True, info=f"Check failed: {e}")


def run_checks(repo_path: str, config=None, deep: bool = False, **kwargs) -> List[CheckResult]:
    """Run git identity check. Standard step interface."""
    return [check_git_identity(repo_path)]
