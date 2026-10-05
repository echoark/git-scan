"""Tests that project-level git-scan.yaml works as a minimal delta config.

Verifies the yaml-config-merge snippet behavior: project config can
disable inherited patterns, add new ones, adjust thresholds, and
add/disable entropy exclusions — all as a small delta file without
repeating the full package defaults.
"""

from git_scan.sdk.config import load_config, PACKAGE_DEFAULTS
from git_scan.sdk.yaml_merge import merge_yaml_configs


def test_project_disables_inherited_pattern(tmp_path, monkeypatch):
    """Project can disable a package-default pattern by id."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no_user"))

    (tmp_path / "git-scan.yaml").write_text(
        "patterns:\n"
        "  - id: my-client\n"
        "    pattern: AcmeCorp\n"
        "  - id: other-client\n"
        "    pattern: WidgetCo\n"
        "    disabled: true\n"
    )

    config = load_config(str(tmp_path))
    active_ids = [p["id"] for p in config.patterns]
    all_ids = [p["id"] for p in config.all_patterns]

    assert "my-client" in active_ids
    assert "other-client" not in active_ids
    assert "other-client" in all_ids  # still in all_patterns


def test_project_disables_inherited_entropy_exclusion(tmp_path, monkeypatch):
    """Project can disable a package-default entropy exclusion by id."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no_user"))

    # The package defaults include npm-integrity. Disable it.
    (tmp_path / "git-scan.yaml").write_text(
        "entropy:\n"
        "  exclusions:\n"
        "    - id: npm-integrity\n"
        "      disabled: true\n"
    )

    config = load_config(str(tmp_path))
    active_ids = [e["id"] for e in config.entropy_exclusions]
    assert "npm-integrity" not in active_ids
    # Other default exclusions should still be present
    assert "yarn-integrity" in active_ids


def test_project_adds_exclusion_and_pattern_as_delta(tmp_path, monkeypatch):
    """Minimal project config adds new items without repeating defaults."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no_user"))

    (tmp_path / "git-scan.yaml").write_text(
        "# Only project-specific additions — package defaults are inherited\n"
        "patterns:\n"
        "  - id: project-secret\n"
        "    pattern: PROJ_SECRET_KEY\n"
        "entropy:\n"
        "  threshold: 4.5\n"
        "  exclusions:\n"
        "    - id: project-hashes\n"
        "      file_glob: '*.sum'\n"
        "      content_pattern: 'h1:[a-zA-Z0-9/+=]+'\n"
    )

    config = load_config(str(tmp_path))

    # New pattern was added
    assert any(p["id"] == "project-secret" for p in config.patterns)

    # New exclusion was added alongside defaults
    excl_ids = [e["id"] for e in config.entropy_exclusions]
    assert "project-hashes" in excl_ids
    assert "npm-integrity" in excl_ids  # default preserved

    # Threshold overridden
    assert config.entropy_threshold == 4.5

    # Other defaults preserved
    assert config.entropy_enabled is True


def test_three_layer_pattern_merge(tmp_path, monkeypatch):
    """Package → user → project: each layer adds/patches patterns."""
    user_dir = tmp_path / "user_config" / "git-scan"
    user_dir.mkdir(parents=True)
    (user_dir / "git-scan.yaml").write_text(
        "patterns:\n"
        "  - id: user-keyword\n"
        "    pattern: MyEmployer\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user_config"))

    (tmp_path / "git-scan.yaml").write_text(
        "patterns:\n"
        "  - id: project-keyword\n"
        "    pattern: ProjectSecret\n"
    )

    config = load_config(str(tmp_path))
    active_ids = [p["id"] for p in config.patterns]
    assert "user-keyword" in active_ids
    assert "project-keyword" in active_ids


def test_project_patches_inherited_pattern_field(tmp_path, monkeypatch):
    """Project can patch a field on an inherited pattern without replacing it."""
    user_dir = tmp_path / "user_config" / "git-scan"
    user_dir.mkdir(parents=True)
    (user_dir / "git-scan.yaml").write_text(
        "patterns:\n"
        "  - id: client-name\n"
        "    pattern: AcmeCorp\n"
        "    category: client\n"
        "    word_boundary: false\n"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user_config"))

    # Project patches only word_boundary, preserving pattern and category
    (tmp_path / "git-scan.yaml").write_text(
        "patterns:\n"
        "  - id: client-name\n"
        "    word_boundary: true\n"
    )

    config = load_config(str(tmp_path))
    pat = next(p for p in config.patterns if p["id"] == "client-name")
    assert pat["pattern"] == "AcmeCorp"  # preserved from user layer
    assert pat["category"] == "client"   # preserved from user layer
    assert pat["word_boundary"] is True  # patched by project layer
