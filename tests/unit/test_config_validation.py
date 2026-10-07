"""Config layers are validated on load; personal patterns must be declared."""

import os
from pathlib import Path

import pytest

from git_scan.sdk.config import load_config, ConfigError
from git_scan.sdk.scanner import run_scan


def _user_file() -> Path:
    return Path(os.environ["XDG_CONFIG_HOME"]) / "git-scan" / "git-scan.yaml"


def _repo(tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    return repo


def test_missing_user_config_fails_scan_with_instructions(tmp_path):
    _user_file().unlink()
    report = run_scan(str(_repo(tmp_path)))
    check = next(c for c in report.checks if c.name == "Personal patterns")
    assert not check.passed
    assert any("git-scan patterns add" in f for f in check.findings)
    assert any("git-scan patterns clear" in f for f in check.findings)


def test_user_config_without_patterns_key_fails(tmp_path):
    _user_file().write_text("thresholds:\n  cents_review: 100\n")
    report = run_scan(str(_repo(tmp_path)))
    assert not next(c for c in report.checks if c.name == "Personal patterns").passed


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
