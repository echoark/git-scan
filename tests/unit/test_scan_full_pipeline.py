"""Full pipeline tests — run_scan with all steps against a real repo."""

import subprocess
from click.testing import CliRunner
from git_scan.sdk.scanner import run_scan, InvalidStepError
from git_scan.cli.main import cli
import pytest

# Build via concatenation
TEST_SSN = "123" + "-" + "45" + "-" + "6789"


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    return repo


def test_clean_repo_all_steps_pass(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "readme.md").write_text("# My Project\n\nNothing sensitive.")
    subprocess.run(["git", "-C", str(repo), "add", "readme.md"], check=True)

    report = run_scan(str(repo))
    assert not report.failed
    assert report.passed_count > 5  # multiple steps ran


def test_dirty_repo_fails(tmp_path):
    repo = _init_repo(tmp_path)
    fake_pat = "ghp_" + "a" * 36
    (repo / "config.txt").write_text(f"token: {fake_pat}\nSSN: {TEST_SSN}")
    subprocess.run(["git", "-C", str(repo), "add", "config.txt"], check=True)

    report = run_scan(str(repo))
    assert report.failed
    assert report.failed_count >= 2  # OAuth + SSN at minimum


def test_invalid_step_raises(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    with pytest.raises(InvalidStepError):
        run_scan(str(repo), only_step="bogus_module")


def test_not_a_git_repo_fails(tmp_path):
    """Running on a non-git directory reports failure."""
    report = run_scan(str(tmp_path))
    git_check = next(c for c in report.checks if c.name == "Git repository")
    assert not git_check.passed


def test_multiple_checks_report_independently(tmp_path):
    """Each check reports its own findings in the combined report."""
    repo = _init_repo(tmp_path)
    fake_pat = "ghp_" + "a" * 36
    email = "somebody" + "@" + "gmail.com"
    (repo / "mixed.txt").write_text(f"token: {fake_pat}\nContact: {email}")
    subprocess.run(["git", "-C", str(repo), "add", "mixed.txt"], check=True)

    report = run_scan(str(repo))
    check_names = [c.name for c in report.checks if not c.passed and not c.skipped]
    # Should have failures from at least patterns and emails
    assert len(check_names) >= 2


def test_cli_exit_zero_on_clean_repo(tmp_path):
    """CLI exits 0 when scan finds nothing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    (repo / "clean.txt").write_text("Nothing sensitive here")
    subprocess.run(["git", "-C", str(repo), "add", "clean.txt"], check=True)

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--step", "ssn_ein", str(repo)])
    assert result.exit_code == 0


@pytest.mark.xfail(strict=True, reason="history scanning is planned for 'git-scan audit'")
def test_deep_mode_finds_secrets_in_history(tmp_path):
    """deep=True should detect sensitive content that was committed then removed."""
    repo = _init_repo(tmp_path)

    # Commit sensitive content
    fake_pat = "ghp_" + "a" * 36
    (repo / "config.txt").write_text(f"token: {fake_pat}")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--no-verify", "-m", "add secret"],
        check=True, capture_output=True,
    )

    # Replace with clean content and commit again
    (repo / "config.txt").write_text("Now clean, secret removed")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--no-verify", "-m", "remove secret"],
        check=True, capture_output=True,
    )

    # Nothing staged — shallow scan should pass
    report = run_scan(str(repo), deep=False)
    assert not report.failed, "Shallow scan should pass when nothing is staged"

    # Deep scan should find the old secret in history
    report = run_scan(str(repo), deep=True)
    assert report.failed, "Deep scan should find secret in git history"


def test_cli_exit_nonzero_on_finding(tmp_path):
    """CLI exits non-zero when scan detects sensitive data."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    ssn = "123" + "-" + "45" + "-" + "6789"
    (repo / "data.txt").write_text(f"SSN: {ssn}")
    subprocess.run(["git", "-C", str(repo), "add", "data.txt"], check=True)

    runner = CliRunner()
    result = runner.invoke(cli, ["run", "--step", "ssn_ein", str(repo)])
    assert result.exit_code != 0
