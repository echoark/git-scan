"""git-scan review against the real reviewer.

The sandbox from tests/conftest.py applies: a temp user config, a temp
global git config, no author variables. The preset itself strips the
reviewer's ambient context (no tools, no user settings, no skills), so the
prompt git-scan builds is the whole input. Assertions check signals, not
wording: a clean commit records, planted placeholder leaks are found by
category and location, and nothing is recorded for them."""

import subprocess
import sys

from click.testing import CliRunner

from git_scan.cli.main import cli
from git_scan.sdk import marks
from .conftest import requires_claude, MODEL

TOKEN = "ghp_" + "a" * 36          # a token shape, assembled so the text is not in the source


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True)


def _run(*args):
    return CliRunner().invoke(cli, list(args))


def _repo(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "remote", "add", "origin", str(remote))
    monkeypatch.chdir(r)
    assert _run("config", "review-runner", "claude").exit_code == 0
    assert _run("config", "review-model", MODEL).exit_code == 0
    assert _run("patterns", "add", "me", "--pattern", "Jane Doe", "--category", "name",
                "--ignore-case").exit_code == 0
    assert _run("patterns", "add", "town", "--pattern", "Springfield", "--category", "city",
                "--judgment", "--word-boundary",
                "--note", "a finding only beside an address or residential context").exit_code == 0
    (r / "README.md").write_text("# Tool\n\nA small utility.\n")
    _git(r, "add", "README.md")
    _git(r, "commit", "-q", "-m", "Initial")
    _git(r, "push", "-q", "origin", "main")
    return r


def _commit(repo, name, text, message):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@requires_claude
def test_a_clean_commit_passes_and_is_recorded(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    sha = _commit(repo, "INSTALL.md",
                  "## Install\n\nRun the setup script, then open the config to set the port.\n",
                  "Document install")
    r = _run("review", "--quiet")
    assert r.exit_code == 0, r.output
    assert "clean for 1 commit(s)" in r.output and "recorded" in r.output
    assert "reviewed" in marks.marks_on(sha)


@requires_claude
def test_planted_leaks_are_found_by_category_and_nothing_is_recorded(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    sha = _commit(repo, "contacts.txt",
                  f"contact: Jane Doe, 12 Example Lane, Springfield\ntoken = {TOKEN}\n",
                  "Add contacts; ask Dana about the deploy")
    r = _run("review", "--quiet")
    assert r.exit_code == 1, r.output
    out = r.output
    assert "Privacy review" in out and "finding(s)" in out
    assert "Jane Doe" in out                         # the catalog name, found in content
    assert f"{sha[:12]}:contacts.txt:1" in out       # located to the commit and line
    assert "Dana" in out and f"{sha[:12]}:message" in out   # the person in the commit message
    assert "nothing was recorded" in out
    assert "reviewed" not in marks.marks_on(sha)
