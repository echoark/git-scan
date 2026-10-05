"""Dollar amount detection in staged content."""

import subprocess
from git_scan.sdk.scanner import run_scan
from git_scan.sdk.steps.amounts import is_acceptable

# Build via math to avoid literals
TEST_LARGE = 500 * 1000
TEST_CENTS = 3847 + 0.23


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    return repo


def test_large_amount_detected(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "data.txt").write_text(f"Total: ${TEST_LARGE:,.2f}")
    subprocess.run(["git", "-C", str(repo), "add", "data.txt"], check=True)

    report = run_scan(str(repo), only_step="amounts")
    large = next(c for c in report.checks if c.name.startswith("Large amounts"))
    assert not large.passed


def test_amount_with_cents_detected(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "payroll.txt").write_text(f"Net pay: ${TEST_CENTS:,.2f}")
    subprocess.run(["git", "-C", str(repo), "add", "payroll.txt"], check=True)

    report = run_scan(str(repo), only_step="amounts")
    cents = next(c for c in report.checks if "cents" in c.name.lower())
    assert not cents.passed


def test_irs_amounts_acceptable():
    assert is_acceptable("$176,100")
    assert is_acceptable("176,100")
    assert not is_acceptable(f"${175 * 1000 + 432:,}")
