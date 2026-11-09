"""What a match does, on the scan side: each entry's ``match`` (block,
judgment, warn, ignore) decides, the scan never reads a clone's relaxed
eligibility, the commands that set it, and ``config migrate``."""

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from git_scan.cli.main import cli
from git_scan.sdk.config import entry_match, load_config, ConfigError
from git_scan.sdk.scanner import run_scan
from git_scan.sdk.user_patterns import export_patterns

GH_STUB = 'import os, sys\nprint(os.environ.get("STUB_GH", "private"))\n'
GITHUB_URL = "https://github.com/octo/notes.git"


def _run(*args, input=None):
    return CliRunner().invoke(cli, list(args), input=input)


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
                "--match", "judgment", "--specifics", "the town the owner lives in").exit_code == 0
    assert _run("patterns", "add", "nick", "--pattern", "jdoe42", "--category", "handle",
                "--match", "warn").exit_code == 0
    assert _run("patterns", "add", "acct", "--pattern", "ACCT-[0-9]{6}", "--category", "account").exit_code == 0
    return r


def _checks(repo, text):
    (repo / "notes.md").write_text(text)
    _git(repo, "add", "notes.md")
    report = run_scan(str(repo))
    return report, {c.name: c for c in report.checks}


def _user_file() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"]) / "git-scan" / "git-scan.yaml"


def test_a_match_is_block_unless_the_entry_says_and_a_relaxed_review_falls_back_to_match():
    assert entry_match({"category": "name"}) == "block"
    assert entry_match({"match": "judgment"}) == "judgment"
    assert entry_match({"match": "warn"}) == "warn"
    assert entry_match({"match": "block", "relaxed": "warn"}) == "block"
    assert entry_match({"match": "block", "relaxed": "warn"}, relaxed=True) == "warn"
    assert entry_match({"match": "judgment"}, relaxed=True) == "judgment"       # absent: as match
    assert entry_match({"relaxed": "ignore"}, relaxed=True) == "ignore"


def test_the_scan_applies_each_entrys_match(repo):
    report, checks = _checks(repo, "Jane Doe lives in Springfield as jdoe42; account ACCT-123456\n")
    assert not checks["Patterns"].passed
    found = "\n".join(checks["Patterns"].findings)
    assert "[name] Jane Doe" in found and "[account] ACCT-123456" in found
    assert "Springfield" not in found and "jdoe42" not in found
    assert not checks["Judgment"].passed
    assert "[city] Springfield" in "\n".join(checks["Judgment"].findings)
    assert "town: the town the owner lives in" in "\n".join(checks["Judgment"].findings)
    assert checks["Warnings"].passed                                 # a warn match never fails
    assert "notes.md:1 [handle] jdoe42" in checks["Warnings"].findings
    assert "1 match(es), reported and passed" in checks["Warnings"].info
    r = _run("run", str(repo))
    assert "[WARN] Warnings" in r.output and "[handle] jdoe42" in r.output


def test_a_warn_only_change_passes_and_is_shown(repo):
    _, checks = _checks(repo, "ping jdoe42\n")
    assert checks["Patterns"].passed and checks["Judgment"].passed and checks["Warnings"].passed
    r = _run("run", str(repo))
    assert r.exit_code == 0 and "[WARN] Warnings" in r.output and "jdoe42" in r.output
    _, checks = _checks(repo, "plain\n")
    assert checks["Warnings"].passed and checks["Warnings"].findings == []
    r = _run("run", str(repo))
    assert "[PASS] Warnings" in r.output and "[WARN]" not in r.output


def test_the_scan_never_reads_the_clones_relaxed_eligibility(repo):
    """Eligibility is for 'review --relaxed' alone: the scan applies 'match'
    whatever the clone's state, and 'relaxed' is never applied to a commit."""
    assert _run("patterns", "edit", "me", "--relaxed", "ignore").exit_code == 0
    assert _run("relax").exit_code == 0
    report, checks = _checks(repo, "Jane Doe\n")
    assert not checks["Patterns"].passed and "[name] Jane Doe" in checks["Patterns"].findings[0]
    assert not hasattr(report, "exposure")
    r = _run("run", str(repo))
    assert "exposure" not in r.output
    assert _run("patterns", "edit", "me", "--relaxed", "default").exit_code == 0
    assert "relaxed" not in next(e for e in yaml.safe_load(_user_file().read_text())["patterns"]
                                 if e["id"] == "me")


def test_an_entry_with_specifics_only_is_for_the_review_alone(repo):
    r = _run("patterns", "add", "street", "--specifics", "the owner's street address", "--category", "address")
    assert r.exit_code == 0, r.output
    assert {"id": "street", "category": "address", "specifics": "the owner's street address"} \
        in yaml.safe_load(_user_file().read_text())["patterns"]
    _, checks = _checks(repo, "12 Example Lane\n")
    assert checks["Patterns"].passed                                 # nothing to compile
    out = _run("patterns", "list").output
    assert "street" in out and "(specifics only) the owner's street address" in out
    exported = {e["id"]: e for e in export_patterns(str(repo))["patterns"]}
    assert exported["street"] == {"id": "street", "match": "block", "category": "address",
                                  "specifics": "the owner's street address"}
    r = _run("patterns", "add", "empty", "--category", "x")
    assert r.exit_code == 1 and "needs a --pattern" in r.output


def test_match_values_are_validated(repo):
    assert _run("patterns", "add", "x", "--pattern", "x", "--match", "maybe").exit_code == 2
    assert _run("patterns", "add", "x", "--pattern", "x", "--relaxed", "maybe").exit_code == 2
    _user_file().write_text("patterns:\n  - id: x\n    pattern: x\n    relaxed: maybe\n")
    with pytest.raises(ConfigError) as e:
        load_config(str(repo))
    assert "'relaxed' must be one of block, judgment, warn, ignore" in str(e.value)
    _user_file().write_text("patterns:\n  - id: x\n    pattern: x\n    judgment: true\n")
    r = _run("run", str(repo))
    assert r.exit_code == 1 and "git-scan config migrate" in r.output


def test_remove_writes_match_ignore_and_restore_drops_it(repo):
    assert _run("patterns", "remove", "hex-40").exit_code == 0
    assert {"id": "hex-40", "match": "ignore"} in yaml.safe_load(_user_file().read_text())["patterns"]
    assert _checks(repo, "a" * 40 + "\n")[1]["Patterns"].passed
    assert _run("patterns", "restore", "hex-40").exit_code == 0
    assert not any(e["id"] == "hex-40" for e in yaml.safe_load(_user_file().read_text())["patterns"])
    assert not _checks(repo, "a" * 40 + "\n")[1]["Patterns"].passed
    # an edited built-in that is then turned off keeps its other overrides on restore
    assert _run("patterns", "edit", "hex-40", "--exclude-file", "docs/*").exit_code == 0
    assert _run("patterns", "remove", "hex-40").exit_code == 0
    assert _run("patterns", "restore", "hex-40").exit_code == 0
    assert {"id": "hex-40", "exclude_files": ["docs/*"]} in yaml.safe_load(_user_file().read_text())["patterns"]


def test_the_mcp_result_has_no_exposure_field(repo):
    from git_scan.mcp.server import scan_repo
    (repo / "a.txt").write_text("plain\n")
    _git(repo, "add", "a.txt")
    result = asyncio.run(scan_repo(str(repo)))
    assert result["passed"] and "exposure" not in result


# --- the built-in entries ------------------------------------------------------------

def test_the_built_in_entries_say_what_a_match_does(repo):
    config = load_config(str(repo))
    by_id = {p["id"]: p for p in config.patterns if config.pattern_sources[p["id"]] == "built-in"}
    assert all("match" not in by_id[i] and "relaxed" not in by_id[i] for i in ("github-pat", "hex-40"))
    home = by_id["user-home-path"]
    assert home["specifics"] == "a user's home-directory path" and home["relaxed"] == "warn"
    assert re.search(home["pattern"], "/home/jane/x") and re.search(home["pattern"], "C:\\Users\\jane\\x")
    for id, match, relaxed in (("credentials", None, None), ("financial", None, None),
                               ("account-ids", None, None), ("people", "judgment", "warn"),
                               ("trackers", None, "warn"), ("personal-framing", "judgment", "ignore"),
                               ("history", None, "ignore")):
        entry = by_id[id]
        assert "pattern" not in entry and entry["specifics"], id
        assert entry.get("match") == match and entry.get("relaxed") == relaxed, id
    _, checks = _checks(repo, "see /home/jane/notes.txt\n")
    assert not checks["Patterns"].passed and "[path] /home/jane/" in checks["Patterns"].findings[0]


# --- config migrate --------------------------------------------------------------------

OLD_USER = """patterns:
- id: me
  pattern: Jane Doe
  category: name
- id: card
  pattern: '1234'
  judgment: true
  note: only beside account or card
- id: github-pat
  disabled: true
- id: fine
  pattern: fine
  judgment: false
  disabled: false
notes:
- a city is a finding only beside an address
"""


def test_config_migrate_shows_the_diff_and_writes_only_on_confirmation(repo):
    _user_file().write_text(OLD_USER)
    (repo / "git-scan.yaml").write_text("patterns:\n- id: proj\n  pattern: Acme\n  note: the customer\n")
    assert _run("run", str(repo)).exit_code == 1                     # old fields are refused
    r = _run("config", "migrate", input="n\n")
    assert r.exit_code == 0, r.output
    assert "-  judgment: true\n" in r.output and "+  match: judgment\n" in r.output
    assert "-  note: only beside account or card" in r.output and "+  specifics: only beside account or card" in r.output
    assert "-  disabled: true\n" in r.output and "+  match: ignore\n" in r.output
    assert "+  specifics: the customer" in r.output
    assert "nothing written" in r.output and "still refused" not in r.output
    assert _user_file().read_text() == OLD_USER                     # untouched
    r = _run("config", "migrate", input="y\n")
    assert r.exit_code == 0 and r.output.count("wrote ") == 2, r.output
    data = yaml.safe_load(_user_file().read_text())
    assert data["patterns"] == [
        {"id": "me", "pattern": "Jane Doe", "category": "name"},
        {"id": "card", "pattern": "1234", "match": "judgment", "specifics": "only beside account or card"},
        {"id": "github-pat", "match": "ignore"},
        {"id": "fine", "pattern": "fine"},
    ]
    assert data["notes"] == ["a city is a finding only beside an address"]
    assert yaml.safe_load((repo / "git-scan.yaml").read_text())["patterns"] == \
        [{"id": "proj", "pattern": "Acme", "specifics": "the customer"}]
    assert _run("run", str(repo), "--step", "patterns").exit_code == 0
    r = _run("config", "migrate")
    assert r.exit_code == 0 and r.output.count("nothing to migrate") == 2


def test_config_migrate_names_what_validation_would_still_refuse(repo):
    _user_file().write_text("patterns:\n- id: x\n  pattern: x\n  judgment: true\n  colour: red\n")
    r = _run("config", "migrate", "--yes")
    assert r.exit_code == 0, r.output
    assert "still refused" in r.output and "unknown field 'colour'" in r.output
    assert "wrote" in r.output                                      # the rewrite itself still happens
    assert "judgment: true" not in _user_file().read_text() and "colour: red" in _user_file().read_text()
