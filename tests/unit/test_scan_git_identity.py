"""Git identity consistency checks."""

import os
import subprocess
from unittest.mock import patch
from pathlib import Path

import pytest

from git_scan.sdk.scanner import run_scan


@pytest.fixture(autouse=True)
def home_sandbox(tmp_path, monkeypatch):
    """Redirect HOME to isolate global git config."""
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    return fake_home


def _init_repo(path):
    subprocess.run(["git", "init", "-b", "main", str(path)],
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"],
                   cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=path, check=True)


def _commit(path, filename, content, author_email=None):
    (path / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=path, check=True, capture_output=True)
    env = os.environ.copy()
    if author_email:
        env["GIT_AUTHOR_EMAIL"] = author_email
        env["GIT_COMMITTER_EMAIL"] = author_email
    subprocess.run(["git", "commit", "--no-verify", "-m", f"Add {filename}"],
                   cwd=path, env=env, check=True, capture_output=True)


def _run_identity(repo_path):
    report = run_scan(str(repo_path), only_step="git_identity")
    return next(c for c in report.checks if c.name == "Git identity")


# --- No Local Configuration (Historical Consistency Mode) ---

def test_pristine_repo_single_user_passes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _commit(repo, "file1.txt", "content", author_email="user@example.com")
    subprocess.run(["git", "config", "--local", "--unset", "user.email"],
                   cwd=repo, check=True)

    with patch.dict(os.environ, {"GIT_AUTHOR_EMAIL": "user@example.com"}):
        result = _run_identity(repo)
    assert result.passed


def test_pristine_history_but_impending_mismatch_fails(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _commit(repo, "file1.txt", "content", author_email="user@example.com")
    subprocess.run(["git", "config", "--local", "--unset", "user.email"],
                   cwd=repo, check=True)

    with patch.dict(os.environ, {"GIT_AUTHOR_EMAIL": "test@fake.com"}):
        result = _run_identity(repo)
    assert not result.passed
    assert any("History has" in f for f in result.findings)


def test_mixed_history_no_local_config_fails(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _commit(repo, "file1.txt", "c1", author_email="user@example.com")
    _commit(repo, "file2.txt", "c2", author_email="test@example.com")
    subprocess.run(["git", "config", "--local", "--unset", "user.email"],
                   cwd=repo, check=True)

    result = _run_identity(repo)
    assert not result.passed


# --- Local Configuration Present (Explicit Trust Mode) ---

def test_local_config_and_commits_match_passes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(["git", "config", "--local", "user.email", "user@example.com"],
                   cwd=repo, check=True)
    _commit(repo, "file1.txt", "c1", author_email="user@example.com")

    with patch.dict(os.environ, {"GIT_AUTHOR_EMAIL": "user@example.com"}):
        result = _run_identity(repo)
    assert result.passed


def test_local_config_but_unpushed_commits_mismatch_fails(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(["git", "config", "--local", "user.email", "user@example.com"],
                   cwd=repo, check=True)
    _commit(repo, "file1.txt", "c1", author_email="test@fake.com")

    with patch.dict(os.environ, {"GIT_AUTHOR_EMAIL": "user@example.com"}):
        result = _run_identity(repo)
    assert not result.passed
    assert any("Unpushed" in f for f in result.findings)


def test_impending_email_differs_from_local_config_fails(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(["git", "config", "--local", "user.email", "user@example.com"],
                   cwd=repo, check=True)

    with patch.dict(os.environ, {"GIT_AUTHOR_EMAIL": "test@fake.com"}):
        result = _run_identity(repo)
    assert not result.passed
    assert any("differs from local config" in f for f in result.findings)


# --- Edge Cases (No Remote / Upstream) ---

def test_no_upstream_local_config_commits_match_passes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(["git", "config", "--local", "user.email", "user@example.com"],
                   cwd=repo, check=True)
    _commit(repo, "file1.txt", "c1", author_email="user@example.com")

    with patch.dict(os.environ, {"GIT_AUTHOR_EMAIL": "user@example.com"}):
        result = _run_identity(repo)
    assert result.passed


def test_no_upstream_local_config_commits_mismatch_fails(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    subprocess.run(["git", "config", "--local", "user.email", "user@example.com"],
                   cwd=repo, check=True)
    _commit(repo, "file1.txt", "c1", author_email="test@fake.com")

    with patch.dict(os.environ, {"GIT_AUTHOR_EMAIL": "user@example.com"}):
        result = _run_identity(repo)
    assert not result.passed
    assert any("Unpushed" in f for f in result.findings)
