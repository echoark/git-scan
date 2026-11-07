"""Entry policy per exposure, on the scan side: the clone's exposure decides
which entries are findings, judgment calls or allowed, and the commands
that set a policy."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from git_scan.cli.main import cli
from git_scan.sdk.config import entry_policy, load_config, ConfigError
from git_scan.sdk.scanner import run_scan

GH_STUB = 'import os, sys\nprint(os.environ.get("STUB_GH", "private"))\n'
GITHUB_URL = "https://github.com/octo/notes.git"


def _run(*args):
    return CliRunner().invoke(cli, list(args))


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository on a GitHub remote, with a stand-in gh that reports it
    private, so the clone can be made eligible for a relaxed review."""
    bin_dir = tmp_path / "ghbin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n" + GH_STUB)
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "remote", "add", "origin", GITHUB_URL)
    monkeypatch.chdir(r)
    assert _run("patterns", "add", "me", "--pattern", "Jane Doe", "--category", "name").exit_code == 0
    assert _run("patterns", "add", "town", "--pattern", "Springfield", "--category", "city",
                "--judgment", "--note", "only beside an address").exit_code == 0
    assert _run("patterns", "add", "acct", "--pattern", "ACCT-[0-9]{6}", "--category", "account").exit_code == 0
    return r


def _checks(repo, text):
    (repo / "notes.md").write_text(text)
    _git(repo, "add", "notes.md")
    report = run_scan(str(repo))
    return report, {c.name: c for c in report.checks}


def _user_file() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"]) / "git-scan" / "git-scan.yaml"


def test_category_defaults():
    assert entry_policy({"category": "name"}, "public") == "finding"
    assert entry_policy({"category": "name"}, "relaxed") == "allow"
    assert entry_policy({"category": "city", "judgment": True}, "public") == "judgment"
    assert entry_policy({"category": "city", "judgment": True}, "relaxed") == "allow"
    assert entry_policy({"category": "last4", "judgment": True}, "relaxed") == "judgment"
    for cat in ("token", "key", "email", "account", "financial", "env", "made_up", ""):
        assert entry_policy({"category": cat}, "public") == "finding"
        assert entry_policy({"category": cat}, "relaxed") == "finding"
    assert entry_policy({"category": "name", "relaxed": "finding"}, "relaxed") == "finding"
    assert entry_policy({"category": "token", "public": "allow"}, "public") == "allow"
    assert entry_policy({"category": "token", "relaxed": "judgment"}, "relaxed") == "judgment"


def test_the_scan_follows_the_clones_exposure(repo):
    text = "Jane Doe lives in Springfield; account ACCT-123456\n"
    report, checks = _checks(repo, text)
    assert report.exposure == "public"
    assert not checks["Patterns"].passed and "[name] Jane Doe" in checks["Patterns"].findings[0]
    assert not checks["Judgment"].passed and "[city] Springfield" in "\n".join(checks["Judgment"].findings)

    assert _run("relax").exit_code == 0
    report, checks = _checks(repo, text)
    assert report.exposure == "relaxed"
    text_found = "\n".join(checks["Patterns"].findings)
    assert "Jane Doe" not in text_found                       # a name: allowed in a relaxed clone
    assert "[account] ACCT-123456" in text_found              # an account id: a finding in both
    allowed = load_config(str(repo)).counts_for("relaxed")["allowed"]
    assert allowed >= 2 and f"relaxed clone, {allowed} allowed" in checks["Patterns"].info
    # a city is allowed in a relaxed clone: not listed (here no judgment entry is left at all)
    assert "Springfield" not in "\n".join(checks.get("Judgment", checks["Patterns"]).findings)
    r = _run("run", str(repo))
    assert "exposure: relaxed" in r.output

    assert _run("relax", "--remove").exit_code == 0
    report, checks = _checks(repo, text)
    assert report.exposure == "public" and "Jane Doe" in "\n".join(checks["Patterns"].findings)


def test_an_explicit_policy_overrides_the_category_default(repo):
    assert _run("patterns", "edit", "me", "--relaxed", "finding").exit_code == 0
    assert _run("patterns", "edit", "acct", "--relaxed", "judgment").exit_code == 0
    assert _run("relax").exit_code == 0
    _, checks = _checks(repo, "Jane Doe; ACCT-123456\n")
    assert "[name] Jane Doe" in "\n".join(checks["Patterns"].findings)
    assert "ACCT-123456" not in "\n".join(checks["Patterns"].findings)
    assert "[account] ACCT-123456" in "\n".join(checks["Judgment"].findings)
    out = _run("patterns", "list").output
    assert "(relaxed: finding)" in out and "(relaxed: judgment)" in out
    assert _run("patterns", "edit", "me", "--relaxed", "default").exit_code == 0
    assert "relaxed" not in next(e for e in yaml.safe_load(_user_file().read_text())["patterns"]
                                 if e["id"] == "me")


def test_public_prospective_keeps_an_eligible_clone_public(repo):
    assert _run("relax").exit_code == 0
    assert _run("config", "public-prospective", "on", "--project").exit_code == 0
    report, checks = _checks(repo, "Jane Doe\n")
    assert report.exposure == "public" and not checks["Patterns"].passed


def test_a_derived_name_is_allowed_in_a_relaxed_clone_but_the_email_is_not(repo, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Pat Example")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "pat@example.com")
    assert _run("relax").exit_code == 0
    _, checks = _checks(repo, "by Pat Example <pat@example.com>\n")
    found = "\n".join(checks["Patterns"].findings)
    assert "[email] pat@example.com" in found and "Pat Example" not in found


def test_policy_values_are_validated(repo):
    r = _run("patterns", "add", "x", "--pattern", "x", "--public", "maybe")
    assert r.exit_code == 2                                   # click refuses the choice
    _user_file().write_text("patterns:\n  - id: x\n    pattern: x\n    relaxed: maybe\n")
    with pytest.raises(ConfigError) as e:
        load_config(str(repo))
    assert "'relaxed' must be one of finding, judgment, allow" in str(e.value)


def test_the_mcp_result_reports_the_exposure(repo):
    import asyncio
    from git_scan.mcp.server import scan_repo
    (repo / "a.txt").write_text("plain\n")
    _git(repo, "add", "a.txt")
    assert asyncio.run(scan_repo(str(repo)))["exposure"] == "public"
    assert _run("relax").exit_code == 0
    assert asyncio.run(scan_repo(str(repo)))["exposure"] == "relaxed"
