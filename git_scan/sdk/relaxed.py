"""Relaxed review: a marking on one exact repository, kept in the cache.

Every repository is reviewed in public mode unless its exact ``origin``
URL has been marked here. The marking lives in the XDG cache
(``${XDG_CACHE_HOME:-~/.cache}/git-scan/relaxed.json``), never in a
configuration layer, so no repository can grant it to itself and losing the
cache only makes reviews stricter.

Marking a GitHub remote needs a live REST answer of ``private`` for that
repository (``gh api repos/OWNER/REPO --jq .visibility``); anything else,
including no answer, refuses. The same check runs again at review time and
falls back to public when it does not answer ``private``. A remote on any
other host can only be marked on the owner's own statement that it is
private, which the CLI takes at an interactive terminal. A repository with
no remote, or one whose configuration declares it public-prospective, is
never relaxed.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

from .config import MergedConfig, load_config
from .steps.git_identity import is_local_remote, normalize_remote

GITHUB_HOST = "github.com"
VISIBILITY_TIMEOUT = 30   # seconds for the GitHub REST call


class RelaxedError(ValueError):
    """The repository can't be marked relaxed; the message says why."""


def cache_path() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "git-scan" / "relaxed.json"


def _load() -> Dict[str, dict]:
    try:
        data = json.loads(cache_path().read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _save(data: Dict[str, dict]) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


def origin_url(repo_path: str = ".") -> Optional[str]:
    res = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo_path,
                         capture_output=True, text=True)
    url = res.stdout.strip() if res.returncode == 0 else ""
    return url or None


def github_repo(url: str) -> Optional[Tuple[str, str]]:
    """``(owner, repo)`` when ``url`` is a github.com repository, else None."""
    remote = normalize_remote(url)
    if not remote:
        return None
    parts = remote.split("/")
    if parts[0] != GITHUB_HOST or len(parts) != 3:
        return None
    return parts[1], parts[2]


def github_visibility(owner: str, repo: str) -> str:
    """GitHub's answer for the repository's visibility, from the REST API.

    Raises RelaxedError when there is no answer: gh missing, not signed in,
    no network, an error status, or empty output.
    """
    try:
        res = subprocess.run(["gh", "api", f"repos/{owner}/{repo}", "--jq", ".visibility"],
                             capture_output=True, text=True, timeout=VISIBILITY_TIMEOUT)
    except FileNotFoundError:
        raise RelaxedError("the GitHub CLI (gh) is not installed, so GitHub can't be asked "
                           "whether the repository is private")
    except subprocess.TimeoutExpired:
        raise RelaxedError(f"GitHub did not answer within {VISIBILITY_TIMEOUT}s")
    if res.returncode != 0:
        tail = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RelaxedError(f"GitHub's visibility check failed: {tail[0]}")
    answer = res.stdout.strip()
    if not answer:
        raise RelaxedError("GitHub's visibility check returned nothing")
    return answer


def public_prospective(config: MergedConfig) -> bool:
    """Whether any layer declares the repository public-prospective.

    Read per layer, like ``review_required``: a layer can declare it, never
    lift a declaration another layer made.
    """
    return any(bool(((layer or {}).get("review") or {}).get("public_prospective"))
               for layer in config.layers.values())


def _checked_url(repo_path: str) -> str:
    url = origin_url(repo_path)
    if not url or is_local_remote(url) or not normalize_remote(url):
        raise RelaxedError("this repository has no remote (origin) git-scan can identify; "
                           "a repository without one is never reviewed as relaxed")
    if public_prospective(load_config(repo_path)):
        raise RelaxedError("this repository's configuration declares it public-prospective "
                           "(review.public_prospective), so it is always reviewed as public")
    return url


def mark(repo_path: str = ".", owner_statement: bool = False) -> dict:
    """Mark the current repository's exact origin URL for relaxed review.

    A github.com remote is marked only when GitHub answers ``private`` now;
    ``owner_statement`` is ignored for it. Any other host is marked only with
    ``owner_statement``: the owner's own statement that the repository is
    private, which the CLI takes at an interactive terminal.
    """
    url = _checked_url(repo_path)
    gh = github_repo(url)
    if gh:
        visibility = github_visibility(*gh)
        if visibility != "private":
            raise RelaxedError(f"GitHub reports {gh[0]}/{gh[1]} as '{visibility}', not private; "
                               "it stays in public review")
        basis = "github-private"
    elif owner_statement:
        basis = "owner-statement"
    else:
        raise RelaxedError(f"{normalize_remote(url)} is not on github.com, so its visibility "
                           "can't be checked; only its owner can state that it is private, "
                           "at an interactive terminal: git-scan review relax")
    entry = {"basis": basis, "marked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    data = _load()
    data[url] = entry
    _save(data)
    return {"url": url, **entry}


def unmark(repo_path: str = ".") -> bool:
    """Drop the current repository's marking; False if it had none."""
    url = origin_url(repo_path)
    data = _load()
    if not url or url not in data:
        return False
    del data[url]
    _save(data)
    return True


def marked() -> Dict[str, dict]:
    """Every marking in the cache, by exact remote URL."""
    return _load()


def review_mode(repo_path: str, config: MergedConfig) -> Tuple[str, str]:
    """``(mode, why)`` for a review now: ``relaxed`` only for a marked
    repository that still qualifies; any doubt is ``public``."""
    url = origin_url(repo_path)
    if not url or is_local_remote(url) or not normalize_remote(url):
        return "public", "no remote"
    if public_prospective(config):
        return "public", "declared public-prospective"
    entry = _load().get(url)
    if not entry:
        return "public", "not marked relaxed"
    gh = github_repo(url)
    if gh:
        try:
            visibility = github_visibility(*gh)
        except RelaxedError as e:
            return "public", f"marked relaxed, but {e}"
        if visibility != "private":
            return "public", f"marked relaxed, but GitHub now reports it as '{visibility}'"
        return "relaxed", "GitHub reports it private"
    if entry.get("basis") == "owner-statement":
        return "relaxed", "marked private by its owner"
    return "public", "marking not recognized"
