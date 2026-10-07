"""Install git-scan as a git pre-commit hook.

The hook is one line, ``git-scan run``, written to a hooks directory that
git uses for every repository (``core.hooksPath``). The default directory is
``$XDG_CONFIG_HOME/git/hooks`` (``~/.config/git/hooks``).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

HOOK_SCRIPT = "#!/bin/sh\n# Installed by git-scan\nexec git-scan run\n"


class HookError(ValueError):
    pass


def default_hooks_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "git" / "hooks"


def _global_hooks_path() -> str:
    res = subprocess.run(["git", "config", "--global", "--get", "core.hooksPath"],
                         capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else ""


def install_hook(hooks_dir: Path | None = None, force: bool = False) -> str:
    """Write the pre-commit hook and point git at its directory.

    Refuses to replace an existing pre-commit hook unless ``force``.
    Returns a sentence describing what was done.
    """
    hooks_dir = Path(hooks_dir or default_hooks_dir()).expanduser()
    hook = hooks_dir / "pre-commit"
    if hook.exists() and hook.read_text() != HOOK_SCRIPT and not force:
        raise HookError(f"{hook} already exists and is not git-scan's; "
                        "re-run with --force to replace it")
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook.write_text(HOOK_SCRIPT)
    hook.chmod(0o755)

    current = _global_hooks_path()
    note = ""
    if not current:
        subprocess.run(["git", "config", "--global", "core.hooksPath", str(hooks_dir)],
                       check=True)
        note = f"; set core.hooksPath to {hooks_dir}"
    elif Path(current).expanduser().resolve() != hooks_dir.resolve():
        note = (f"; note: git's core.hooksPath is {current}, so this hook won't run "
                f"until it points at {hooks_dir}")
    return f"wrote {hook}{note}"
