"""The push gate end to end: real repositories, a real bare remote, and the
hooks git-scan installs, run by git itself on commit and push."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from git_scan.cli.main import cli
from git_scan.sdk import marks
from git_scan.sdk.config import load_config
from git_scan.sdk.hooks import install_hook, hook_state, status, uninstall_hook


def _git(repo, *args, check=True, input=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True, input=input)


def _run(*args, input=None):
    return CliRunner().invoke(cli, list(args), input=input)


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """A repository with an empty bare remote and the push gate installed.

    The hook scripts prepend $HOME/.local/bin, so HOME points at the sandbox
    and this venv's git-scan is the one the hooks run.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    install_hook(tmp_path / "hooks", pre_push=True)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", str(remote))
    monkeypatch.chdir(repo)
    return repo


def _commit(repo, name, text="clean\n", *extra):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"add {name}", *extra)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _push(repo):
    return _git(repo, "push", "-q", "origin", "main", check=False)


def _states(hooks, repo):
    return {r["hook"]: r["state"] for r in status(hooks, repo_path=str(repo))["hooks"]}


def test_install_writes_the_gate_hooks_beside_pre_commit(gate, tmp_path):
    hooks = tmp_path / "hooks"
    for name in ("pre-commit", "commit-msg", "pre-push", "post-commit"):
        assert hook_state(hooks / name) == "installed", name
    assert _states(hooks, gate) == {"pre-commit": "installed", "commit-msg": "installed",
                                    "post-commit": "installed", "pre-push": "installed"}
    assert "removed" in uninstall_hook(hooks)
    assert not any((hooks / n).exists()
                   for n in ("pre-commit", "commit-msg", "pre-push", "post-commit"))


def test_without_pre_push_nothing_changes_for_push(tmp_path, monkeypatch):
    hooks = tmp_path / "hooks"
    install_hook(hooks)
    assert not (hooks / "pre-push").exists() and not (hooks / "post-commit").exists()
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    rows = {r["hook"]: r for r in status(hooks, repo_path=str(repo))["hooks"]}
    assert rows["pre-push"]["state"] == "absent"
    assert "pushes are not scanned" in rows["pre-push"]["protects"]


def test_a_foreign_pre_push_hook_blocks_install_until_forced(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-push").write_text("#!/bin/sh\nother-tool\n")
    r = _run("hook", "install", "--hooks-dir", str(hooks), "--pre-push")
    assert r.exit_code == 1 and "not a git-scan script" in r.output
    assert not (hooks / "pre-commit").exists()            # nothing written
    r = _run("hook", "install", "--hooks-dir", str(hooks), "--pre-push", "--force")
    assert r.exit_code == 0 and hook_state(hooks / "pre-push") == "installed"


def test_a_commit_through_the_hook_is_marked_and_pushes(gate):
    sha = _commit(gate, "a.txt")
    assert marks.marks_on(sha) == {"scanned"}
    assert _push(gate).returncode == 0


def test_a_clean_commit_that_skipped_the_hook_is_scanned_at_push_and_marked(gate):
    sha = _commit(gate, "a.txt", "clean\n", "--no-verify")
    assert marks.marks_on(sha) == set()
    pushed = _push(gate)
    assert pushed.returncode == 0, pushed.stderr
    assert "1 scanned now, 0 already scanned" in pushed.stdout + pushed.stderr
    assert marks.marks_on(sha) == {"scanned"}


def test_a_commit_that_skipped_the_hook_with_a_finding_is_refused(gate):
    sha = _commit(gate, "token.txt", "ghp_" + "c" * 36 + "\n", "--no-verify")
    refused = _push(gate)
    assert refused.returncode != 0
    assert f"[commit {sha[:12]}] token.txt:1" in refused.stderr
    assert "push refused" in refused.stderr
    assert marks.marks_on(sha) == set()


def test_scanning_a_commit_with_a_finding_does_not_mark_it(gate):
    sha = _commit(gate, "token.txt", "ghp_" + "c" * 36 + "\n", "--no-verify")
    r = _run("run", "--commit", sha)
    assert r.exit_code == 1 and f"[commit {sha[:12]}] token.txt:1" in r.output
    assert marks.marks_on(sha) == set()


def test_a_marked_commit_is_not_scanned_again(gate):
    sha = _commit(gate, "a.txt")
    assert marks.marks_on(sha) == {"scanned"}
    pushed = _push(gate)
    assert pushed.returncode == 0
    assert "0 scanned now, 1 already scanned" in pushed.stdout + pushed.stderr


def test_an_amended_commit_is_scanned_again_at_push(gate):
    _commit(gate, "a.txt")
    (gate / "a.txt").write_text("ghp_" + "d" * 36 + "\n")
    _git(gate, "commit", "-q", "-a", "--amend", "--no-edit", "--no-verify")
    assert marks.marks_on("HEAD") == set()
    refused = _push(gate)
    assert refused.returncode != 0 and "a.txt:1" in refused.stderr


def test_a_required_mark_is_refused_until_marked_and_covers_the_batch(gate):
    assert _run("hook", "install", "--pre-push", "--require-mark", "review").exit_code == 0
    assert load_config(".").required_marks == ["review"]
    first = _commit(gate, "a.txt")
    _commit(gate, "b.txt")
    refused = _push(gate)
    assert refused.returncode != 0
    assert "review" in refused.stderr and first[:12] in refused.stderr
    assert "git-scan mark review" in refused.stderr

    r = _run("mark", "review")                              # one mark on the tip
    assert r.exit_code == 0 and "marked" in r.output
    assert _push(gate).returncode == 0


def test_only_unpushed_commits_are_checked(gate):
    _run("hook", "install", "--pre-push", "--require-mark", "review")
    _commit(gate, "a.txt")
    _run("mark", "review")
    assert _push(gate).returncode == 0
    newer = _commit(gate, "b.txt")
    refused = _push(gate)
    assert refused.returncode != 0 and newer[:12] in refused.stderr
    assert "1 commit(s) to push" in refused.stderr
    assert "1 commit(s) missing a required mark" in refused.stderr
    _run("mark", "review")
    assert _push(gate).returncode == 0


def test_a_project_layer_cannot_drop_a_required_mark(gate):
    _run("hook", "install", "--pre-push", "--require-mark", "review")
    (gate / "git-scan.yaml").write_text("push:\n  require_marks: []\n")
    assert load_config(".").required_marks == ["review"]


def test_push_check_by_hand_reports_against_the_upstream(gate):
    _commit(gate, "a.txt")
    assert _push(gate).returncode == 0
    _git(gate, "branch", "-q", "--set-upstream-to=origin/main")
    _commit(gate, "b.txt", "ghp_" + "e" * 36 + "\n", "--no-verify")
    r = _run("push-check")
    assert r.exit_code == 1 and "b.txt:1" in r.output and "push refused (1 commit(s)" in r.output


def test_mark_rejects_a_bad_label(gate):
    _commit(gate, "a.txt")
    r = _run("mark", "no spaces")
    assert r.exit_code == 1 and "not a valid mark label" in r.output


def test_require_mark_needs_pre_push(tmp_path):
    r = _run("hook", "install", "--hooks-dir", str(tmp_path / "hooks"), "--require-mark", "x")
    assert r.exit_code == 1 and "--require-mark needs --pre-push" in r.output


def test_deleting_a_remote_branch_is_not_checked(gate):
    _commit(gate, "a.txt")
    assert _push(gate).returncode == 0
    _git(gate, "push", "-q", "origin", "main:side")
    assert _git(gate, "push", "-q", "origin", "--delete", "side", check=False).returncode == 0


def test_install_records_missing_config_help_and_a_real_commit_shows_it(gate, tmp_path):
    r = _run("hook", "install", "--hooks-dir", str(tmp_path / "hooks"), "--pre-push",
             "--missing-config-help", "Commits go through the review tool, which also sets this up.")
    assert r.exit_code == 0 and "missing-config help:" in r.output
    (Path(os.environ["XDG_CONFIG_HOME"]) / "git-scan" / "git-scan.yaml").unlink()
    (gate / "a.txt").write_text("clean\n")
    _git(gate, "add", "a.txt")
    blocked = _git(gate, "commit", "-q", "-m", "x", check=False)
    assert blocked.returncode != 0
    assert "Commit refused" in blocked.stderr
    assert "Commits go through the review tool, which also sets this up." in blocked.stderr
    assert "patterns clear" not in blocked.stderr and "[PASS]" not in blocked.stderr
