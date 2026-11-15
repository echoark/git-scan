"""Groups of entries turned off at once, and the repositories whose own
git-scan.yaml is trusted to do that and to lift the push review."""

import subprocess

import pytest
import yaml
from click.testing import CliRunner

from git_scan.cli.main import cli
from git_scan.sdk.config import ConfigError, load_config, user_config_path

REMOTE = "github.com/octo/fleet"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _run(*args):
    return CliRunner().invoke(cli, list(args))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository whose origin is github.com/octo/fleet, with three of the
    user's own entries: two domains and a cloud id."""
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "remote", "add", "origin", f"https://{REMOTE}.git")
    monkeypatch.chdir(path)
    for id, pattern, category in (("domain-01", "octo\\\\.example", "domain"),
                                  ("domain-02", "fleet\\\\.example", "domain"),
                                  ("cloud_id-01", "octo-project-1", "cloud_id")):
        assert _run("patterns", "add", id, "--pattern", pattern, "--category", category).exit_code == 0
    return path


def active(path="."):
    return {p["id"] for p in load_config(str(path)).patterns}


def staged_scan(repo, text):
    (repo / "fleet.yaml").write_text(text)
    _git(repo, "add", "fleet.yaml")
    return _run("run")


FLEET = "project: octo-project-1\ndomain: app.octo.example\nrepo: octo/fleet\n"


# --- a group in your own config ---


def test_a_category_is_turned_off_as_one_line_and_covers_entries_added_later(repo):
    assert _run("patterns", "remove", "--category", "domain").exit_code == 0
    assert {"domain-01", "domain-02"}.isdisjoint(active()) and "cloud_id-01" in active()
    assert yaml.safe_load(user_config_path().read_text())["patterns"][-1] == {"category": "domain", "match": "ignore"}
    _run("patterns", "add", "domain-03", "--pattern", "later\\.example", "--category", "domain")
    assert "domain-03" not in active()


def test_an_id_with_a_wildcard_is_a_group(repo):
    assert _run("patterns", "remove", "cloud_id-*").exit_code == 0
    assert "cloud_id-01" not in active() and "domain-01" in active()


def test_restore_turns_a_group_back_on(repo):
    _run("patterns", "remove", "--category", "domain")
    assert _run("patterns", "restore", "--category", "domain").exit_code == 0
    assert {"domain-01", "domain-02"} <= active()
    assert "not turned off" in _run("patterns", "restore", "--category", "domain").output


def test_an_entry_named_by_id_beats_the_group_it_is_in(repo):
    _run("patterns", "remove", "--category", "domain")
    _run("patterns", "edit", "domain-01", "--match", "judgment")
    assert "domain-01" in active() and "domain-02" not in active()


def test_built_in_entries_are_never_part_of_a_group(repo):
    assert _run("patterns", "remove", "--layer", "built-in").exit_code != 0
    _run("patterns", "remove", "*")
    assert "github-pat" in active() and "domain-01" not in active()


def test_a_group_needs_exactly_one_selector(repo):
    assert _run("patterns", "remove").exit_code != 0
    assert _run("patterns", "remove", "domain-01", "--category", "domain").exit_code != 0
    assert "domain-01" in active()


# --- a group in a repository's own file ---


def test_a_repository_cannot_turn_off_a_group_until_you_trust_it(repo):
    refused = _run("patterns", "remove", "--layer", "user", "--project")
    assert refused.exit_code != 0 and "git-scan repos trust" in refused.output
    assert not (repo / "git-scan.yaml").exists()


def test_a_group_written_by_hand_in_an_untrusted_repository_fails_the_scan(repo):
    (repo / "git-scan.yaml").write_text("patterns:\n- layer: user\n  match: ignore\n")
    with pytest.raises(ConfigError, match="git-scan repos trust"):
        load_config(str(repo))
    result = staged_scan(repo, FLEET)
    assert result.exit_code == 1 and "git-scan repos trust" in result.output


def test_a_trusted_repository_turns_off_everything_personal_and_keeps_the_built_in_checks(repo):
    assert staged_scan(repo, FLEET).exit_code == 1                 # the user's own entries refuse it
    assert _run("repos", "trust", REMOTE).exit_code == 0
    assert _run("patterns", "remove", "--layer", "user", "--project").exit_code == 0
    assert _run("patterns", "remove", "--layer", "environment", "--project").exit_code == 0
    assert yaml.safe_load((repo / "git-scan.yaml").read_text()) == {"patterns": [
        {"layer": "user", "match": "ignore"}, {"layer": "environment", "match": "ignore"}]}
    assert active().isdisjoint({"domain-01", "domain-02", "cloud_id-01", "git-email", "git-name-full"})
    clean = staged_scan(repo, FLEET)
    assert clean.exit_code == 0, clean.output
    leaked = staged_scan(repo, FLEET + "token: ghp_" + "a" * 36 + "\n")
    assert leaked.exit_code == 1 and "[token]" in leaked.output


def test_trust_covers_an_owner_by_prefix_and_not_a_lookalike(repo):
    (repo / "git-scan.yaml").write_text("patterns:\n- category: domain\n  match: ignore\n")
    _run("repos", "trust", "github.com/oct")
    with pytest.raises(ConfigError):
        load_config(str(repo))
    _run("repos", "trust", "github.com/octo")
    assert "domain-01" not in active(repo)


def test_another_repository_is_untouched(repo, tmp_path):
    _run("repos", "trust", REMOTE)
    _run("patterns", "remove", "--layer", "user", "--project")
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q", "-b", "main")
    _git(other, "remote", "add", "origin", "https://github.com/octo/app.git")
    assert {"domain-01", "domain-02", "cloud_id-01"} <= active(other)


def test_a_repository_cannot_name_itself_as_trusted(repo):
    (repo / "git-scan.yaml").write_text(f"trusted_repos:\n- {REMOTE}\n")
    with pytest.raises(ConfigError, match="a repository cannot name itself"):
        load_config(str(repo))


# --- the push review ---


def test_only_a_trusted_repository_can_say_no_review_is_needed(repo):
    _run("config", "require-review", "on")
    (repo / "git-scan.yaml").write_text("push:\n  require_review: false\n")
    assert load_config(str(repo)).review_required is True
    _run("repos", "trust", REMOTE)
    assert load_config(str(repo)).review_required is False
    _run("repos", "untrust", REMOTE)
    assert load_config(str(repo)).review_required is True


def test_a_trusted_repository_that_says_nothing_still_needs_the_review(repo):
    _run("config", "require-review", "on")
    _run("repos", "trust", REMOTE)
    assert load_config(str(repo)).review_required is True


# --- the list ---


def test_repos_trust_list_and_untrust(repo):
    assert "none" in _run("repos", "list").output
    _run("repos", "trust", REMOTE + "/")
    _run("repos", "trust", REMOTE)
    assert _run("repos", "list").output.split() == [REMOTE]
    assert _run("repos", "trust", "https://github.com/octo/fleet").exit_code != 0
    assert _run("repos", "untrust", REMOTE).exit_code == 0
    assert "trusted_repos" not in yaml.safe_load(user_config_path().read_text())
    assert _run("repos", "untrust", REMOTE).exit_code != 0
