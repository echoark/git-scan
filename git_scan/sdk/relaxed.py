"""Relaxed review: eligibility per repository, used review by review.

Two separate things:

* **Eligibility** says this repository may get a relaxed review. ``mark``
  establishes it once per clone and writes it to the repository's own
  ``.git/config`` (local to the clone, never pushed), keyed by the exact
  ``origin`` URL. It needs a remote whose host says the repository is
  private (github.com and registered ``github`` hosts are asked through
  their REST API), or a host the user registered as ``other`` with
  ``allow-relaxed``. A repository with no remote, on an unregistered host,
  or declared public-prospective in any configuration layer is never
  eligible.
* **Use** is ``git-scan review --relaxed``, one review at a time.
  ``check_use`` refuses unless the repository is eligible for the same
  exact URL, and asks a verifying host again each time. Without the flag
  every review is public.
* **The commit scan** (pre-commit, commit-msg, the push gate) resolves
  each entry's ``relaxed`` value too, in an eligible clone the host reports
  private (``commit_mode``). It must not cost a network call per commit, so
  it reads the host's last answer, which ``mark`` and ``check_use`` record
  with its time, and asks again only when that answer is older than
  ``PRIVACY_TTL``. Anything it can't establish, it treats as public.

Nothing here writes git-scan's YAML configuration: per-repository state
doesn't belong in a file that is backed up and meant to be verified as the
user's, and a new clone or session simply marks again.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from . import hosts
from .config import MergedConfig, load_config
from .steps.git_identity import is_local_remote, normalize_remote

KEY_URL = "git-scan.relaxed-remote"
KEY_BASIS = "git-scan.relaxed-basis"
KEY_CHECKED = "git-scan.relaxed-checked"   # "<epoch seconds> private|not-private" from the last ask
PRIVACY_TTL = 24 * 3600                     # how long the commit scan trusts that answer


class RelaxedError(ValueError):
    """Not eligible, or the relaxed review can't be used; the message says why."""


@dataclass
class Marking:
    url: str
    basis: str            # "verified private by HOST" or "HOST vouched for by the user"
    already: bool = False


def _git(repo_path: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo_path, capture_output=True, text=True)


def origin_url(repo_path: str = ".") -> Optional[str]:
    res = _git(repo_path, "remote", "get-url", "origin")
    url = res.stdout.strip() if res.returncode == 0 else ""
    return url or None


def public_prospective(config: MergedConfig) -> bool:
    """Whether any layer declares the repository public-prospective. Read
    per layer, like ``review_required``: a layer can declare it, never lift
    a declaration another layer made."""
    return any(bool(((layer or {}).get("review") or {}).get("public_prospective"))
               for layer in config.layers.values())


def _parts(url: Optional[str]) -> Tuple[str, str, str]:
    """``(host, owner, repo)`` of a remote URL; RelaxedError without one."""
    if not url or is_local_remote(url):
        raise RelaxedError("this repository has no remote (origin) on a git host; a repository "
                           "without one never gets a relaxed review")
    remote = normalize_remote(url)
    parts = remote.split("/") if remote else []
    if len(parts) < 3:
        raise RelaxedError(f"can't read host/owner/repo from the remote '{url}'")
    return parts[0], "/".join(parts[1:-1]), parts[-1]


def _verify(url: str) -> str:
    """Why this remote qualifies now; RelaxedError if it doesn't."""
    host, owner, repo = _parts(url)
    entry = hosts.get_host(host)
    if entry is None:
        raise RelaxedError(f"{host} is not a registered host. Register it with "
                           f"'git-scan hosts add github|other {host}' (see git-scan hosts --help)")
    provider = entry.provider
    if provider is None:
        raise RelaxedError(f"{host} is registered as '{entry.type}', a host type this git-scan "
                           "doesn't know (a missing plugin?)")
    if not provider.supported:
        raise RelaxedError(f"{entry.type} hosts are not supported yet")
    if provider.verifies:
        try:
            visibility = provider.visibility(host, owner, repo)
        except hosts.HostError as e:
            raise RelaxedError(str(e))
        if visibility != "private":
            raise RelaxedError(f"{host} reports {owner}/{repo} as '{visibility}', not private")
        return f"verified private by {host}"
    if entry.allow_relaxed:
        return f"{host} vouched for by the user (no check possible)"
    raise RelaxedError(f"{host} is a host git-scan can't ask, and it isn't registered with "
                       "--allow-relaxed")


def _record(repo_path: str, private: bool, now: Optional[float] = None) -> None:
    stamp = int(now if now is not None else time.time())
    _git(repo_path, "config", "--local", KEY_CHECKED,
         f"{stamp} {'private' if private else 'not-private'}")


def _recorded(repo_path: str) -> Optional[Tuple[float, bool]]:
    """``(when, private)`` from the host's last answer, if one is recorded."""
    res = _git(repo_path, "config", "--local", "--get", KEY_CHECKED)
    parts = res.stdout.split() if res.returncode == 0 else []
    if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in ("private", "not-private"):
        return None
    return float(parts[0]), parts[1] == "private"


def _verify_and_record(repo_path: str, url: str) -> str:
    try:
        basis = _verify(url)
    except RelaxedError:
        _record(repo_path, False)
        raise
    _record(repo_path, True)
    return basis


def eligible_url(repo_path: str = ".") -> Optional[str]:
    """The remote URL this clone is marked eligible for, if any."""
    res = _git(repo_path, "config", "--local", "--get", KEY_URL)
    return res.stdout.strip() or None if res.returncode == 0 else None


def mark(repo_path: str = ".") -> Marking:
    """Make this repository eligible for relaxed review, or report that it
    already is (for the same exact remote URL)."""
    url = origin_url(repo_path)
    if public_prospective(load_config(repo_path)):
        raise RelaxedError("this repository's configuration declares it public-prospective "
                           "(review.public_prospective), so it is always reviewed as public")
    if url and eligible_url(repo_path) == url:
        basis = _git(repo_path, "config", "--local", "--get", KEY_BASIS).stdout.strip()
        return Marking(url, basis, already=True)
    basis = _verify_and_record(repo_path, url)
    _git(repo_path, "config", "--local", KEY_URL, url)
    _git(repo_path, "config", "--local", KEY_BASIS, basis)
    return Marking(url, basis)


def unmark(repo_path: str = ".") -> bool:
    """Drop this clone's eligibility; False if it had none."""
    had = eligible_url(repo_path) is not None
    _git(repo_path, "config", "--local", "--unset", KEY_URL)
    _git(repo_path, "config", "--local", "--unset", KEY_BASIS)
    _git(repo_path, "config", "--local", "--unset", KEY_CHECKED)
    return had


def check_use(repo_path: str, config: MergedConfig) -> str:
    """Why a relaxed review may run now; RelaxedError if it may not."""
    if public_prospective(config):
        raise RelaxedError("this repository is declared public-prospective; its reviews are "
                           "always public")
    url = origin_url(repo_path)
    _parts(url)
    if eligible_url(repo_path) != url:
        raise RelaxedError("this repository is not marked eligible for a relaxed review "
                           "(git-scan relax)")
    return _verify_and_record(repo_path, url)


def commit_mode(repo_path: str, config: MergedConfig,
                now: Optional[float] = None) -> Optional[str]:
    """Why the commit scan may apply each entry's ``relaxed`` value here, or
    None for the strict (public) values.

    Relaxed needs what a relaxed review needs: no public-prospective
    declaration, eligibility for this exact ``origin`` URL, and a host that
    says private. A host git-scan can't ask is taken as the user vouched for
    it (no network). A host it can ask is not asked per commit: its last
    answer stands for ``PRIVACY_TTL``; an older one, or none, is asked again
    once and recorded. Any error, or an answer other than private, is strict.
    """
    now = now if now is not None else time.time()
    try:
        if public_prospective(config):
            return None
        url = origin_url(repo_path)
        host, _, _ = _parts(url)
        if eligible_url(repo_path) != url:
            return None
        entry = hosts.get_host(host)
        if entry is None or entry.provider is None or not entry.provider.supported:
            return None
        if not entry.provider.verifies:
            return _verify(url)
        last = _recorded(repo_path)
        if last is not None and 0 <= now - last[0] < PRIVACY_TTL:
            return f"verified private by {host}" if last[1] else None
        return _verify_and_record(repo_path, url)
    except RelaxedError:
        return None
