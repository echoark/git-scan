"""Auto-isolate every test from real config directories and git identity."""

import pytest


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    """Redirect XDG_CONFIG_HOME so no test reads real user config, and give
    every test a user layer that declares (empty) personal patterns, so the
    personal-patterns check passes unless a test removes it.

    Git's global config is pointed at an empty temp file so dynamic patterns
    (git name, email) and identity checks never see the developer's own.
    """
    test_config = tmp_path / "xdg_config"
    (test_config / "git-scan").mkdir(parents=True)
    (test_config / "git-scan" / "git-scan.yaml").write_text("patterns: []\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(test_config))

    global_git = tmp_path / "gitconfig"
    global_git.write_text("[user]\n\tname = Test User\n\temail = test@example.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_git))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("USER", "testuser")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL", "EMAIL"):
        monkeypatch.delenv(var, raising=False)
