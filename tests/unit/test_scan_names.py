"""Personal patterns in file, branch, and tag names."""

import subprocess

from git_scan.sdk.scanner import run_scan


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path, pattern="acmeproject", category="customer"):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    config = tmp_path / "xdg_config" / "git-scan"
    config.mkdir(parents=True, exist_ok=True)
    (config / "git-scan.yaml").write_text(
        f"patterns:\n  - id: p1\n    pattern: '{pattern}'\n    category: {category}\n"
    )
    (repo / "readme.txt").write_text("clean")
    _git(repo, "add", ".")
    _git(repo, "commit", "--no-verify", "-m", "init")
    return repo


def _checks(repo):
    report = run_scan(str(repo), only_step="names")
    return {c.name: c for c in report.checks if c.name != "Git repository"}


def test_clean_names_pass(tmp_path):
    checks = _checks(_repo(tmp_path))
    assert all(c.passed for c in checks.values())
    assert set(checks) == {"Patterns in file names", "Patterns in branch names",
                           "Patterns in tag names"}


def test_tracked_file_name_flagged(tmp_path):
    repo = _repo(tmp_path)
    (repo / "acmeproject-notes.md").write_text("x")
    _git(repo, "add", ".")
    _git(repo, "commit", "--no-verify", "-m", "add")
    check = _checks(repo)["Patterns in file names"]
    assert not check.passed
    assert "acmeproject-notes.md" in check.findings[0]


def test_staged_new_file_name_flagged(tmp_path):
    repo = _repo(tmp_path)
    (repo / "acmeproject.txt").write_text("x")
    _git(repo, "add", ".")
    assert not _checks(repo)["Patterns in file names"].passed


def test_branch_name_flagged_even_when_not_checked_out(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "branch", "fix-for-acmeproject")
    check = _checks(repo)["Patterns in branch names"]
    assert not check.passed
    assert "fix-for-acmeproject" in check.findings[0]


def test_tag_name_flagged(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "tag", "acmeproject-v1")
    assert not _checks(repo)["Patterns in tag names"].passed


def test_builtin_secret_categories_not_applied_to_names(tmp_path):
    repo = _repo(tmp_path, pattern="[0-9a-f]{8}", category="token")
    (repo / "deadbeef.txt").write_text("x")
    _git(repo, "add", ".")
    assert _checks(repo)["Patterns in names"].skipped


def test_no_personal_patterns_skips(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    assert _checks(repo)["Patterns in names"].skipped


def test_prose_tuned_patterns_not_applied_to_names(tmp_path):
    repo = _repo(tmp_path)
    config = tmp_path / "xdg_config" / "git-scan" / "git-scan.yaml"
    config.write_text(
        "patterns:\n  - id: co\n    pattern: 'bigco'\n    category: employer\n"
        "    not_followed_by: ['-cloud']\n"
    )
    (repo / "bigco-tasks.txt").write_text("x")
    _git(repo, "add", ".")
    assert _checks(repo)["Patterns in names"].skipped
