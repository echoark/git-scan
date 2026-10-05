"""SSN and EIN detection in staged content."""

import subprocess
from git_scan.sdk.scanner import run_scan

# Build via concatenation to avoid literal patterns in source
TEST_SSN = "123" + "-" + "45" + "-" + "6789"
TEST_EIN = "12" + "-" + "3456789"


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    return repo


def test_ssn_detected_in_staged(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "data.txt").write_text(f"SSN: {TEST_SSN}")
    subprocess.run(["git", "-C", str(repo), "add", "data.txt"], check=True)

    report = run_scan(str(repo), only_step="ssn_ein")
    check = next(c for c in report.checks if c.name == "SSN/EIN patterns")
    assert not check.passed
    assert any(TEST_SSN in f for f in check.findings)


def test_ein_detected_in_staged(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "data.txt").write_text(f"EIN: {TEST_EIN}")
    subprocess.run(["git", "-C", str(repo), "add", "data.txt"], check=True)

    report = run_scan(str(repo), only_step="ssn_ein")
    check = next(c for c in report.checks if c.name == "SSN/EIN patterns")
    assert not check.passed
    assert any(TEST_EIN in f for f in check.findings)


def test_clean_content_passes(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "clean.txt").write_text("No sensitive numbers here 42")
    subprocess.run(["git", "-C", str(repo), "add", "clean.txt"], check=True)

    report = run_scan(str(repo), only_step="ssn_ein")
    check = next(c for c in report.checks if c.name == "SSN/EIN patterns")
    assert check.passed
