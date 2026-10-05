"""Auto-isolate every test from real config directories."""

import pytest


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    """Redirect XDG_CONFIG_HOME so no test reads real user config."""
    test_config = tmp_path / "xdg_config"
    test_config.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(test_config))
