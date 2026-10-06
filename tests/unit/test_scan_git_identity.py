"""Git identity check: real git repos and a real GitHub CLI hosts file in tmp dirs."""

import subprocess

import pytest

from git_scan.sdk.steps.git_identity import (
    check_git_identity,
    domain_matches,
    remote_host,
)

NOREPLY = "12345+octo@users.noreply.github.com"
OTHER_NOREPLY = "999+someone@users.noreply.github.com"


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Isolate HOME (global git config) and the GitHub CLI config dir."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "gh"))
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL", "EMAIL",
                "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return tmp_path


def gh_login(tmp_path, host, *logins):
    d = tmp_path / "gh"
    d.mkdir(exist_ok=True)
    users = "".join(f"            {u}:\n" for u in logins)
    (d / "hosts.yml").write_text(
        f"{host}:\n    git_protocol: https\n    users:\n{users}    user: {logins[0]}\n"
    )


def make_repo(tmp_path, remote=None, global_email=None, repo_email=None):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if global_email:
        subprocess.run(["git", "config", "--global", "user.email", global_email], check=True)
    subprocess.run(["git", "config", "--global", "user.name", "Test"], check=True)
    if repo_email:
        subprocess.run(["git", "config", "user.email", repo_email], cwd=repo, check=True)
    if remote:
        subprocess.run(["git", "remote", "add", "origin", remote], cwd=repo, check=True)
    return repo


# --- Rule 1: the repo's own setting is trusted ---

def test_repo_setting_trusted_anywhere(tmp_path):
    repo = make_repo(tmp_path, "https://github.com/customer/app.git",
                     global_email="work@example.com", repo_email="me@example.org")
    assert check_git_identity(repo).passed


def test_repo_setting_overridden_by_env_fails(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, "https://github.com/octo/app.git", repo_email=NOREPLY)
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "other@example.com")
    result = check_git_identity(repo)
    assert not result.passed
    assert "differs from the repo's user.email" in result.findings[0]


# --- Rule 2: inherited email on github.com ---

def test_github_noreply_of_logged_in_account_passes(tmp_path):
    gh_login(tmp_path, "github.com", "octo")
    repo = make_repo(tmp_path, "https://github.com/octo-org/app.git", global_email=NOREPLY)
    assert check_git_identity(repo).passed


def test_github_noreply_any_of_several_accounts(tmp_path):
    gh_login(tmp_path, "github.com", "customer-acct", "octo")
    repo = make_repo(tmp_path, "https://github.com/octo/app", global_email=NOREPLY)
    assert check_git_identity(repo).passed


def test_github_personal_address_blocked(tmp_path):
    gh_login(tmp_path, "github.com", "octo")
    repo = make_repo(tmp_path, "https://github.com/octo/app.git",
                     global_email="me@example.net")
    result = check_git_identity(repo)
    assert not result.passed
    assert any("git config user.email" in f for f in result.findings)


def test_github_work_address_blocked(tmp_path):
    gh_login(tmp_path, "github.com", "octo")
    repo = make_repo(tmp_path, "https://github.com/octo/app.git",
                     global_email="me@example.com")
    assert not check_git_identity(repo).passed


def test_github_noreply_of_account_not_logged_in_blocked(tmp_path):
    gh_login(tmp_path, "github.com", "octo")
    repo = make_repo(tmp_path, "https://github.com/octo/app.git", global_email=OTHER_NOREPLY)
    result = check_git_identity(repo)
    assert not result.passed
    assert "isn't logged into" in result.findings[0]


def test_github_not_logged_in_blocked(tmp_path):
    repo = make_repo(tmp_path, "https://github.com/octo/app.git", global_email=NOREPLY)
    result = check_git_identity(repo)
    assert not result.passed
    assert any("gh auth login" in f for f in result.findings)


# --- Rule 3: inherited email on other hosts ---

def test_self_hosted_domain_match_passes(tmp_path):
    repo = make_repo(tmp_path, "git@internal-git.example.com:team/app.git",
                     global_email="me@example.com")
    assert check_git_identity(repo).passed


def test_self_hosted_noreply_blocked(tmp_path):
    gh_login(tmp_path, "github.com", "octo")
    repo = make_repo(tmp_path, "https://internal-git.example.com/team/app.git",
                     global_email=NOREPLY)
    result = check_git_identity(repo)
    assert not result.passed
    assert "doesn't match the remote host" in result.findings[0]


def test_enterprise_server_noreply_of_logged_in_account_passes(tmp_path):
    gh_login(tmp_path, "github.example.com", "octo")
    repo = make_repo(tmp_path, "https://github.example.com/team/app.git",
                     global_email="7+octo@users.noreply.github.example.com")
    assert check_git_identity(repo).passed


# --- No remote ---

def test_no_remote_passes(tmp_path):
    repo = make_repo(tmp_path, global_email="anyone@example.com")
    assert check_git_identity(repo).passed


# --- Helpers ---

@pytest.mark.parametrize("url,host", [
    ("https://github.com/o/r.git", "github.com"),
    ("https://user@Git.Example.COM/o/r", "git.example.com"),
    ("git@git.example.com:o/r.git", "git.example.com"),
    ("ssh://git@git.example.org:2222/o/r.git", "git.example.org"),
])
def test_remote_host(url, host):
    assert remote_host(url) == host


@pytest.mark.parametrize("email,host,ok", [
    ("me@example.com", "internal-git.example.com", True),
    ("me@example.com", "example.com", True),
    ("me@example.com.au", "git.example.com.au", True),
    ("me@corp.example.com", "git.example.com", False),
    ("me@example.org", "gitlab.example-tools.org", False),
    ("me@test.com", "git.latest.com", False),
])
def test_domain_matches(email, host, ok):
    assert domain_matches(email, host) is ok


def test_runs_through_the_scanner(tmp_path):
    """The step is discovered and run by run_scan."""
    from git_scan.sdk.scanner import run_scan

    gh_login(tmp_path, "github.com", "octo")
    repo = make_repo(tmp_path, "https://github.com/octo/app.git", global_email=NOREPLY)
    report = run_scan(str(repo), only_step="git_identity")
    check = next(c for c in report.checks if c.name == "Git identity")
    assert check.passed
