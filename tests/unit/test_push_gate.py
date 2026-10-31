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


def test_a_project_layer_cannot_lift_the_review_requirement(gate):
    _run("hook", "install", "--pre-push", "--require-review")
    (gate / "git-scan.yaml").write_text("push:\n  require_review: false\n")
    assert load_config(".").review_required is True


def test_judgment_matches_in_a_commit_that_skipped_the_hook_are_cleared_by_ack(gate):
    _run("patterns", "add", "town", "--pattern", "Springfield", "--category", "city",
         "--judgment", "--note", "only beside an address")
    sha = _commit(gate, "notes.txt", "Springfield is a common town name\n", "--no-verify")
    refused = _push(gate)
    assert refused.returncode != 0
    assert f"[commit {sha[:12]}] notes.txt:1 [city] Springfield" in refused.stderr
    assert f"git-scan ack --commit {sha[:12]}" in refused.stderr
    assert marks.marks_on(sha) == set()

    r = _run("ack", "--commit", sha[:12])
    assert r.exit_code == 0, r.output
    assert "1 match(es) acknowledged" in r.output and "marked scanned" in r.output
    assert marks.marks_on(sha) == {"scanned"}
    assert _push(gate).returncode == 0


def test_ack_on_a_commit_does_not_clear_a_real_finding(gate):
    _run("patterns", "add", "town", "--pattern", "Springfield", "--category", "city", "--judgment")
    sha = _commit(gate, "x.txt", "Springfield ghp_" + "f" * 36 + "\n", "--no-verify")
    r = _run("ack", "--commit", sha[:12])
    assert r.exit_code == 1 and "not acknowledged" in r.output and "x.txt:1" in r.output
    assert marks.marks_on(sha) == set()


def test_staged_ack_lets_the_commit_through_the_hook_and_is_consumed(gate):
    _run("patterns", "add", "town", "--pattern", "Springfield", "--category", "city",
         "--judgment", "--note", "only beside an address")
    (gate / "notes.txt").write_text("Springfield is a common town name\n")
    _git(gate, "add", "notes.txt")
    refused = _git(gate, "commit", "-q", "-m", "add notes", check=False)
    assert refused.returncode != 0
    assert "[city] Springfield" in refused.stdout + refused.stderr
    assert "git-scan ack" in refused.stdout + refused.stderr

    r = _run("ack")
    assert r.exit_code == 0 and "acknowledged 1 judgment match(es)" in r.output, r.output
    _git(gate, "commit", "-q", "-m", "add notes")
    sha = _git(gate, "rev-parse", "HEAD").stdout.strip()
    assert marks.marks_on(sha) == {"scanned"}

    (gate / "more.txt").write_text("Springfield again\n")          # the ack was consumed
    _git(gate, "add", "more.txt")
    assert _git(gate, "commit", "-q", "-m", "more", check=False).returncode != 0
    assert _run("ack").exit_code == 0
    assert _git(gate, "commit", "-q", "-m", "more", check=False).returncode == 0


def test_ack_with_nothing_to_acknowledge_says_so(gate):
    r = _run("ack")
    assert r.exit_code == 0 and "no judgment entries" in r.output
    _run("patterns", "add", "town", "--pattern", "Springfield", "--category", "city", "--judgment")
    (gate / "a.txt").write_text("plain\n")
    _git(gate, "add", "a.txt")
    r = _run("ack")
    assert r.exit_code == 0 and "no judgment matches" in r.output


def test_push_check_by_hand_reports_against_the_upstream(gate):
    _commit(gate, "a.txt")
    assert _push(gate).returncode == 0
    _git(gate, "branch", "-q", "--set-upstream-to=origin/main")
    _commit(gate, "b.txt", "ghp_" + "e" * 36 + "\n", "--no-verify")
    r = _run("push-check")
    assert r.exit_code == 1 and "b.txt:1" in r.output and "push refused (1 commit(s)" in r.output


def test_require_review_needs_pre_push(tmp_path):
    r = _run("hook", "install", "--hooks-dir", str(tmp_path / "hooks"), "--require-review")
    assert r.exit_code == 1 and "--require-review needs --pre-push" in r.output


def test_there_is_no_command_that_writes_a_mark(gate):
    assert _run("mark", "scanned").exit_code == 2       # unknown command


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


def test_a_judgment_match_in_the_commit_message_is_refused_until_acknowledged(gate):
    _run("patterns", "add", "town", "--pattern", "Springfield", "--category", "city",
         "--judgment", "--note", "only beside an address")
    (gate / "a.txt").write_text("clean\n")
    _git(gate, "add", "a.txt")
    refused = _git(gate, "commit", "-q", "-m", "Move the office to Springfield", check=False)
    assert refused.returncode != 0
    out = refused.stdout + refused.stderr
    assert "commit message:1 [city] Springfield" in out and "git-scan ack" in out
    assert _run("ack").exit_code == 0            # the staged tree is unchanged, so the ack covers the message too
    assert _git(gate, "commit", "-q", "-m", "Move the office to Springfield", check=False).returncode == 0


def test_the_judgment_check_lists_the_guidance_before_the_matches(gate):
    _run("patterns", "add", "town", "--pattern", "Springfield", "--category", "city", "--judgment")
    (gate / "a.txt").write_text("".join(f"Springfield line {i}\n" for i in range(12)))
    _git(gate, "add", "a.txt")
    r = _run("run")
    assert r.exit_code == 1
    lines = [ln.strip() for ln in r.output.splitlines()]
    first = next(i for i, ln in enumerate(lines) if ln.startswith("Each match below"))
    assert lines[first + 1] == "git-scan ack"      # the command is visible even when matches are cut at ten
    assert "and" in r.output and "more (-v shows all)" in r.output
