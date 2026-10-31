"""Install, remove, and report the git-scan hooks.

The pre-commit hook scans the staged change and the ``commit-msg`` hook
scans the commit message; both are installed together. With the push gate
(``pre_push=True``) two more sit beside them: ``pre-push`` runs ``git-scan
push-check`` and ``post-commit`` marks a commit whose scan passed (see
``marks``). Each is a one-line script that calls the CLI; all behavior
lives in Python.

A ``commit-msg`` hook that is not git-scan's (another tool's, such as a
commit-message linter) is left in place without ``force``, and the
install goes ahead without it: status then says commit messages are not
scanned there. Any other hook in the way stops the install.

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

# The commit-msg hook, installed with pre-commit; same rules.
MESSAGE_SCRIPTS = [
    '#!/bin/sh\n# Installed by git-scan\nPATH="$HOME/.local/bin:$PATH"\n'
    'exec git-scan run --message "$1"\n',
]

# The push gate's hooks, same rules: append, never edit an entry.
GATE_SCRIPTS = {
    "pre-push": [
        '#!/bin/sh\n# Installed by git-scan\nPATH="$HOME/.local/bin:$PATH"\n'
        'exec git-scan push-check "$@"\n',
    ],
    "post-commit": [
        '#!/bin/sh\n# Installed by git-scan\nPATH="$HOME/.local/bin:$PATH"\n'
        'exec git-scan hook post-commit\n',
    ],
}
ALL_SCRIPTS = {"pre-commit": SCRIPTS, "commit-msg": MESSAGE_SCRIPTS, **GATE_SCRIPTS}
HOOKS = ["pre-commit", "commit-msg", "post-commit", "pre-push"]

# What each hook protects when it is git-scan's, and what is lost when it isn't.
PROTECTS = {
    "pre-commit": ("scans each commit's changes and every file, branch, and tag name",
                   "commits are not scanned here"),
    "commit-msg": ("scans each commit message", "commit messages are not scanned here"),
    "post-commit": ("marks each commit whose scan passed, so a push can skip it",
                    "commits are not marked; a push scans every unpushed commit"),
    "pre-push": ("scans unpushed commits, and requires a recorded review when configured, before a push",
                 "pushes are not scanned"),
}
_FIX = {
    "pre-commit": "git-scan hook install", "commit-msg": "git-scan hook install",
    "post-commit": "git-scan hook install --pre-push", "pre-push": "git-scan hook install --pre-push",
}


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
    """``absent``, ``installed``, ``outdated``, or ``other``; exact matches only.

    The file name says which hook it is (``pre-commit``, ``pre-push``,
    ``post-commit``); it is compared with the scripts shipped for that hook.
    """
    if not hook.exists():
        return "absent"
    try:
        content = hook.read_bytes()
    except OSError:
        return "other"
    scripts = ALL_SCRIPTS.get(hook.name, SCRIPTS)
    if content == scripts[-1].encode():
        return "installed"
    if any(content == s.encode() for s in scripts[:-1]):
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


def _write_hook(hook: Path) -> str:
    """Write one hook file (state already checked). Returns what was done."""
    state = hook_state(hook)
    if state == "installed":
        return "already installed:"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(ALL_SCRIPTS[hook.name][-1])
    hook.chmod(0o755)
    return {"absent": "wrote", "outdated": "upgraded", "other": "replaced"}[state]


def install_hook(hooks_dir: Optional[Path] = None, force: bool = False,
                 local: bool = False, repo_path: str = ".", pre_push: bool = False) -> str:
    """Write pre-commit and commit-msg (and with ``pre_push``, the push
    gate's two hooks).

    Returns a sentence describing what was done. Without ``force``, another
    tool's commit-msg hook is left alone (the sentence says so), and nothing
    is written if any other target is another tool's hook.
    """
    hook = hook_path(local, hooks_dir, repo_path)
    targets = [hook, hook.parent / "commit-msg"]
    targets += [hook.parent / name for name in GATE_SCRIPTS] if pre_push else []
    kept = None
    if not force:
        for t in targets:
            if hook_state(t) != "other":
                continue
            if t.name == "commit-msg":
                kept = t
                continue
            raise HookError(f"{t} exists and is not a git-scan script (another tool's "
                            "hook, or an edited one); re-run with --force to replace it")
    done = [(t.name, _write_hook(t)) for t in targets if t != kept]
    changed = [f"{v} {n}" for n, v in done if v != "already installed:"]
    verb = ", ".join(changed) + " in" if changed else "already installed:"
    where = hook.parent
    if kept:
        verb = (f"left commit-msg alone (not a git-scan script, so commit messages are not "
                f"scanned; --force replaces it); {verb}")

    if local:
        hp = _git("config", "--get", "core.hooksPath", cwd=repo_path)
        note = (f"; note: core.hooksPath is set to {hp}, so git will ignore this "
                "repository's own hooks until that setting is removed" if hp else "")
        return f"{verb} {where}{note}"

    current = _git("config", "--global", "--get", "core.hooksPath")
    if not current:
        subprocess.run(["git", "config", "--global", "core.hooksPath", str(hook.parent)],
                       check=True)
        return f"{verb} {where}; set core.hooksPath to {hook.parent}"
    if Path(current).expanduser().resolve() != hook.parent.resolve():
        return (f"{verb} {hook}; note: core.hooksPath is {current}, so this hook "
                f"won't run until it points at {hook.parent}")
    return f"{verb} {where}"


def uninstall_hook(hooks_dir: Optional[Path] = None, local: bool = False,
                   repo_path: str = ".") -> str:
    """Remove a git-scan hook (current or outdated). Any other hook is left alone.

    The ``core.hooksPath`` setting is not changed: other hooks may live
    in that directory.
    """
    hook = hook_path(local, hooks_dir, repo_path)
    removed = []
    for name in ("commit-msg", *GATE_SCRIPTS):
        gate = hook.parent / name
        if hook_state(gate) in ("installed", "outdated"):
            gate.unlink()
            removed.append(name)
    gate_note = f" (and {', '.join(removed)})" if removed else ""
    state = hook_state(hook)
    if state == "absent":
        return f"no pre-commit hook at {hook}" + (f"; removed {', '.join(removed)}" if removed else "")
    if state == "other":
        raise HookError(f"{hook} is not a git-scan script; remove it yourself if that is intended")
    hook.unlink()
    return f"removed {hook}{gate_note}"


def _row(directory: Path, name: str) -> dict:
    state = hook_state(directory / name)
    does, lost = PROTECTS[name]
    if state == "installed":
        text = does
    elif state == "outdated":
        text = f"{does} (an earlier git-scan script; {_FIX[name]} upgrades it)"
    elif state == "other":
        text = f"{lost}: not a git-scan script ({_FIX[name]} --force replaces it)"
    else:
        text = f"{lost} ({_FIX[name]})"
    return {"hook": name, "path": str(directory / name), "state": state, "protects": text}


def status(hooks_dir: Optional[Path] = None, repo_path: str = ".") -> dict:
    """Facts only: the state at each level, and what git will run.

    ``effective`` is the pre-commit hook git runs for ``repo_path``: in the
    ``core.hooksPath`` directory if that is set (at any level), else in the
    repository's own ``.git/hooks``. ``hooks`` is one row per hook in that
    directory: its state and, in words, what it protects or what is not
    protected without it.
    """
    global_hook = hook_path(False, hooks_dir)
    info: dict = {
        "global": {"path": str(global_hook), "state": hook_state(global_hook)},
        "local": None,
        "hooks_path": _git("config", "--get", "core.hooksPath", cwd=repo_path) or None,
        "effective": None,
        "hooks": None,
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
    if info["effective"]:
        d = Path(info["effective"]["path"]).parent
        info["hooks"] = [_row(d, name) for name in HOOKS]
    return info


def format_status(info: dict) -> str:
    lines = [f"  global  {info['global']['path']}  ({info['global']['state']})"]
    if info["local"]:
        lines.append(f"  local   {info['local']['path']}  ({info['local']['state']})")
    if info["hooks_path"]:
        lines.append(f"  core.hooksPath = {info['hooks_path']}")
    for row in info.get("hooks") or []:
        lines.append(f"  {row['hook']:<12} {row['state']:<10} {row['protects']}")
    if info["effective"]:
        lines.append(f"  git runs:  {info['effective']['path']}  ({info['effective']['state']})")
    elif not info["local"]:
        lines.append("  git runs:  each repository's own .git/hooks (core.hooksPath not set)")
    return "\n".join(lines)
