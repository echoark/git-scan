"""Git hosts and the providers that answer "is this repository private?".

A host is registered by name with a provider type, in the machine's global
git config, one named subsection per host:

    [git-scan-host "git.corp.example"]
        type = github
    [git-scan-host "git.internal.example"]
        type = other
        allow-relaxed = true

``github.com`` is built in. The type names a provider: an object that
either asks the host for a repository's visibility or says it can't. Built-in
providers are ``github`` (github.com and GitHub Enterprise, through
``gh api``), ``bitbucket`` (registered but not supported yet) and ``other``
(no check; the user may vouch for the whole host with ``allow-relaxed``).
More providers are added as plugins under the ``git_scan.host_providers``
entry-point group, each a ``HostProvider`` subclass; the CLI, the storage and
the relaxed-review rules don't change when one is added.

Nothing here is written to git-scan's YAML configuration.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Dict, List, Optional

SECTION = "git-scan-host"
GITHUB_COM = "github.com"
VISIBILITY_TIMEOUT = 30   # seconds for one visibility call
ENTRY_POINT_GROUP = "git_scan.host_providers"


class HostError(ValueError):
    """A host can't be registered, or can't answer; the message says why."""


class HostProvider:
    """One kind of git host.

    ``verifies`` says whether the provider can ask the host for a
    repository's visibility. A provider that can't is never trusted on its
    own; only a user's ``allow-relaxed`` on the host makes its repositories
    eligible.
    """

    name = ""
    verifies = False
    supported = True
    description = ""

    def visibility(self, host: str, owner: str, repo: str) -> str:
        """The host's answer: ``private``, ``public``, ``internal``, ...

        Raises HostError when there is no answer.
        """
        raise HostError(f"{self.name} hosts can't be asked about visibility")


class GitHubProvider(HostProvider):
    name = "github"
    verifies = True
    description = "github.com or GitHub Enterprise; visibility from the REST API (gh api)"

    def visibility(self, host: str, owner: str, repo: str) -> str:
        argv = ["gh", "api"]
        if host != GITHUB_COM:
            argv += ["--hostname", host]
        argv += [f"repos/{owner}/{repo}", "--jq", ".visibility"]
        try:
            res = subprocess.run(argv, capture_output=True, text=True, timeout=VISIBILITY_TIMEOUT)
        except FileNotFoundError:
            raise HostError("the GitHub CLI (gh) is not installed, so the host can't be asked "
                            "whether the repository is private")
        except subprocess.TimeoutExpired:
            raise HostError(f"{host} did not answer within {VISIBILITY_TIMEOUT}s")
        if res.returncode != 0:
            tail = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["no output"]
            raise HostError(f"{host}'s visibility check failed: {tail[0]}")
        answer = res.stdout.strip()
        if not answer:
            raise HostError(f"{host}'s visibility check returned nothing")
        return answer


class BitbucketProvider(HostProvider):
    name = "bitbucket"
    verifies = True
    supported = False
    description = "Bitbucket (not supported yet)"


class OtherProvider(HostProvider):
    name = "other"
    verifies = False
    description = "a host git-scan can't ask; eligible only with --allow-relaxed"


BUILTIN: Dict[str, HostProvider] = {p.name: p for p in
                                    (GitHubProvider(), BitbucketProvider(), OtherProvider())}


def providers() -> Dict[str, HostProvider]:
    """Built-in providers plus any installed plugins (a plugin can't replace
    a built-in)."""
    found = dict(BUILTIN)
    try:
        eps = entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:          # pragma: no cover - older importlib.metadata
        eps = entry_points().get(ENTRY_POINT_GROUP, [])
    for ep in eps:
        if ep.name in found:
            continue
        try:
            cls = ep.load()
            provider = cls() if isinstance(cls, type) else cls
        except Exception:      # a broken plugin never breaks git-scan
            continue
        if isinstance(provider, HostProvider):
            provider.name = provider.name or ep.name
            found[ep.name] = provider
    return found


@dataclass
class Host:
    host: str
    type: str
    allow_relaxed: bool = False
    builtin: bool = False

    @property
    def provider(self) -> Optional[HostProvider]:
        return providers().get(self.type)


def _git_global(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "config", "--global", *args], capture_output=True, text=True)


def _read() -> Dict[str, Dict[str, str]]:
    res = _git_global("--get-regexp", rf"^{SECTION}\.")
    rows: Dict[str, Dict[str, str]] = {}
    for line in res.stdout.splitlines():
        key, _, value = line.partition(" ")
        rest = key[len(SECTION) + 1:]
        host, _, field = rest.rpartition(".")
        if host:
            rows.setdefault(host.lower(), {})[field.lower()] = value
    return rows


def list_hosts() -> List[Host]:
    """Every registered host, github.com first."""
    out = [Host(GITHUB_COM, "github", builtin=True)]
    for host, fields in sorted(_read().items()):
        if host == GITHUB_COM:
            continue
        out.append(Host(host, fields.get("type", ""),
                        allow_relaxed=fields.get("allow-relaxed", "").lower() == "true"))
    return out


def get_host(host: str) -> Optional[Host]:
    host = host.lower()
    return next((h for h in list_hosts() if h.host == host), None)


def add_host(kind: str, host: str, allow_relaxed: bool = False) -> Host:
    host = host.strip().lower().strip("/")
    if not host or "/" in host or ":" in host or "@" in host or " " in host:
        raise HostError(f"'{host}' is not a host name (e.g. git.corp.example)")
    if host == GITHUB_COM:
        raise HostError("github.com is built in")
    known = providers()
    provider = known.get(kind)
    if provider is None:
        raise HostError(f"unknown host type '{kind}' (known: {', '.join(sorted(known))})")
    if not provider.supported:
        raise HostError(f"{kind} hosts are not supported yet; use 'other' with --allow-relaxed "
                        "if you vouch for this host")
    if allow_relaxed and provider.verifies:
        raise HostError(f"--allow-relaxed is only for hosts git-scan can't ask; {kind} hosts are "
                        "checked for each repository")
    remove_host(host, missing_ok=True)
    _git_global(f"{SECTION}.{host}.type", kind)
    if allow_relaxed:
        _git_global(f"{SECTION}.{host}.allow-relaxed", "true")
    return Host(host, kind, allow_relaxed=allow_relaxed)


def remove_host(host: str, missing_ok: bool = False) -> bool:
    host = host.strip().lower()
    if host == GITHUB_COM:
        raise HostError("github.com is built in and can't be removed")
    res = _git_global("--remove-section", f"{SECTION}.{host}")
    if res.returncode != 0 and not missing_ok:
        raise HostError(f"{host} is not registered")
    return res.returncode == 0
