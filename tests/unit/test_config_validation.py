"""Config layers are validated on load; personal patterns must be declared."""

import os
from pathlib import Path

import pytest

from git_scan.sdk.config import load_config, ConfigError, write_missing_config_help
from git_scan.sdk.scanner import run_scan


def _user_file() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"]) / "git-scan" / "git-scan.yaml"


def _repo(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    return repo


def test_missing_user_config_refuses_before_any_check(tmp_path):
    _user_file().unlink()
    report = run_scan(str(_repo(tmp_path)))
    assert report.failed and report.checks == []
    text = "\n".join(report.refusal)
    assert "no user config on this machine" in text and "Commit refused" in text
    assert "git-scan patterns add" in text and "git-scan patterns clear" in text


def test_user_config_without_patterns_key_refuses(tmp_path):
    _user_file().write_text("thresholds:\n  cents_review: 100\n")
    report = run_scan(str(_repo(tmp_path)))
    assert report.failed and report.checks == []
    assert "has no personal patterns" in "\n".join(report.refusal)


def test_missing_config_help_replaces_the_setup_instructions(tmp_path):
    _user_file().unlink()
    write_missing_config_help("Commits go through the review tool, which also sets this up.")
    text = "\n".join(run_scan(str(_repo(tmp_path))).refusal)
    assert "Commits go through the review tool, which also sets this up." in text
    assert "patterns clear" not in text and "patterns add" not in text


def test_missing_config_help_is_not_shown_when_configured(tmp_path):
    write_missing_config_help("Commits go through the review tool.")
    report = run_scan(str(_repo(tmp_path)))
    assert report.refusal == []
    assert next(c for c in report.checks if c.name == "Personal patterns").passed


def test_a_step_only_run_is_not_refused(tmp_path):
    _user_file().unlink()
    report = run_scan(str(_repo(tmp_path)), only_step="amounts")
    assert report.refusal == []


def test_empty_patterns_list_is_an_explicit_opt_out(tmp_path):
    _user_file().write_text("patterns: []\n")
    report = run_scan(str(_repo(tmp_path)))
    assert next(c for c in report.checks if c.name == "Personal patterns").passed


def test_bare_patterns_key_is_rejected_not_merged_as_none(tmp_path):
    _user_file().write_text("patterns:\n")
    with pytest.raises(ConfigError, match="patterns: must be a list"):
        load_config(str(_repo(tmp_path)))


def test_unknown_field_names_file_and_entry(tmp_path):
    _user_file().write_text("patterns:\n  - id: x\n    pattrn: abc\n")
    with pytest.raises(ConfigError) as e:
        load_config(str(_repo(tmp_path)))
    msg = str(e.value)
    assert str(_user_file()) in msg
    assert "patterns[0] (x): unknown field 'pattrn'" in msg
    assert "git-scan patterns remove" in msg


def test_invalid_regex_is_rejected(tmp_path):
    _user_file().write_text("patterns:\n  - id: x\n    pattern: '('\n")
    with pytest.raises(ConfigError, match="not a valid regex"):
        load_config(str(_repo(tmp_path)))


def test_invalid_config_fails_the_scan(tmp_path):
    _user_file().write_text("bogus: 1\n")
    report = run_scan(str(_repo(tmp_path)))
    assert report.failed
    assert report.checks[-1].name == "Configuration"


def test_pattern_counts_and_sources(tmp_path):
    _user_file().write_text("patterns:\n  - id: me\n    pattern: Jane\n    category: name\n")
    config = load_config(str(_repo(tmp_path)))
    assert config.pattern_sources["me"] == "user"
    assert config.pattern_sources["github-pat"] == "built-in"
    assert config.pattern_counts["personal"] == 1
    assert config.pattern_counts["built-in"] >= 15


def test_judgment_note_ignore_case_and_notes_are_accepted(tmp_path):
    _user_file().write_text(
        "patterns:\n"
        "  - id: card\n"
        "    pattern: '(1234'\n"       # not a regex; fine for a judgment entry
        "    judgment: true\n"
        "    note: only beside account\n"
        "  - id: me\n"
        "    pattern: Jane\n"
        "    ignore_case: true\n"
        "notes:\n"
        "  - a city is a finding only beside an address\n"
    )
    config = load_config(str(_repo(tmp_path)))
    assert [p["id"] for p in config.judgment_patterns] == ["card"]
    assert [p["id"] for p in config.scan_patterns if p["id"] in ("card", "me")] == ["me"]
    assert config.notes == ["a city is a finding only beside an address"]
    assert config.pattern_counts["judgment"] == 1
    assert config.pattern_counts["personal"] == 1


def test_bad_types_for_new_fields_are_rejected(tmp_path):
    repo = _repo(tmp_path)
    _user_file().write_text("patterns:\n  - id: x\n    pattern: a\n    note: 3\n    judgment: yes please\n")
    with pytest.raises(ConfigError) as e:
        load_config(str(repo))
    assert "'note' must be text" in str(e.value)
    assert "'judgment' must be true or false" in str(e.value)
    _user_file().write_text("patterns: []\nnotes: not-a-list\n")
    with pytest.raises(ConfigError, match="notes: must be a list of text"):
        load_config(str(repo))


def test_notes_combine_across_layers(tmp_path):
    repo = _repo(tmp_path)
    _user_file().write_text("patterns: []\nnotes:\n  - from the user\n")
    (repo / "git-scan.yaml").write_text("notes:\n  - from the project\n")
    assert load_config(str(repo)).notes == ["from the user", "from the project"]
