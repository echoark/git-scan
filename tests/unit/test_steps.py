"""Tests for step discovery and basic step behavior."""

from git_scan.sdk.steps import discover_steps, get_step_names


def test_discover_steps_finds_modules():
    steps = discover_steps()
    names = [name for name, _ in steps]
    assert "entropy_scan" in names
    assert "patterns" in names
    assert "ssn_ein" in names
    assert "emails" in names
    assert "drive_ids" in names
    assert "git_identity" in names
    assert "amounts" in names
    assert "names" in names


def test_get_step_names():
    names = get_step_names()
    assert len(names) >= 7
    assert all(isinstance(n, str) for n in names)


def test_no_split_steps():
    """Old split steps should not exist after consolidation."""
    names = get_step_names()
    assert "generic_regex" not in names
    assert "user_patterns" not in names
    assert "oauth_tokens" not in names
    assert "local_username" not in names
