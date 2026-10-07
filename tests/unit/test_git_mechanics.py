"""Scanning through real git operations: renames, deletions, binaries,
mixed staged/unstaged edits, and the installed hook blocking a commit."""

import os
import subprocess
import sys
from pathlib import Path

from git_scan.sdk.scanner import run_scan
from git_scan.sdk.sources import collect
from git_scan.sdk.hooks import install_hook

FAKE_PAT = "ghp_" + "b" * 36


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True)


def _repo(tmp_path, files=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for name, text in (files or {"readme.md": "clean\n"}).items():
        (repo / name).write_text(text)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _check(repo, name, **kw):
    return next(c for c in run_scan(str(repo), **kw).checks if c.name == name)


def test_pure_rename_produces_no_content_lines(tmp_path):
    repo = _repo(tmp_path, {"old.txt": f"token = {FAKE_PAT}\n"})
    _git(repo, "mv", "old.txt", "new.txt")
    assert collect(str(repo)).content == []
    assert _check(repo, "Patterns").passed


def test_rename_with_edit_reports_only_the_edit(tmp_path):
    body = "".join(f"line {i}\n" for i in range(1, 11))
    repo = _repo(tmp_path, {"old.txt": body})
    _git(repo, "mv", "old.txt", "new.txt")
    (repo / "new.txt").write_text(body.replace("line 10\n", f"{FAKE_PAT}\n"))
    _git(repo, "add", ".")
    lines = collect(str(repo)).content
    assert [(l.file, l.kind, l.line, l.text) for l in lines] == [
        ("new.txt", "removed", 10, "line 10"), ("new.txt", "added", 10, FAKE_PAT)]


def test_deleted_file_reports_its_removed_lines(tmp_path):
    repo = _repo(tmp_path, {"cfg.txt": f"token = {FAKE_PAT}\n"})
    _git(repo, "rm", "-q", "cfg.txt")
    check = _check(repo, "Patterns")
    assert not check.passed
    assert check.findings[0].startswith("cfg.txt:1 (removed)")


def test_binary_file_is_never_scanned(tmp_path):
    repo = _repo(tmp_path)
    (repo / "blob.bin").write_bytes(os.urandom(4096))
    _git(repo, "add", ".")
    assert collect(str(repo)).content == []
    entropy = _check(repo, "Entropy scan")
    assert entropy.passed and entropy.info == "0 files"


def test_only_the_staged_part_of_a_file_is_scanned(tmp_path):
    repo = _repo(tmp_path, {"a.txt": "one\ntwo\n"})
    (repo / "a.txt").write_text("one\ntwo\nthree\n")
    _git(repo, "add", ".")
    (repo / "a.txt").write_text(f"one\ntwo\nthree\n{FAKE_PAT}\n")   # unstaged
    assert _check(repo, "Patterns").passed
    assert not _check(repo, "Patterns", include_unstaged=True).passed


def test_non_ascii_path_and_content(tmp_path):
    repo = _repo(tmp_path)
    (repo / "café.txt").write_text(f"naïve {FAKE_PAT}\n")
    _git(repo, "add", ".")
    check = _check(repo, "Patterns")
    assert not check.passed and check.findings[0].startswith("café.txt:1")


def test_finding_names_nested_path_and_line(tmp_path):
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "k.py").write_text(f"# header\nK = '{FAKE_PAT}'\n")
    _git(repo, "add", ".")
    assert "src/k.py:2" in _check(repo, "Patterns").findings[0]


def test_installed_hook_blocks_a_commit_and_allows_a_clean_one(tmp_path, monkeypatch):
    """End to end through git: the hook git-scan installs runs git-scan on
    commit. Everything involved lives in the sandbox: the hooks directory,
    the global git config install_hook edits, and the repository."""
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    install_hook(tmp_path / "hooks")
    repo = _repo(tmp_path)
    assert _git(repo, "config", "--get", "core.hooksPath").stdout.strip() == str(tmp_path / "hooks")

    (repo / "secret.txt").write_text(f"{FAKE_PAT}\n")
    _git(repo, "add", ".")
    blocked = _git(repo, "commit", "-q", "-m", "leak", check=False)
    assert blocked.returncode != 0
    assert "[FAIL]" in blocked.stderr and "secret.txt:1" in blocked.stderr   # git sends hook output to stderr
    assert _git(repo, "log", "--oneline").stdout.count("\n") == 1   # still only "init"

    (repo / "secret.txt").write_text("fine\n")
    _git(repo, "add", ".")
    assert _git(repo, "commit", "-q", "-m", "ok").returncode == 0
    assert _git(repo, "log", "--oneline").stdout.count("\n") == 2
