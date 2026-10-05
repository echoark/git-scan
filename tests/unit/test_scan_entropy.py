"""Entropy detection in staged content, including exclusion filtering."""

import subprocess
from git_scan.sdk.scanner import run_scan


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    return repo


def test_high_entropy_token_flagged(tmp_path):
    repo = _init_repo(tmp_path)
    # Realistic high-entropy token
    secret = "xK9mB2vL" + "8nR3pQ5wT7jF" + "0hD4sA6gY1cE"
    (repo / "creds.txt").write_text(f"api_key={secret}")
    subprocess.run(["git", "-C", str(repo), "add", "creds.txt"], check=True)

    report = run_scan(str(repo), only_step="entropy_scan")
    check = next(c for c in report.checks if c.name == "Entropy scan")
    assert not check.passed
    assert any(secret[:20] in f for f in check.findings)


def test_low_entropy_code_passes(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "app.py").write_text(
        "def calculate_total_amount():\n    return sum(items)\n"
    )
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)

    report = run_scan(str(repo), only_step="entropy_scan")
    check = next(c for c in report.checks if c.name == "Entropy scan")
    assert check.passed


def test_sha512_in_lockfile_suppressed_by_exclusion(tmp_path):
    """Entropy exclusion rules suppress sha512 hashes in lockfiles."""
    repo = _init_repo(tmp_path)

    # Realistic package-lock.json integrity hash
    sha = "sha512-" + "xiqMQR4xAeH" + "TuB9uWmfFRcIO" + "gKBMiOBPnLzR0"
    content = f'{{"integrity": "{sha}"}}'
    (repo / "package-lock.json").write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", "package-lock.json"], check=True)

    report = run_scan(str(repo), only_step="entropy_scan")
    check = next(c for c in report.checks if c.name == "Entropy scan")
    # Should pass because the exclusion rule matches
    assert check.passed
    assert "excluded" in (check.info or "")


def test_entropy_disabled_via_project_config(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "git-scan.yaml").write_text("entropy:\n  enabled: false\n")
    secret = "xK9mB2vL" + "8nR3pQ5wT7jF" + "0hD4sA6gY1cE"
    (repo / "creds.txt").write_text(f"api_key={secret}")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)

    report = run_scan(str(repo), only_step="entropy_scan")
    check = next(c for c in report.checks if c.name == "Entropy scan")
    assert check.skipped
