"""Git identity check: real git repos, rules from a real user config."""

import os
import subprocess
from pathlib import Path

import pytest

from git_scan.sdk.steps.git_identity import (
    check_git_identity, normalize_remote, remote_matches, email_matches,
)
from git_scan.sdk.scanner import run_scan

NOREPLY = "12345+octo@users.noreply.github.com"


@pytest.fixture(autouse=True)
def no_env_overrides(monkeypatch):
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(var, raising=False)


def _user_file() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"]) / "git-scan" / "git-scan.yaml"


def rules(*entries):
    """Write identity rules to the sandbox user config; return them as dicts."""
    lines = ["patterns: []", "identity:"]
    out = []
    for remote, *emails in entries:
        lines.append(f"  - remote: {remote}")
        lines.append("    emails: [" + ", ".join(f"'{e}'" for e in emails) + "]")
        out.append({"remote": remote, "emails": list(emails)})
    _user_file().write_text("\n".join(lines) + "\n")
    return out


def make_repo(tmp_path, remote=None, global_email=None, repo_email=None):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if global_email:
        subprocess.run(["git", "config", "--global", "user.email", global_email], check=True)
    if repo_email:
        subprocess.run(["git", "config", "user.email", repo_email], cwd=repo, check=True)
    if remote:
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=repo, check=True)
    return repo


# --- Rule 1: the repo's own setting is trusted ---

def test_repo_setting_trusted_without_any_rule(tmp_path):
    repo = make_repo(tmp_path, "https://github.com/customer/app.git",
                     global_email="work@example.com", repo_email="me@example.org")
    result = check_git_identity(repo)
    assert result.passed and not result.skipped


def test_repo_setting_overridden_by_env_fails(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, "https://github.com/octo/app.git", repo_email=NOREPLY)
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "other@example.com")
    result = check_git_identity(repo)
    assert not result.passed
    assert "differs from the repo's user.email" in result.findings[0]


# --- Rule 2: an inherited email must match a rule for the remote ---

def test_allowed_by_owner_rule(tmp_path):
    r = rules(("github.com/octo", NOREPLY))
    repo = make_repo(tmp_path, "git@github.com:octo/app.git", global_email=NOREPLY)
    result = check_git_identity(repo, r)
    assert result.passed and not result.skipped


def test_wrong_email_for_the_remote_fails(tmp_path):
    r = rules(("github.com/octo", NOREPLY))
    repo = make_repo(tmp_path, "https://github.com/octo/app.git", global_email="work@example.com")
    result = check_git_identity(repo, r)
    assert not result.passed
    assert "not allowed for github.com/octo/app" in result.findings[0]
    assert NOREPLY in result.findings[1]
    assert "git-scan identity allow" in result.findings[2]


def test_email_wildcards(tmp_path):
    r = rules(("git.example.com", "*@example.com"),
              ("github.com/octo", "*+octo@users.noreply.github.com"))
    repo = make_repo(tmp_path, "https://git.example.com/team/app.git", global_email="me@example.com")
    assert check_git_identity(repo, r).passed
    repo2 = make_repo(tmp_path / "b", "https://github.com/octo/app", global_email="999+octo@users.noreply.github.com")
    assert check_git_identity(repo2, r).passed
    repo3 = make_repo(tmp_path / "c", "https://github.com/octo/app", global_email="999+someone@users.noreply.github.com")
    assert not check_git_identity(repo3, r).passed


def test_several_emails_and_several_matching_rules(tmp_path):
    r = rules(("github.com", "a@example.com", "b@example.com"),
              ("github.com/octo/app", "c@example.com"))
    for email in ("a@example.com", "b@example.com", "c@example.com"):
        repo = make_repo(tmp_path / email.split("@")[0], "https://github.com/octo/app.git",
                         global_email=email)
        assert check_git_identity(repo, r).passed, email
    repo = make_repo(tmp_path / "d", "https://github.com/octo/app.git", global_email="d@example.com")
    assert not check_git_identity(repo, r).passed


# --- Rule 3: no rule, skipped with a hint ---

def test_no_matching_rule_is_skipped_with_hint(tmp_path):
    r = rules(("github.com/octo", NOREPLY))
    repo = make_repo(tmp_path, "https://github.com/customer/app.git", global_email="work@example.com")
    result = check_git_identity(repo, r)
    assert result.passed and result.skipped
    assert "no identity rule for github.com/customer/app" in result.info
    assert "git-scan identity allow github.com/<owner> <email>" in result.info


def test_no_rules_at_all_is_skipped(tmp_path):
    repo = make_repo(tmp_path, "https://github.com/octo/app.git", global_email="anyone@example.com")
    result = check_git_identity(repo, [])
    assert result.passed and result.skipped


def test_no_remote_and_local_path_remote_pass(tmp_path):
    assert check_git_identity(make_repo(tmp_path, global_email="x@example.com")).passed
    repo = make_repo(tmp_path / "b", "/srv/git/app.git", global_email="x@example.com")
    result = check_git_identity(repo)
    assert result.passed and "No network remote" in result.info


def test_scanner_reads_rules_from_config(tmp_path):
    rules(("github.com/octo", NOREPLY))
    repo = make_repo(tmp_path, "https://github.com/octo/app.git", global_email="work@example.com")
    report = run_scan(str(repo), only_step="git_identity")
    check = next(c for c in report.checks if c.name == "Git identity")
    assert not check.passed


# --- Helpers ---

@pytest.mark.parametrize("url,expected", [
    ("https://github.com/o/r.git", "github.com/o/r"),
    ("https://deploy@Git.Example.com/o/r", "git.example.com/o/r"),
    ("git@github.com:o/r.git", "github.com/o/r"),
    ("ssh://git@git.example.com:2222/o/r.git", "git.example.com/o/r"),
    ("https://git.example.com/", "git.example.com"),
    ("/srv/git/app.git", None),
    ("file:///srv/git/app.git", None),
])
def test_normalize_remote(url, expected):
    assert normalize_remote(url) == expected


@pytest.mark.parametrize("rule,remote,ok", [
    ("github.com/echo", "github.com/echo/app", True),
    ("github.com/echo", "github.com/echoes/app", False),      # prefix must end at a slash
    ("github.com", "github.com/anyone/app", True),
    ("github.com", "github.com.evil.test/anyone/app", False),
    ("GitHub.com/echo", "github.com/echo/app", True),         # host is case-insensitive
    ("github.com/Echo", "github.com/echo/app", False),        # path is not
    ("github.com/echo/app", "github.com/echo/app", True),
    ("github.com/echo/app", "github.com/echo", False),        # rule longer than remote
    ("github.com/echo/", "github.com/echo/app", True),        # trailing slash tolerated
])
def test_remote_matches(rule, remote, ok):
    assert remote_matches(rule, remote) is ok


@pytest.mark.parametrize("rule,email,ok", [
    ("me@example.com", "me@example.com", True),
    ("me@example.com", "Me@Example.com", True),
    ("*@example.com", "anyone@example.com", True),
    ("*@example.com", "anyone@example.org", False),
    ("*+octo@users.noreply.github.com", "123+octo@users.noreply.github.com", True),
    ("*+octo@users.noreply.github.com", "123+other@users.noreply.github.com", False),
])
def test_email_matches(rule, email, ok):
    assert email_matches(rule, email) is ok
