"""The patterns / config / hook commands, through the real CLI and real files."""

import os
import subprocess
from pathlib import Path

import yaml
from click.testing import CliRunner

from git_scan.cli.main import cli


def _user_file() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"]) / "git-scan" / "git-scan.yaml"


def _run(*args):
    return CliRunner().invoke(cli, list(args))


def _repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.chdir(repo)
    return repo


def test_add_creates_file_and_entry(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    _user_file().unlink()
    r = _run("patterns", "add", "my-name", "--pattern", "Jane Doe", "--category", "name",
             "--word-boundary", "--not-followed-by", " Inc")
    assert r.exit_code == 0, r.output
    data = yaml.safe_load(_user_file().read_text())
    assert data["patterns"] == [{"id": "my-name", "pattern": "Jane Doe", "category": "name",
                                 "word_boundary": True, "not_followed_by": [" Inc"]}]


def test_add_rejects_duplicate_and_bad_regex(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    assert _run("patterns", "add", "x", "--pattern", "abc").exit_code == 0
    r = _run("patterns", "add", "x", "--pattern", "abc")
    assert r.exit_code == 1 and "already exists" in r.output
    r = _run("patterns", "add", "y", "--pattern", "(")
    assert r.exit_code == 1 and "not a valid regex" in r.output


def test_remove_own_entry_deletes_it(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    _run("patterns", "add", "x", "--pattern", "abc")
    r = _run("patterns", "remove", "x")
    assert r.exit_code == 0 and "removed 'x'" in r.output
    assert yaml.safe_load(_user_file().read_text())["patterns"] == []


def test_remove_builtin_turns_it_off_and_restore_turns_it_on(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    r = _run("patterns", "remove", "github-pat")
    assert r.exit_code == 0 and "built in" in r.output
    assert {"id": "github-pat", "disabled": True} in yaml.safe_load(_user_file().read_text())["patterns"]
    listed = _run("patterns", "list", "--all").output
    assert "github-pat" in listed and "restore" in listed
    assert "github-pat" not in _run("patterns", "list").output
    r = _run("patterns", "restore", "github-pat")
    assert r.exit_code == 0
    assert "github-pat" in _run("patterns", "list").output


def test_remove_unknown_suggests_close_match(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    r = _run("patterns", "remove", "github-pt")
    assert r.exit_code == 1 and "did you mean github-pat" in r.output


def test_clear_writes_empty_list_and_scan_passes(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _user_file().unlink()
    assert _run("run", str(repo)).exit_code == 1
    assert _run("patterns", "clear").exit_code == 0
    assert yaml.safe_load(_user_file().read_text()) == {"patterns": []}
    assert _run("run", str(repo)).exit_code == 0


def test_list_shows_layer(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    _run("patterns", "add", "me", "--pattern", "Jane", "--category", "name")
    out = _run("patterns", "list").output
    assert "me" in out and "user" in out and "built-in" in out and "[name]" in out


def test_config_settings_get_and_set(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    assert _run("config", "large-amount").output.strip() == "300000"
    assert _run("config", "large-amount", "500000").exit_code == 0
    assert _run("config", "large-amount").output.strip() == "500000"
    assert _run("config", "entropy-enabled", "off").exit_code == 0
    assert _run("config", "entropy-enabled").output.strip() == "off"
    assert _run("config", "large-amount", "lots").exit_code == 1


def test_config_path_lists_layers(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    out = _run("config", "path").output
    assert "built-in" in out and str(_user_file()) in out and "present" in out


def test_hook_install_writes_script(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    hooks = tmp_path / "hooks"
    r = _run("hook", "install", "--hooks-dir", str(hooks))
    assert r.exit_code == 0, r.output
    script = hooks / "pre-commit"
    assert "git-scan run" in script.read_text()
    assert os.access(script, os.X_OK)
    script.write_text("#!/bin/sh\necho mine\n")
    assert _run("hook", "install", "--hooks-dir", str(hooks)).exit_code == 1
    assert _run("hook", "install", "--hooks-dir", str(hooks), "--force").exit_code == 0


def test_exclude_files_skips_matching_paths(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    _run("patterns", "add", "co", "--pattern", "Vendorco", "--category", "employer",
         "--exclude-file", "data/*.yaml")
    (repo / "data").mkdir()
    (repo / "data" / "defaults.yaml").write_text("id: Vendorco-oauth\n")
    (repo / "notes.md").write_text("Vendorco\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    r = _run("run", str(repo), "--step", "patterns")
    assert r.exit_code == 1
    assert "notes.md:1" in r.output and "defaults.yaml" not in r.output


def test_edit_overrides_a_builtin_and_updates_own_entry(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    r = _run("patterns", "edit", "github-pat", "--exclude-file", "tests/**")
    assert r.exit_code == 0, r.output
    data = yaml.safe_load(_user_file().read_text())
    assert {"id": "github-pat", "exclude_files": ["tests/**"]} in data["patterns"]
    _run("patterns", "add", "me", "--pattern", "Jane")
    assert _run("patterns", "edit", "me", "--category", "name", "--word-boundary").exit_code == 0
    entry = next(e for e in yaml.safe_load(_user_file().read_text())["patterns"] if e["id"] == "me")
    assert entry["category"] == "name" and entry["word_boundary"] is True
    assert _run("patterns", "edit", "nope", "--category", "x").exit_code == 1


def test_allowed_emails_round_trip_and_scan(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    addr = "bot@" + "sample-corp.org"
    (repo / "f.txt").write_text(f"{addr}\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    assert _run("run", str(repo), "--step", "emails").exit_code == 1
    assert _run("emails", "allow", addr).exit_code == 0
    assert _run("emails", "allow", addr).exit_code == 1          # duplicate
    assert addr in _run("emails", "list").output
    assert _run("run", str(repo), "--step", "emails").exit_code == 0
    assert _run("emails", "disallow", addr).exit_code == 0
    assert _run("emails", "disallow", addr).exit_code == 1
    assert _run("emails", "allow", "not-an-address").exit_code == 1


def test_exclude_files_matches_the_full_path_not_the_base_name(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    _run("patterns", "add", "co", "--pattern", "Vendorco", "--exclude-file", "data/defaults.yaml")
    (repo / "data").mkdir()
    (repo / "data" / "defaults.yaml").write_text("Vendorco\n")      # excluded
    (repo / "other").mkdir()
    (repo / "other" / "defaults.yaml").write_text("Vendorco\n")     # same name, still scanned
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    r = _run("run", str(repo), "--step", "patterns")
    assert r.exit_code == 1
    assert "other/defaults.yaml:1" in r.output and "data/defaults.yaml" not in r.output


def test_edit_with_empty_value_clears_a_list_field(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    _run("patterns", "add", "co", "--pattern", "Vendorco", "--exclude-file", "data/*.yaml")
    assert _run("patterns", "edit", "co", "--exclude-file", "").exit_code == 0
    entry = next(e for e in yaml.safe_load(_user_file().read_text())["patterns"] if e["id"] == "co")
    assert "exclude_files" not in entry


def test_project_remove_turns_off_a_user_pattern_for_that_repo_only(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _run("patterns", "add", "co", "--pattern", "Vendorco", "--category", "employer")
    r = _run("patterns", "remove", "co", "--project")
    assert r.exit_code == 0 and "off for this repository" in r.output, r.output
    assert yaml.safe_load((repo / "git-scan.yaml").read_text()) == {
        "patterns": [{"id": "co", "disabled": True}]}
    # still in the user layer, still active elsewhere
    assert any(e["id"] == "co" and "disabled" not in e
               for e in yaml.safe_load(_user_file().read_text())["patterns"])
    (repo / "notes.md").write_text("Vendorco\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    assert _run("run", str(repo), "--step", "patterns").exit_code == 0
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(other)], check=True)
    (other / "notes.md").write_text("Vendorco\n")
    subprocess.run(["git", "-C", str(other), "add", "."], check=True)
    assert _run("run", str(other), "--step", "patterns").exit_code == 1
    assert "restore" in _run("patterns", "list", "--all").output   # how to undo is shown


def test_identity_allow_remove_list(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    assert "no identity rules" in _run("identity", "list").output
    r = _run("identity", "allow", "github.com/octo", "*+octo@users.noreply.github.com")
    assert r.exit_code == 0, r.output
    r = _run("identity", "allow", "github.com/octo/", "me@example.com")        # appends, same rule
    assert r.exit_code == 0 and "me@example.com" in r.output
    data = yaml.safe_load(_user_file().read_text())
    assert data["identity"] == [{"remote": "github.com/octo",
                                 "emails": ["*+octo@users.noreply.github.com", "me@example.com"]}]
    assert "github.com/octo:" in _run("identity", "list").output
    assert _run("identity", "allow", "https://github.com/octo", "x@example.com").exit_code == 1
    assert _run("identity", "remove", "github.com/octo", "me@example.com").exit_code == 0
    assert _run("identity", "remove", "github.com/octo", "me@example.com").exit_code == 1
    assert _run("identity", "remove", "github.com/octo").exit_code == 0
    assert yaml.safe_load(_user_file().read_text())["identity"] == []
    assert _run("identity", "remove", "github.com/nope").exit_code == 1


def test_invalid_identity_rule_fails_the_scan(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _user_file().write_text("patterns: []\nidentity:\n  - remote: github.com/octo\n")
    r = _run("run", str(repo))
    assert r.exit_code == 1 and "emails" in r.output


# --- patterns exceptions ----------------------------------------------------

def _entry(path: Path, id: str) -> dict:
    return next(e for e in yaml.safe_load(path.read_text())["patterns"] if e["id"] == id)


def test_exceptions_add_keeps_existing_values_and_is_idempotent(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    _run("patterns", "add", "co", "--pattern", "Vendorco", "--not-followed-by", " Cloud")
    r = _run("patterns", "exceptions", "add", "co", "-sdk")
    assert r.exit_code == 0, r.output
    assert r.output == " Cloud\n-sdk\n"                      # the resulting list
    assert _entry(_user_file(), "co")["not_followed_by"] == [" Cloud", "-sdk"]
    r = _run("patterns", "exceptions", "add", "co", "-sdk", " Cloud")
    assert r.exit_code == 0 and r.output == " Cloud\n-sdk\n"
    assert _entry(_user_file(), "co")["not_followed_by"] == [" Cloud", "-sdk"]


def test_exceptions_list_prints_one_value_per_line_and_nothing_else(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    _run("patterns", "add", "co", "--pattern", "Vendorco")
    assert _run("patterns", "exceptions", "list", "co").output == ""
    _run("patterns", "exceptions", "add", "co", " Cloud", "-sdk")
    r = _run("patterns", "exceptions", "list", "co")
    assert r.exit_code == 0 and r.output == " Cloud\n-sdk\n"
    r = _run("patterns", "exceptions", "list", "nope")
    assert r.exit_code == 1 and "no pattern 'nope'" in r.output


def test_exceptions_remove_drops_named_values_and_fails_on_an_absent_one(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    _run("patterns", "add", "co", "--pattern", "Vendorco", "--not-followed-by", " Cloud",
         "--not-followed-by", "-sdk")
    r = _run("patterns", "exceptions", "remove", "co", "-sdk")
    assert r.exit_code == 0 and r.output == " Cloud\n"
    assert _entry(_user_file(), "co")["not_followed_by"] == [" Cloud"]
    before = _user_file().read_text()
    r = _run("patterns", "exceptions", "remove", "co", "-sdk")
    assert r.exit_code == 1 and "has no exception '-sdk'" in r.output
    assert "exceptions list co" in r.output
    assert _user_file().read_text() == before                 # nothing written
    r = _run("patterns", "exceptions", "remove", "co", " Cloud", "-x")      # one absent: none removed
    assert r.exit_code == 1 and _user_file().read_text() == before
    r = _run("patterns", "exceptions", "remove", "co", " Cloud")
    assert r.exit_code == 0 and r.output == ""
    assert "not_followed_by" not in _entry(_user_file(), "co")


def test_exceptions_change_what_the_scan_matches(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    _run("patterns", "add", "co", "--pattern", "Vendorco", "--category", "employer")
    (repo / "notes.md").write_text("Vendorco Cloud\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    assert _run("run", str(repo), "--step", "patterns").exit_code == 1
    assert _run("patterns", "exceptions", "add", "co", " Cloud").exit_code == 0
    assert _run("run", str(repo), "--step", "patterns").exit_code == 0
    assert _run("patterns", "exceptions", "remove", "co", " Cloud").exit_code == 0
    assert _run("run", str(repo), "--step", "patterns").exit_code == 1


def test_exceptions_project_writes_the_repo_file_with_the_full_list(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    _run("patterns", "add", "co", "--pattern", "Vendorco", "--not-followed-by", " Cloud")
    r = _run("patterns", "exceptions", "add", "co", "-sdk", "--project")
    assert r.exit_code == 0 and r.output == " Cloud\n-sdk\n", r.output
    # the project override carries the user's value too, since the merge replaces lists
    assert yaml.safe_load((repo / "git-scan.yaml").read_text()) == {
        "patterns": [{"id": "co", "not_followed_by": [" Cloud", "-sdk"]}]}
    assert _entry(_user_file(), "co")["not_followed_by"] == [" Cloud"]      # user layer untouched
    assert _run("patterns", "exceptions", "list", "co").output == " Cloud\n-sdk\n"
    # removing the user's value in the project leaves an explicit empty override there
    r = _run("patterns", "exceptions", "remove", "co", " Cloud", "-sdk", "--project")
    assert r.exit_code == 0 and r.output == ""
    assert yaml.safe_load((repo / "git-scan.yaml").read_text()) == {
        "patterns": [{"id": "co", "not_followed_by": []}]}
    assert _run("patterns", "exceptions", "list", "co").output == ""


def test_exceptions_on_a_builtin_write_a_user_layer_override(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    r = _run("patterns", "exceptions", "add", "github-pat", "_example")
    assert r.exit_code == 0 and r.output == "_example\n", r.output
    data = yaml.safe_load(_user_file().read_text())
    assert data["patterns"] == [{"id": "github-pat", "not_followed_by": ["_example"]}]
    assert _run("patterns", "exceptions", "list", "github-pat").output == "_example\n"
    r = _run("patterns", "exceptions", "remove", "github-pat", "_example")
    assert r.exit_code == 0 and r.output == ""
    assert yaml.safe_load(_user_file().read_text())["patterns"] == [
        {"id": "github-pat", "not_followed_by": []}]
    assert _run("run", str(tmp_path / "repo")).exit_code == 0          # the override is valid config


def test_exceptions_reject_unknown_pattern_and_empty_value(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    r = _run("patterns", "exceptions", "add", "github-pt", "x")
    assert r.exit_code == 1 and "did you mean github-pat" in r.output
    _run("patterns", "add", "co", "--pattern", "Vendorco")
    r = _run("patterns", "exceptions", "add", "co", "")
    assert r.exit_code == 1 and "must not be empty" in r.output
    assert "not_followed_by" not in _entry(_user_file(), "co")
    assert _run("patterns", "exceptions", "add", "co").exit_code == 2        # VALUE is required
    # values that look like options are values; '--' also works
    r = _run("patterns", "exceptions", "add", "co", "--", "-sdk", "--project")
    assert r.exit_code == 0 and r.output == "-sdk\n--project\n", r.output
    assert not (Path.cwd() / "git-scan.yaml").exists()


def test_edit_not_followed_by_still_replaces_and_points_at_exceptions_add(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    _run("patterns", "add", "co", "--pattern", "Vendorco", "--not-followed-by", " Cloud")
    assert _run("patterns", "edit", "co", "--not-followed-by", "-sdk").exit_code == 0
    assert _entry(_user_file(), "co")["not_followed_by"] == ["-sdk"]
    assert "exceptions add" in _run("patterns", "edit", "--help").output
