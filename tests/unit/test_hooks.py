"""Hook install, uninstall, and status at both levels, through real git."""

import subprocess

from click.testing import CliRunner

from git_scan.cli.main import cli
from git_scan.sdk.hooks import (
    hook_state, install_hook, uninstall_hook, status, HOOK_SCRIPT, SCRIPTS, HookError,
)


def _run(*args):
    return CliRunner().invoke(cli, list(args))


def _repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    return repo


def _global_hooks_path():
    res = subprocess.run(["git", "config", "--global", "--get", "core.hooksPath"],
                         capture_output=True, text=True)
    return res.stdout.strip()


def test_script_is_machine_independent():
    assert "/Users/" not in HOOK_SCRIPT and "/home/" not in HOOK_SCRIPT
    assert HOOK_SCRIPT == SCRIPTS[-1]


def test_global_install_sets_hooks_path_and_is_idempotent(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    hooks = tmp_path / "hooks"
    first = _run("hook", "install", "--hooks-dir", str(hooks))
    assert first.exit_code == 0 and "wrote" in first.output and "set core.hooksPath" in first.output
    assert _global_hooks_path() == str(hooks)
    assert hook_state(hooks / "pre-commit") == "installed"
    again = _run("hook", "install", "--hooks-dir", str(hooks))
    assert again.exit_code == 0 and "already installed" in again.output


def test_outdated_script_is_recognized_and_upgraded(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    hook = tmp_path / "hooks" / "pre-commit"
    hook.parent.mkdir()
    hook.write_text(SCRIPTS[0])
    assert hook_state(hook) == "outdated"
    r = _run("hook", "install", "--hooks-dir", str(hook.parent))
    assert r.exit_code == 0 and "upgraded" in r.output
    assert hook_state(hook) == "installed"


def test_edited_git_scan_script_is_neither_replaced_nor_deleted(tmp_path):
    hook = tmp_path / "hooks" / "pre-commit"
    hook.parent.mkdir()
    hook.write_text(HOOK_SCRIPT.replace("exec git-scan run", "exec git-scan run --unstaged"))
    assert hook_state(hook) == "other"
    try:
        install_hook(tmp_path / "hooks")
        raise AssertionError("must refuse without force")
    except HookError as e:
        assert "not a git-scan script" in str(e)
    try:
        uninstall_hook(tmp_path / "hooks")
        raise AssertionError("must refuse to delete")
    except HookError:
        pass
    assert "--unstaged" in hook.read_text()


def test_foreign_hook_is_refused_unless_forced(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\nother-scanner --check\n")
    r = _run("hook", "install", "--hooks-dir", str(hooks))
    assert r.exit_code == 1 and "not a git-scan script" in r.output
    assert "other-scanner" in (hooks / "pre-commit").read_text()
    r = _run("hook", "install", "--hooks-dir", str(hooks), "--force")
    assert r.exit_code == 0 and "replaced" in r.output
    assert hook_state(hooks / "pre-commit") == "installed"


def test_uninstall_removes_git_scan_hooks_only(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    hooks = tmp_path / "hooks"
    assert _run("hook", "install", "--hooks-dir", str(hooks)).exit_code == 0
    r = _run("hook", "uninstall", "--hooks-dir", str(hooks))
    assert r.exit_code == 0 and "removed" in r.output
    assert not (hooks / "pre-commit").exists()
    assert _global_hooks_path() == str(hooks)          # setting left for other hooks
    r = _run("hook", "uninstall", "--hooks-dir", str(hooks))
    assert r.exit_code == 0 and "no pre-commit hook" in r.output
    (hooks / "pre-commit").write_text("#!/bin/sh\nmine\n")
    r = _run("hook", "uninstall", "--hooks-dir", str(hooks))
    assert r.exit_code == 1 and "not a git-scan script" in r.output
    assert (hooks / "pre-commit").exists()


def test_local_install_writes_the_repository_hook(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    r = _run("hook", "install", "--local")
    assert r.exit_code == 0, r.output
    assert hook_state(repo / ".git" / "hooks" / "pre-commit") == "installed"
    assert "note:" not in r.output
    assert _global_hooks_path() == ""                  # touches no global config
    assert "removed" in uninstall_hook(local=True)


def test_local_install_warns_when_hooks_path_shadows_it(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    subprocess.run(["git", "config", "--global", "core.hooksPath", str(tmp_path / "elsewhere")],
                   check=True)
    r = _run("hook", "install", "--local")
    assert r.exit_code == 0 and "git will ignore this repository's own hooks" in r.output


def test_local_install_outside_a_repository_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = _run("hook", "install", "--local")
    assert r.exit_code == 1 and "not a git repository" in r.output


def test_status_reports_levels_and_what_git_runs(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    hooks = tmp_path / "hooks"
    before = status(hooks)
    assert before["global"]["state"] == "absent" and before["local"]["state"] == "absent"
    assert before["hooks_path"] is None
    assert before["effective"]["path"] == str(repo / ".git" / "hooks" / "pre-commit")

    _run("hook", "install", "--hooks-dir", str(hooks))
    after = status(hooks)
    assert after["global"]["state"] == "installed"
    assert after["hooks_path"] == str(hooks)
    assert after["effective"]["path"] == str(hooks / "pre-commit")
    out = _run("hook", "status", "--hooks-dir", str(hooks)).output
    assert "(installed)" in out and "git runs:" in out and str(hooks) in out


def test_status_outside_a_repository_has_no_local_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    info = status(tmp_path / "hooks")
    assert info["local"] is None and info["effective"] is None
    out = _run("hook", "status", "--hooks-dir", str(tmp_path / "hooks")).output
    assert "local" not in out and "each repository's own .git/hooks" in out
