"""Auto-isolate every test from real config directories and git identity."""

import os

import pytest

from git_scan.sdk import gates


@pytest.fixture(autouse=True)
def person_at_the_terminal(monkeypatch):
    """Gate-loosening commands ask the person at the terminal to confirm;
    there is none under pytest, so a stand-in says yes. A test of the
    confirmation itself sets ``gates.confirm_override`` to False or None."""
    monkeypatch.setattr(gates, "confirm_override", True)


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    """Redirect XDG_CONFIG_HOME so no test reads real user config, and give
    every test a user layer that declares (empty) personal patterns, so the
    personal-patterns check passes unless a test removes it.

    Git's global config is pointed at a temp file with a sandbox identity so
    the derived patterns (git name and its parts, email) and identity checks
    never see the developer's own. The identity's words are ones fixture
    content never uses by accident: its parts are judgment entries that
    match regardless of case, so "Test User" would make every "test" in a
    fixture a judgment match. HOME is a temp directory named after a system
    account, so the developer's home directory name is not derived either.
    """
    test_config = tmp_path / "xdg_config"
    (test_config / "git-scan").mkdir(parents=True)
    (test_config / "git-scan" / "git-scan.yaml").write_text("patterns: []\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(test_config))

    global_git = tmp_path / "gitconfig"
    global_git.write_text("[user]\n\tname = Quill Sandboxer\n\temail = quill@example.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_git))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    home = tmp_path / "root"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    # Config injected through the environment (GIT_CONFIG_COUNT with
    # GIT_CONFIG_KEY_n / GIT_CONFIG_VALUE_n) is read before any file;
    # hosted environments use it for URL rewrites and credential settings.
    for var in list(os.environ):
        if var == "GIT_CONFIG_COUNT" or var.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("USER", "testuser")
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME",
                "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(var, raising=False)
