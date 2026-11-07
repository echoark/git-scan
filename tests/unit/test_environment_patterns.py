"""The environment layer: identity patterns derived from the machine, as
entries with ids that take the same controls as any other."""

import subprocess
from pathlib import Path

import yaml
from click.testing import CliRunner

from git_scan.cli.main import cli
from git_scan.sdk.config import load_config
from git_scan.sdk.scanner import run_scan
from git_scan.sdk.user_patterns import export_patterns


def _run(*args):
    return CliRunner().invoke(cli, list(args))


def _repo(tmp_path, monkeypatch, name="Jane Smith", email="jane.smith@example.com"):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    monkeypatch.setenv("GIT_AUTHOR_NAME", name)
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", email)
    monkeypatch.chdir(repo)
    return repo


def _checks(repo, text, file="notes.md"):
    (repo / file).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", file], check=True)
    report = run_scan(str(repo), only_step="patterns")
    return {c.name: c for c in report.checks}


def _user_file() -> Path:
    import os
    return Path(os.environ["XDG_CONFIG_HOME"]) / "git-scan" / "git-scan.yaml"


def test_derived_entries_have_stable_ids_in_the_environment_layer(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    config = load_config(str(repo))
    sources = config.pattern_sources
    for id in ("git-name-full", "git-name-first", "git-name-last", "git-email", "local-username"):
        assert sources[id] == "environment", id
    by_id = {p["id"]: p for p in config.patterns}
    assert by_id["git-name-full"]["category"] == "name" and "judgment" not in by_id["git-name-full"]
    assert by_id["git-email"]["category"] == "email" and "judgment" not in by_id["git-email"]
    assert by_id["git-name-first"]["judgment"] and by_id["git-name-last"]["judgment"]
    assert by_id["local-username"]["judgment"] and by_id["local-username"]["category"] == "username"
    out = _run("patterns", "list").output
    assert "git-name-last" in out and "environment" in out
    assert _user_file().read_text() == "patterns: []\n"            # nothing derived is written


def test_a_surname_inside_another_word_is_a_judgment_match_not_a_block(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    checks = _checks(repo, "The acme360 tenant is named Smith360.\n")
    assert checks["Patterns"].passed
    assert not checks["Judgment"].passed
    text = "\n".join(checks["Judgment"].findings)
    assert "notes.md:1 [name] Smith" in text and "git-scan ack" in text


def test_upper_and_lower_case_forms_match_identically(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    upper = _checks(repo, "SMITH360\n")["Judgment"]
    lower = _checks(repo, "smith360\n")["Judgment"]
    assert not upper.passed and not lower.passed
    assert [f.split("] ")[0] for f in upper.findings] == [f.split("] ")[0] for f in lower.findings]


def test_full_name_and_email_are_findings(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    checks = _checks(repo, "Written by jane smith <jane.smith@example.com>\n")
    assert not checks["Patterns"].passed
    text = "\n".join(checks["Patterns"].findings)
    assert "[name] jane smith" in text and "[email] jane.smith@example.com" in text


def test_a_common_word_first_name_can_be_turned_off_or_made_case_sensitive(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, name="Will Smith")
    assert not _checks(repo, "the build will fail\n")["Judgment"].passed

    r = _run("patterns", "edit", "git-name-first", "--no-ignore-case", "--word-boundary")
    assert r.exit_code == 0, r.output
    assert {"id": "git-name-first", "ignore_case": False, "word_boundary": True} in \
        yaml.safe_load(_user_file().read_text())["patterns"]
    assert _checks(repo, "the build will fail\n")["Judgment"].passed
    assert not _checks(repo, "ask Will about it\n")["Judgment"].passed

    r = _run("patterns", "remove", "git-name-first")
    assert r.exit_code == 0 and "turned off the environment pattern" in r.output, r.output
    assert _checks(repo, "ask Will about it\n")["Judgment"].passed
    assert "git-name-first" not in _run("patterns", "list").output
    assert "git-name-first" in _run("patterns", "list", "--all").output
    r = _run("patterns", "restore", "git-name-first")
    assert r.exit_code == 0, r.output
    assert not _checks(repo, "ask Will about it\n")["Judgment"].passed


def test_a_derived_judgment_entry_can_be_made_a_finding_again(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    r = _run("patterns", "edit", "git-name-last", "--no-judgment")
    assert r.exit_code == 0, r.output
    assert {"id": "git-name-last", "judgment": False} in yaml.safe_load(_user_file().read_text())["patterns"]
    checks = _checks(repo, "Smith360\n")
    assert not checks["Patterns"].passed and "[name] Smith" in checks["Patterns"].findings[0]


def test_a_catalog_entry_for_the_same_value_governs(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    r = _run("patterns", "add", "my-surname", "--pattern", "smith", "--category", "surname",
             "--judgment", "--word-boundary", "--note", "a finding when it names the author")
    assert r.exit_code == 0, r.output
    config = load_config(str(repo))
    assert "git-name-last" not in config.pattern_sources          # suppressed
    assert config.pattern_sources["git-name-first"] == "environment"
    assert _checks(repo, "Smith360\n")["Judgment"].passed           # the catalog's word boundary applies
    judgment = _checks(repo, "ask Smith\n")["Judgment"]
    assert not judgment.passed and "[surname] Smith" in "\n".join(judgment.findings)


def test_exceptions_apply_to_a_derived_entry(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    r = _run("patterns", "exceptions", "add", "git-name-last", "sonian")
    assert r.exit_code == 0, r.output
    assert _checks(repo, "the Smithsonian\n")["Judgment"].passed
    assert not _checks(repo, "the Smiths\n")["Judgment"].passed


def test_env_values_still_block(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    secret = "s3cr3t-" + "value-abc"
    (repo / ".env").write_text(f"# comment\nAPI_TOKEN={secret}\nSHORT=abc\n")
    checks = _checks(repo, f"token = {secret.upper()}\n")
    assert not checks["Patterns"].passed
    assert "[env]" in checks["Patterns"].findings[0]
    config = load_config(str(repo))
    assert config.pattern_sources["env-API_TOKEN"] == "environment"
    assert not any(p["id"].startswith("env-SHORT") for p in config.patterns)
    # .env values never leave the machine: the reviewer's catalog has no env entry
    assert not any(e["id"].startswith("env-") for e in export_patterns(str(repo))["patterns"])
    assert any(e["id"] == "git-name-last" for e in export_patterns(str(repo))["patterns"])


def test_an_override_with_nothing_to_apply_to_is_ignored(tmp_path, monkeypatch):
    """A one-word git name has no parts; an override of git-name-first written
    on another machine must not break the scan here."""
    repo = _repo(tmp_path, monkeypatch, name="Solo")
    _user_file().write_text("patterns:\n  - id: git-name-first\n    word_boundary: true\n")
    config = load_config(str(repo))
    assert "git-name-first" not in [p["id"] for p in config.all_patterns]
    assert _checks(repo, "plain\n")["Patterns"].passed
    assert _run("run", str(repo)).exit_code == 0


def test_names_of_three_or_more_parts_are_numbered(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, name="Jane Q de Smith")     # 'Q' and 'de' are too short
    ids = [p["id"] for p in load_config(str(repo)).patterns if p["id"].startswith("git-name-")]
    assert ids == ["git-name-full", "git-name-part-1", "git-name-part-4"]


def test_copyright_line_in_a_license_is_not_a_match(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    checks = _checks(repo, "Copyright 2026 Jane Smith\nMIT License\n", file="LICENSE")
    assert checks["Patterns"].passed and "1 ignored" in checks["Patterns"].info
    assert checks["Judgment"].passed


def test_patterns_info_counts_environment_entries(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    info = _checks(repo, "plain\n")["Patterns"].info
    assert "2 environment" in info                     # git-name-full and git-email are findings
