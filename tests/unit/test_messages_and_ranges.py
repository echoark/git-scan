"""Commit messages and commit ranges as scan inputs: the commit-msg hook,
`run --message`, `run --range`, and the push gate scanning what it sends.

Real repositories, a real bare remote, and the hooks git-scan installs, run
by git itself on commit and push."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from git_scan.cli.main import cli
from git_scan.sdk import marks
from git_scan.sdk.hooks import install_hook
from git_scan.sdk.scanner import run_scan

FAKE_PAT = "ghp_" + "b" * 36
FULL_SHA = "a1" * 20          # 40 hex characters, shaped like a commit id


def _git(repo, *args, check=True, input=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True, input=input)


def _run(*args, input=None):
    return CliRunner().invoke(cli, list(args), input=input)


def _user_config(text):
    (Path(os.environ["XDG_CONFIG_HOME"]) / "git-scan" / "git-scan.yaml").write_text(text)


def _repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


def _commit(repo, name, text="clean\n", message=None, *extra):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message or f"add {name}", *extra)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def hooked(tmp_path, monkeypatch):
    """A repository with an empty bare remote and every git-scan hook.

    The hook scripts prepend $HOME/.local/bin, so HOME points at the sandbox
    and this venv's git-scan is the one the hooks run.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    install_hook(tmp_path / "hooks", pre_push=True)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = _repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    monkeypatch.chdir(repo)
    return repo


def _push(repo):
    return _git(repo, "push", "-q", "origin", "main", check=False)


# --- commit messages ---------------------------------------------------------

def test_a_full_sha_in_a_message_is_refused_and_the_message_is_kept(hooked):
    (hooked / "a.txt").write_text("clean\n")
    _git(hooked, "add", "a.txt")
    refused = _git(hooked, "commit", "-q", "-m", f"Revert {FULL_SHA}", check=False)
    assert refused.returncode != 0
    assert "commit message refused" in refused.stderr
    assert f"commit message:1 [identifier] {FULL_SHA}" in refused.stderr
    assert "git commit -e -F .git/COMMIT_EDITMSG" in refused.stderr
    assert "short SHA" in refused.stderr
    assert _git(hooked, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode != 0
    kept = hooked / ".git" / "COMMIT_EDITMSG"
    assert FULL_SHA in kept.read_text()

    kept.write_text(f"Revert {FULL_SHA[:12]}\n")
    retried = _git(hooked, "commit", "-q", "-F", ".git/COMMIT_EDITMSG", check=False)
    assert retried.returncode == 0, retried.stderr
    assert marks.marks_on("HEAD") == {"scanned"}


def test_a_personal_pattern_in_a_message_is_refused(tmp_path, monkeypatch):
    _user_config("patterns:\n  - id: cust\n    pattern: Vendorco\n    category: customer\n")
    monkeypatch.chdir(_repo(tmp_path))
    draft = tmp_path / "draft.txt"
    draft.write_text("Fix the export\n\nAs asked by Vendorco.\n")
    r = _run("run", "--message", str(draft))
    assert r.exit_code == 1
    assert "commit message:3 [customer] Vendorco" in r.output
    assert "commit message refused" in r.output
    assert "COMMIT_EDITMSG" not in r.output          # a draft, not git's file


def test_a_clean_message_passes_from_standard_input(tmp_path, monkeypatch):
    monkeypatch.chdir(_repo(tmp_path))
    r = _run("run", "--message", "-", input="Fix the export\n\nCloses the gap in 1b08aa6.\n")
    assert r.exit_code == 0, r.output
    assert "commit message clean" in r.output


def test_comment_lines_and_the_verbose_diff_are_not_part_of_the_message(tmp_path, monkeypatch):
    _user_config("patterns:\n  - id: cust\n    pattern: Vendorco\n")
    monkeypatch.chdir(_repo(tmp_path))
    text = ("Fix the export\n"
            "# On branch vendorco-work: Vendorco\n"
            "# ------------------------ >8 ------------------------\n"
            f"+token = {FAKE_PAT}\n")
    r = _run("run", "--message", "-", input=text)
    assert r.exit_code == 0, r.output


def test_a_refused_message_leaves_nothing_for_a_no_verify_retry_to_inherit(hooked):
    (hooked / "a.txt").write_text("clean\n")
    _git(hooked, "add", "a.txt")
    assert _git(hooked, "commit", "-q", "-m", f"see {FULL_SHA}", check=False).returncode != 0
    _git(hooked, "commit", "-q", "--no-verify", "-m", f"see {FULL_SHA}")
    sha = _git(hooked, "rev-parse", "HEAD").stdout.strip()
    assert marks.marks_on(sha) == set()               # the pre-commit pass was dropped
    refused = _push(hooked)
    assert refused.returncode != 0
    assert f"[commit {sha[:12]}] commit message:1" in refused.stderr


# --- ranges ------------------------------------------------------------------

def test_a_range_finds_a_secret_added_then_deleted(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    base = _commit(repo, "readme.md")
    added = _commit(repo, "cfg.txt", f"token = {FAKE_PAT}\n")
    deleted = _commit(repo, "cfg.txt", "token = from the environment\n")
    later = _commit(repo, "notes.txt")
    assert _git(repo, "diff", base, deleted, "--", "cfg.txt").stdout.count(FAKE_PAT) == 0

    r = _run("run", "--range", f"{base}..{later}")
    assert r.exit_code == 1
    assert f"[commit {added[:12]}] cfg.txt:1 [token]" in r.output
    assert f"[commit {deleted[:12]}] cfg.txt:1 (removed) [token]" in r.output
    assert "3 commit(s) read; 1 clean and marked 'scanned'" in r.output
    assert marks.marks_on(later) == {"scanned"}
    assert marks.marks_on(added) == set() and marks.marks_on(deleted) == set()


def test_a_clean_range_marks_every_commit_in_one_pass(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    shas = [_commit(repo, f"f{i}.txt") for i in range(4)]
    report = run_scan(".", rev_range=f"{shas[0]}..HEAD")
    assert not report.failed
    assert set(report.commits) == set(shas[1:]) == set(report.marked)
    assert all(marks.marks_on(s) == {"scanned"} for s in shas[1:])
    assert marks.marks_on(shas[0]) == set()


def test_an_author_outside_the_identity_rules_is_refused(tmp_path, monkeypatch):
    _user_config("patterns: []\nidentity:\n"
                 "  - remote: github.com/octo\n    emails: ['test@example.com']\n")
    repo = _repo(tmp_path)
    _git(repo, "remote", "add", "origin", "https://github.com/octo/app.git")
    monkeypatch.chdir(repo)
    base = _commit(repo, "readme.md")
    outsider = "someone@" + "corp.invalid"
    bad = _commit(repo, "a.txt", "clean\n", None, f"--author=Someone <{outsider}>")
    good = _commit(repo, "b.txt")
    r = _run("run", "--range", f"{base}..HEAD")
    assert r.exit_code == 1
    assert f"[commit {bad[:12]}] author {outsider} is not allowed for github.com/octo/app" \
        in r.output
    assert "committer" not in r.output.split("Allowed by")[0]   # the committer is allowed
    assert "[FAIL] Email addresses" not in r.output              # identities aren't content
    assert marks.marks_on(good) == {"scanned"} and marks.marks_on(bad) == set()


def test_a_hand_edited_merge_resolution_is_scanned_as_the_merge(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    base = _commit(repo, "a.txt", "base\n")
    _git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "a.txt", "side\n")
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "a.txt", "main\n")
    assert _git(repo, "merge", "-q", "side", check=False).returncode != 0   # conflict
    (repo / "a.txt").write_text(f"main and side\ntoken = {FAKE_PAT}\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "--no-edit")
    merge = _git(repo, "rev-parse", "HEAD").stdout.strip()

    report = run_scan(".", rev_range=f"{base}..HEAD")
    patterns = next(c for c in report.checks if c.name == "Patterns")
    assert not patterns.passed
    assert [f for f in patterns.findings if FAKE_PAT[:8] in f] == [
        f"[commit {merge[:12]}] a.txt:2 [token] {FAKE_PAT}"]
    assert merge not in report.marked and len(report.marked) == 2


def test_a_bad_range_fails_and_combining_inputs_is_a_usage_error(tmp_path, monkeypatch):
    monkeypatch.chdir(_repo(tmp_path))
    r = _run("run", "--range", "nope..HEAD")
    assert r.exit_code == 1 and "[FAIL] Range" in r.output
    r = _run("run", "--range", "HEAD~1..HEAD", "--message", "-", input="x\n")
    assert r.exit_code == 2 and "choose one of" in r.output
    r = _run("run", "--range", "HEAD~1..HEAD", "--unstaged")
    assert r.exit_code == 2


# --- the push gate scans -----------------------------------------------------

def test_a_push_after_a_history_rewrite_passes_without_per_commit_marks(hooked):
    for i in range(3):
        _commit(hooked, f"f{i}.txt")
    assert all(marks.marks_on(s) == {"scanned"} for s in
               _git(hooked, "rev-list", "HEAD").stdout.split())
    _git(hooked, "rebase", "-q", "--force-rebase", "--root")
    rewritten = _git(hooked, "rev-list", "HEAD").stdout.split()
    assert all(marks.marks_on(s) == set() for s in rewritten)

    pushed = _push(hooked)
    assert pushed.returncode == 0, pushed.stderr
    assert "3 commit(s) to push: 3 scanned now, 0 already scanned" in pushed.stdout
    assert all(marks.marks_on(s) == {"scanned"} for s in rewritten)


def test_a_refused_push_marks_only_the_clean_commits(hooked):
    first = _commit(hooked, "a.txt", "clean\n", None, "--no-verify")
    second = _commit(hooked, "b.txt", "clean\n", None, "--no-verify")
    dirty = _commit(hooked, "c.txt", f"{FAKE_PAT}\n", None, "--no-verify")
    last = _commit(hooked, "d.txt", "clean\n", None, "--no-verify")
    refused = _push(hooked)
    assert refused.returncode != 0 and f"[commit {dirty[:12]}] c.txt:1" in refused.stderr
    assert {s for s in (first, second, dirty, last) if marks.marks_on(s)} == {first, second, last}
