"""Install, remove, and report the git-scan pre-commit hook.

Two levels:

- **global**: one hook file in a directory git uses for every repository
  (``core.hooksPath``), default ``$XDG_CONFIG_HOME/git/hooks``.
- **local**: ``.git/hooks/pre-commit`` of one repository. Git ignores a
  repository's own hooks directory while ``core.hooksPath`` is set at any
  level, so status reports which location git will actually use.

The hook script is a stable, machine-independent contract: no absolute
paths, nothing per machine. Every script git-scan has ever shipped is
listed in ``SCRIPTS``, so a hook file is classified by exact byte match
against known content and nothing else:

- ``installed``: the current script;
- ``outdated``: an earlier git-scan script (safe to upgrade: its content is
  known);
- ``other``: anything else, another tool's hook or an edited copy. git-scan
  never replaces it without ``force`` and never deletes it, because it
  can't tell a deliberate edit from a foreign script and doesn't guess.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

# Every script ever shipped, oldest first. Append; never edit an entry.
SCRIPTS = [
    "#!/bin/sh\n# Installed by git-scan\nexec git-scan run\n",
    # PATH line: GUI git clients run hooks with a minimal PATH; pipx installs
    # to ~/.local/bin on every platform. Resolved at run time, so the script
    # is identical on every machine.
    '#!/bin/sh\n# Installed by git-scan\nPATH="$HOME/.local/bin:$PATH"\nexec git-scan run\n',
]
HOOK_SCRIPT = SCRIPTS[-1]


class HookError(ValueError):
    pass


def default_hooks_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "git" / "hooks"


def _git(*args: str, cwd: Optional[str] = None) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else ""


def hook_state(hook: Path) -> str:
    """``absent``, ``installed``, ``outdated``, or ``other``; exact matches only."""
    if not hook.exists():
        return "absent"
    try:
        content = hook.read_bytes()
    except OSError:
        return "other"
    if content == HOOK_SCRIPT.encode():
        return "installed"
    if any(content == s.encode() for s in SCRIPTS[:-1]):
        return "outdated"
    return "other"


def hook_path(local: bool = False, hooks_dir: Optional[Path] = None,
              repo_path: str = ".") -> Path:
    if local:
        git_dir = _git("rev-parse", "--git-dir", cwd=repo_path)
        if not git_dir:
            raise HookError(f"{Path(repo_path).resolve()} is not a git repository")
        return (Path(repo_path) / git_dir).resolve() / "hooks" / "pre-commit"
    return Path(hooks_dir or default_hooks_dir()).expanduser() / "pre-commit"


def install_hook(hooks_dir: Optional[Path] = None, force: bool = False,
                 local: bool = False, repo_path: str = ".") -> str:
    """Write the hook. Returns a sentence describing what was done."""
    hook = hook_path(local, hooks_dir, repo_path)
    state = hook_state(hook)
    if state == "other" and not force:
        raise HookError(f"{hook} exists and is not a git-scan script (another tool's "
                        "hook, or an edited one); re-run with --force to replace it")
    if state == "installed":
        verb = "already installed:"
    else:
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(HOOK_SCRIPT)
        hook.chmod(0o755)
        verb = {"absent": "wrote", "outdated": "upgraded", "other": "replaced"}[state]

    if local:
        hp = _git("config", "--get", "core.hooksPath", cwd=repo_path)
        note = (f"; note: core.hooksPath is set to {hp}, so git will ignore this "
                "repository's own hooks until that setting is removed" if hp else "")
        return f"{verb} {hook}{note}"

    current = _git("config", "--global", "--get", "core.hooksPath")
    if not current:
        subprocess.run(["git", "config", "--global", "core.hooksPath", str(hook.parent)],
                       check=True)
        return f"{verb} {hook}; set core.hooksPath to {hook.parent}"
    if Path(current).expanduser().resolve() != hook.parent.resolve():
        return (f"{verb} {hook}; note: core.hooksPath is {current}, so this hook "
                f"won't run until it points at {hook.parent}")
    return f"{verb} {hook}"


def uninstall_hook(hooks_dir: Optional[Path] = None, local: bool = False,
                   repo_path: str = ".") -> str:
    """Remove a git-scan hook (current or outdated). Any other hook is left alone.

    The ``core.hooksPath`` setting is not changed: other hooks may live
    in that directory.
    """
    hook = hook_path(local, hooks_dir, repo_path)
    state = hook_state(hook)
    if state == "absent":
        return f"no pre-commit hook at {hook}"
    if state == "other":
        raise HookError(f"{hook} is not a git-scan script; remove it yourself if that is intended")
    hook.unlink()
    return f"removed {hook}"


def status(hooks_dir: Optional[Path] = None, repo_path: str = ".") -> dict:
    """Facts only: the state at each level, and which one git will use.

    ``effective`` is where git runs pre-commit hooks from for ``repo_path``:
    the ``core.hooksPath`` directory if that is set (at any level), else
    the repository's own ``.git/hooks``.
    """
    global_hook = hook_path(False, hooks_dir)
    info: dict = {
        "global": {"path": str(global_hook), "state": hook_state(global_hook)},
        "local": None,
        "hooks_path": _git("config", "--get", "core.hooksPath", cwd=repo_path) or None,
        "effective": None,
    }
    try:
        local_hook = hook_path(True, repo_path=repo_path)
        info["local"] = {"path": str(local_hook), "state": hook_state(local_hook)}
    except HookError:
        pass
    if info["hooks_path"]:
        eff = Path(info["hooks_path"]).expanduser() / "pre-commit"
        info["effective"] = {"path": str(eff), "state": hook_state(eff)}
    elif info["local"]:
        info["effective"] = dict(info["local"])
    return info


def format_status(info: dict) -> str:
    lines = [f"  global  {info['global']['path']}  ({info['global']['state']})"]
    if info["local"]:
        lines.append(f"  local   {info['local']['path']}  ({info['local']['state']})")
    if info["hooks_path"]:
        lines.append(f"  core.hooksPath = {info['hooks_path']}")
    if info["effective"]:
        lines.append(f"  git runs:  {info['effective']['path']}  ({info['effective']['state']})")
    elif not info["local"]:
        lines.append("  git runs:  each repository's own .git/hooks (core.hooksPath not set)")
    return "\n".join(lines)
