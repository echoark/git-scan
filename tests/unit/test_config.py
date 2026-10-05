"""Tests for layered config loading."""

import os
from pathlib import Path

from git_scan.sdk.config import load_config, MergedConfig


def test_load_config_package_defaults_only(tmp_path, monkeypatch):
    """With no user/project config, package defaults load."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "noconfig"))
    config = load_config(str(tmp_path))
    assert config.entropy_enabled is True
    assert config.entropy_threshold == 4.2
    assert isinstance(config.thresholds, dict)


def test_project_config_overrides(tmp_path, monkeypatch):
    """Project git-scan.yaml overrides package defaults."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "noconfig"))

    project_config = tmp_path / "git-scan.yaml"
    project_config.write_text(
        "entropy:\n  threshold: 5.0\n"
        "patterns:\n"
        "  - id: test-pat\n"
        "    pattern: SECRET\n"
    )

    # Fake a git repo
    (tmp_path / ".git").mkdir()

    config = load_config(str(tmp_path))
    assert config.entropy_threshold == 5.0
    assert any(p["id"] == "test-pat" for p in config.patterns)


def test_disabled_pattern_filtered(tmp_path, monkeypatch):
    """Patterns with disabled: true are excluded from .patterns."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "noconfig"))

    project_config = tmp_path / "git-scan.yaml"
    project_config.write_text(
        "patterns:\n"
        "  - id: active\n"
        "    pattern: ACTIVE\n"
        "  - id: gone\n"
        "    pattern: GONE\n"
        "    disabled: true\n"
    )

    config = load_config(str(tmp_path))
    ids = [p["id"] for p in config.patterns]
    assert "active" in ids
    assert "gone" not in ids
    # But all_patterns includes it
    all_ids = [p["id"] for p in config.all_patterns]
    assert "gone" in all_ids


def test_entropy_exclusions_from_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "noconfig"))

    project_config = tmp_path / "git-scan.yaml"
    project_config.write_text(
        "entropy:\n"
        "  exclusions:\n"
        "    - id: custom-excl\n"
        "      file_glob: '*.lock'\n"
        "      content_pattern: 'sha256-\\S+'\n"
    )

    config = load_config(str(tmp_path))
    excl_ids = [e["id"] for e in config.entropy_exclusions]
    # Should have both package defaults and project additions
    assert "custom-excl" in excl_ids


def test_user_config_layer(tmp_path, monkeypatch):
    """User config merges between package and project."""
    user_dir = tmp_path / "user_config" / "git-scan"
    user_dir.mkdir(parents=True)
    (user_dir / "git-scan.yaml").write_text("entropy:\n  threshold: 3.5\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user_config"))

    config = load_config(str(tmp_path))
    assert config.entropy_threshold == 3.5


def test_three_layer_precedence(tmp_path, monkeypatch):
    """Project > user > package for scalar values."""
    user_dir = tmp_path / "user_config" / "git-scan"
    user_dir.mkdir(parents=True)
    (user_dir / "git-scan.yaml").write_text("entropy:\n  threshold: 3.0\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user_config"))

    (tmp_path / "git-scan.yaml").write_text("entropy:\n  threshold: 5.5\n")

    config = load_config(str(tmp_path))
    assert config.entropy_threshold == 5.5
