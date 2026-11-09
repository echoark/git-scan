"""What a match does, on the scan side: each entry's ``match`` (block,
judgment, warn, ignore) decides, or its ``relaxed`` value in a clone that
is eligible for a relaxed review and private; the commands that set them,
and ``config migrate``."""

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
from git_scan.sdk import relaxed
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


LOGGING_GH = ('import os, sys\nlog = os.environ.get("STUB_GH_LOG")\n'
              'if log:\n    open(log, "a").write(" ".join(sys.argv[1:]) + "\\n")\n'
              'answer = os.environ.get("STUB_GH", "private")\n'
              'if answer == "fail":\n    sys.exit(1)\nprint(answer)\n')


@pytest.fixture
def gh_log(repo, tmp_path, monkeypatch):
    """The fixture's gh, logging each call, so a test can count network asks."""
    (tmp_path / "ghbin" / "gh").write_text(f"#!{sys.executable}\n" + LOGGING_GH)
    log = tmp_path / "gh.log"
    monkeypatch.setenv("STUB_GH_LOG", str(log))
    return lambda: len(log.read_text().splitlines()) if log.exists() else 0


def _relaxed_judgment():
    assert _run("patterns", "edit", "me", "--relaxed", "judgment").exit_code == 0


def test_an_eligible_private_clone_applies_each_entrys_relaxed_value_at_commit_time(repo, gh_log):
    _relaxed_judgment()
    assert _run("relax").exit_code == 0
    report, checks = _checks(repo, "Jane Doe\n")
    assert report.relaxed == "verified private by github.com"
    assert checks["Patterns"].passed                                  # not a block here
    assert not checks["Judgment"].passed and "notes.md:1 [name] Jane Doe" in checks["Judgment"].findings
    assert _run("ack").exit_code == 0                                 # ackable
    _, checks = _checks(repo, "Jane Doe\n")
    assert checks["Judgment"].passed and checks["Judgment"].info.endswith("acknowledged")
    r = _run("run", str(repo))
    assert "relaxed: private repo (verified private by github.com)" in r.output


def test_the_push_gates_scan_of_commits_applies_it_too(repo, gh_log):
    _relaxed_judgment()
    (repo / "a.md").write_text("Jane Doe\n")
    _git(repo, "add", "a.md")
    _git(repo, "commit", "-q", "--no-verify", "-m", "a")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    strict = {c.name: c for c in run_scan(str(repo), commits=[sha]).checks}
    assert not strict["Patterns"].passed
    assert _run("relax").exit_code == 0
    report = run_scan(str(repo), commits=[sha])
    checks = {c.name: c for c in report.checks}
    assert report.relaxed and checks["Patterns"].passed and not checks["Judgment"].passed
    assert f"  git-scan ack --commit {sha[:12]}" in checks["Judgment"].findings


def test_removed_lines_are_treated_like_added_ones(repo, gh_log):
    _relaxed_judgment()
    (repo / "old.md").write_text("Jane Doe\n")
    _git(repo, "add", "old.md")
    _git(repo, "commit", "-q", "-m", "old")
    assert _run("relax").exit_code == 0
    _git(repo, "rm", "-q", "old.md")
    report = run_scan(str(repo))
    checks = {c.name: c for c in report.checks}
    assert checks["Patterns"].passed and "old.md:1 (removed) [name] Jane Doe" in checks["Judgment"].findings


def test_the_same_entry_blocks_in_an_ineligible_clone(repo, gh_log):
    _relaxed_judgment()
    report, checks = _checks(repo, "Jane Doe\n")                     # never made eligible
    assert report.relaxed is None and not checks["Patterns"].passed
    assert gh_log() == 0
    r = _run("run", str(repo))
    assert "relaxed:" not in r.output


def test_a_public_repository_is_strict_and_the_answer_is_recorded(repo, gh_log, monkeypatch):
    _relaxed_judgment()
    assert _run("relax").exit_code == 0
    _age_privacy_record(repo)
    monkeypatch.setenv("STUB_GH", "public")                           # the repository went public
    report, checks = _checks(repo, "Jane Doe\n")
    assert report.relaxed is None and not checks["Patterns"].passed
    asks = gh_log()
    report, _ = _checks(repo, "Jane Doe\n")                          # the 'public' answer is reused
    assert report.relaxed is None and gh_log() == asks


@pytest.mark.parametrize("why", ["gh fails", "gh missing", "no record yet"])
def test_unknown_privacy_falls_back_to_strict(repo, gh_log, monkeypatch, tmp_path, why):
    _relaxed_judgment()
    assert _run("relax").exit_code == 0
    _age_privacy_record(repo)
    if why == "gh fails":
        monkeypatch.setenv("STUB_GH", "fail")
    elif why == "gh missing":
        (tmp_path / "ghbin" / "gh").unlink()
    else:
        _git(repo, "config", "--local", "--unset", relaxed.KEY_CHECKED)
        monkeypatch.setenv("STUB_GH", "fail")
    report, checks = _checks(repo, "Jane Doe\n")
    assert report.relaxed is None and not checks["Patterns"].passed


def test_no_network_call_per_commit_and_one_ask_once_the_answer_is_old(repo, gh_log):
    _relaxed_judgment()
    assert _run("relax").exit_code == 0
    assert gh_log() == 1                                              # relax asked, and recorded it
    for i in range(3):
        report, _ = _checks(repo, f"Jane Doe {i}\n")
        assert report.relaxed
    assert gh_log() == 1                                              # no ask per commit
    _age_privacy_record(repo)
    for i in range(3):
        report, _ = _checks(repo, f"Jane Doe again {i}\n")
        assert report.relaxed
    assert gh_log() == 2                                              # one ask, then recorded again


def test_public_prospective_keeps_the_commit_scan_strict(repo, gh_log):
    _relaxed_judgment()
    assert _run("relax").exit_code == 0
    assert _run("config", "public-prospective", "on", "--project").exit_code == 0
    report, checks = _checks(repo, "Jane Doe\n")
    assert report.relaxed is None and not checks["Patterns"].passed


def test_without_a_relaxed_value_the_entry_keeps_its_match(repo, gh_log):
    assert _run("relax").exit_code == 0
    report, checks = _checks(repo, "Jane Doe\n")                     # 'me' has no relaxed value
    assert report.relaxed and not checks["Patterns"].passed


def _age_privacy_record(repo):
    """Make the host's last answer older than the commit scan trusts."""
    when, private = relaxed._recorded(str(repo))
    old = int(when) - relaxed.PRIVACY_TTL - 60
    _git(repo, "config", "--local", relaxed.KEY_CHECKED, f"{old} {'private' if private else 'not-private'}")


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
