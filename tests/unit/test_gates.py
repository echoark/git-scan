"""Gates an agent cannot loosen unilaterally: the notice and confirmation
on every loosening command, the audit records, and the catalog lock."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from git_scan.cli.main import cli
from git_scan.sdk import gates, marks

REPO_ROOT = Path(__file__).resolve().parents[2]
GITHUB_URL = "https://github.com/octo/notes.git"


def _run(*args):
    return CliRunner().invoke(cli, list(args))


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check, capture_output=True, text=True)


def _user_file() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"]) / "git-scan" / "git-scan.yaml"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository on a GitHub remote (a stand-in gh says private), the hooks
    installed in a temp directory, one name entry and one judgment entry."""
    bin_dir = tmp_path / "ghbin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\nprint('private')\n")
    gh.chmod(0o755)
    # the stand-in gh first, then this working tree's git-scan for the hooks
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}"
                               f"{os.environ['PATH']}")
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "remote", "add", "origin", GITHUB_URL)
    monkeypatch.chdir(r)
    assert _run("hook", "install", "--hooks-dir", str(tmp_path / "hooks"), "--pre-push").exit_code == 0
    assert _run("patterns", "add", "me", "--pattern", "Jane Doe", "--category", "name").exit_code == 0
    assert _run("patterns", "add", "town", "--pattern", "Springfield", "--category", "city",
                "--judgment", "--note", "only beside an address").exit_code == 0
    gates.audit_path().unlink(missing_ok=True)       # the fixture's own hook install is recorded
    return r


def _stage(repo, text="Springfield is a town\n"):
    (repo / "notes.md").write_text(text)
    _git(repo, "add", "notes.md")


def _records():
    return gates.audit_entries()


LOOSENING = [
    ("ack",),
    ("relax",),
    ("patterns", "edit", "me", "--category", "surname"),
    ("patterns", "remove", "me"),
    ("patterns", "clear"),
    ("patterns", "exceptions", "add", "me", " Inc"),
    ("emails", "allow", "bot@" + "sample-corp.org"),
    ("config", "require-review", "off"),
    ("config", "review-runner", "echo"),
    ("hook", "uninstall", "--hooks-dir", "HOOKS"),
    ("hook", "install", "--hooks-dir", "HOOKS", "--force"),
]


def _args(args, tmp_path):
    if "--force" in args:            # something to replace
        (tmp_path / "hooks" / "pre-commit").write_text("#!/bin/sh\necho other\n")
    return [str(tmp_path / "hooks") if a == "HOOKS" else a for a in args]


@pytest.mark.parametrize("args", LOOSENING, ids=lambda a: " ".join(a[:2]))
def test_a_loosening_command_is_refused_with_nobody_to_confirm_it(repo, tmp_path, monkeypatch, args):
    monkeypatch.setattr(gates, "confirm_override", None)        # no terminal under pytest
    _stage(repo)
    before = _user_file().read_text()
    r = _run(*_args(args, tmp_path))
    assert r.exit_code == 1, r.output
    assert gates.NOTICE in r.output and "refused" in r.output and "--yes" in r.output
    assert _user_file().read_text() == before
    assert _records() == []
    assert not marks.ack_applies(str(repo))


@pytest.mark.parametrize("args", LOOSENING, ids=lambda a: " ".join(a[:2]))
def test_yes_confirms_and_the_action_is_recorded(repo, tmp_path, monkeypatch, args):
    monkeypatch.setattr(gates, "confirm_override", None)
    _stage(repo)
    r = _run(*_args(args, tmp_path), "--yes")
    assert r.exit_code == 0, r.output
    assert gates.NOTICE in r.output
    entries = _records()
    assert len(entries) == 1
    e = entries[0]
    assert e["who"] == "testuser" and e["confirmed"] == "--yes" and e["repo"] == GITHUB_URL
    assert e["action"].startswith(args[0])
    assert e["time"].endswith("+00:00")


def test_the_person_at_the_terminal_can_say_no(repo, monkeypatch):
    _stage(repo)
    monkeypatch.setattr(gates, "confirm_override", False)
    r = _run("ack")
    assert r.exit_code == 1 and "not confirmed" in r.output
    assert not marks.ack_applies(str(repo)) and _records() == []
    monkeypatch.setattr(gates, "confirm_override", True)
    r = _run("ack")
    assert r.exit_code == 0 and marks.ack_applies(str(repo))
    assert _records()[0]["confirmed"] == "interactive"
    assert "Springfield" in _records()[0]["detail"]


def test_tightening_commands_ask_nothing_and_a_hook_install_is_still_recorded(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(gates, "confirm_override", None)
    assert _run("patterns", "add", "co", "--pattern", "Vendorco").exit_code == 0
    assert _run("patterns", "remove", "github-pat", "--yes").exit_code == 0
    assert _run("patterns", "restore", "github-pat").exit_code == 0
    assert _run("config", "require-review", "on").exit_code == 0
    assert _run("emails", "disallow", "nobody@" + "sample-corp.org").exit_code == 1   # never allowed
    r = _run("hook", "install", "--hooks-dir", str(tmp_path / "hooks2"))
    assert r.exit_code == 0 and gates.NOTICE not in r.output
    actions = [e["action"] for e in _records()]
    assert actions == ["patterns remove", "patterns restore", "config", "hook install"]
    assert all(e["confirmed"] == "-" for e in _records() if e["action"] != "patterns remove")


def test_the_audit_command_lists_who_what_when_and_where(repo):
    _stage(repo)
    assert _run("ack").exit_code == 0
    assert _run("patterns", "exceptions", "add", "me", " Inc").exit_code == 0
    r = _run("audit")
    assert r.exit_code == 0, r.output
    lines = [ln for ln in r.output.splitlines() if ln.strip()]
    assert len(lines) == 2
    assert "testuser" in lines[0] and "ack" in lines[0] and GITHUB_URL in lines[0]
    assert "patterns exceptions add" in lines[1] and "confirmed: interactive" in lines[1]
    assert _run("audit", "--limit", "1").output.count("\n") == 1
    assert str(gates.audit_path()) in _run("config", "path").output


def test_relax_is_confirmed_and_recorded_and_its_removal_recorded(repo, monkeypatch):
    monkeypatch.setattr(gates, "confirm_override", None)
    r = _run("relax")
    assert r.exit_code == 1 and "refused" in r.output
    r = _run("relax", "--yes")
    assert r.exit_code == 0 and "exposure is now relaxed" in r.output, r.output
    r = _run("relax", "--yes")
    assert r.exit_code == 0 and "already eligible" in r.output
    assert _run("relax", "--remove").exit_code == 0                 # tightening: no confirmation
    actions = [(e["action"], e["confirmed"]) for e in _records()]
    assert actions == [("relax", "--yes"), ("relax --remove", "-")]


def test_ack_range_acknowledges_every_commit_in_a_range(repo):
    base = _git(repo, "commit", "-q", "--allow-empty", "-m", "init").returncode == 0
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    shas = []
    for n in ("a", "b"):
        (repo / f"{n}.txt").write_text("Springfield\n")
        _git(repo, "add", f"{n}.txt")
        _git(repo, "commit", "-q", "--no-verify", "-m", f"add {n}")
        shas.append(_git(repo, "rev-parse", "HEAD").stdout.strip())
    r = _run("ack", "--range", f"{base}..HEAD", "--yes")
    assert r.exit_code == 0, r.output
    assert "will not scan them again" in r.output
    for sha in shas:
        assert marks.marks_on(sha, str(repo)) == {"scanned"}
    assert _records()[-1]["action"] == "ack" and "..HEAD" in _records()[-1]["detail"]
    assert _run("ack", "--range", "x..y", "--commit", "z").exit_code == 1


# --- the lock ------------------------------------------------------------------------

def test_lock_works_only_at_a_terminal(repo, monkeypatch):
    monkeypatch.setattr(gates, "confirm_override", None)
    r = _run("lock")
    assert r.exit_code == 1 and "only at a terminal" in r.output and "--yes" in r.output
    assert _run("lock", "--yes").exit_code == 2                      # there is no such option
    assert _run("lock", "--status").output.startswith("unlocked")
    monkeypatch.setattr(gates, "confirm_override", True)             # a person is there
    r = _run("lock")
    assert r.exit_code == 0 and "locked" in r.output
    assert _run("lock", "--status").output.startswith("locked")
    assert gates.lock_path().read_text().strip() == gates.catalog_hash()
    assert [e["action"] for e in _records()] == ["lock"]


def test_a_catalog_changed_behind_the_lock_fails_scans_and_reviews_closed(repo, tmp_path):
    assert _run("lock").exit_code == 0
    _stage(repo, "plain\n")
    assert _run("run", str(repo)).exit_code == 0
    # edited by hand: an entry dropped
    _user_file().write_text("patterns: []\n")
    r = _run("run", str(repo))
    assert r.exit_code == 1 and "Configuration lock" in r.output and "git-scan lock" in r.output
    assert "Patterns" not in r.output and "Entropy" not in r.output    # no check ran
    assert _run("lock", "--status").output.startswith("stale")
    (repo / "a.txt").write_text("x\n")
    _git(repo, "add", "a.txt")
    assert _git(repo, "commit", "-q", "-m", "x", check=False).returncode != 0    # the hook refuses
    r = _run("review")
    assert r.exit_code == 1 and "no review" in r.output and "changed since it was locked" in r.output
    assert _run("lock").exit_code == 0                               # a person looked and re-locked
    assert _run("run", str(repo)).exit_code == 0


def test_a_change_by_git_scan_itself_keeps_the_lock_only_when_a_person_confirmed_it(repo, monkeypatch):
    assert _run("lock").exit_code == 0
    r = _run("patterns", "edit", "me", "--category", "surname")          # interactive stand-in
    assert r.exit_code == 0 and "lock updated" in r.output
    assert _run("lock", "--status").output.startswith("locked")
    monkeypatch.setattr(gates, "confirm_override", None)
    r = _run("patterns", "edit", "me", "--category", "name", "--yes")   # an agent, taking responsibility
    assert r.exit_code == 0 and "lock is now stale" in r.output
    assert _run("lock", "--status").output.startswith("stale")
    _stage(repo, "plain\n")
    assert _run("run", str(repo)).exit_code == 1
    monkeypatch.setattr(gates, "confirm_override", True)
    assert _run("lock").exit_code == 0
    r = _run("lock", "--remove")
    assert r.exit_code == 0 and _run("lock", "--status").output.startswith("unlocked")
    assert [e["action"] for e in _records()] == ["lock", "patterns edit", "patterns edit", "lock", "unlock"]


def test_a_project_layer_write_does_not_touch_the_lock(repo):
    assert _run("lock").exit_code == 0
    assert _run("patterns", "remove", "me", "--project").exit_code == 0
    assert _run("lock", "--status").output.startswith("locked")


# --- the harness and the server ------------------------------------------------------

def test_the_permissions_doc_names_every_loosening_command():
    text = (REPO_ROOT / "docs" / "agent-permissions.md").read_text()
    for cmd in ("git-scan ack", "git-scan relax", "git-scan patterns", "git-scan config",
                "git-scan hook", "git-scan lock", "git-scan emails allow", "--no-verify"):
        assert cmd in text, cmd
    rules = json.loads(text.split("```json\n")[1].split("```")[0])
    asks = rules["permissions"]["ask"]
    assert "Bash(git-scan ack*)" in asks and "Bash(git push *--no-verify*)" in asks


def test_the_gate_workflow_is_reusable_and_takes_the_catalog_as_a_secret():
    data = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "gate.yml").read_text())
    call = (data.get("on") or data.get(True))["workflow_call"]
    assert call["secrets"]["GIT_SCAN_CONFIG"]["required"] is True
    assert set(call["inputs"]) >= {"git-scan-ref", "review", "acknowledge-judgment"}
    steps = " ".join(json.dumps(s) for s in data["jobs"]["gate"]["steps"])
    assert "git-scan run --range" in steps and "git-scan ack --range" in steps and "--yes" in steps
    assert "git-scan review --public --range" in steps
    assert data["permissions"] == {"contents": "read"}
