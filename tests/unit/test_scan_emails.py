"""Email address detection in staged content."""

import subprocess
from git_scan.sdk.scanner import run_scan


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    return repo


def test_example_domain_not_flagged(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "docs.md").write_text("Contact: user@example.com")
    subprocess.run(["git", "-C", str(repo), "add", "docs.md"], check=True)

    report = run_scan(str(repo), only_step="emails")
    check = next(c for c in report.checks if c.name == "Email addresses")
    assert check.passed


def test_real_email_domain_flagged(tmp_path):
    repo = _init_repo(tmp_path)
    email = "somebody" + "@" + "gmail.com"
    (repo / "notes.txt").write_text(f"Contact: {email}")
    subprocess.run(["git", "-C", str(repo), "add", "notes.txt"], check=True)

    report = run_scan(str(repo), only_step="emails")
    check = next(c for c in report.checks if c.name == "Email addresses")
    assert not check.passed
    assert any("gmail" in f for f in check.findings)


def test_python_decorator_not_flagged(tmp_path):
    """Decorator-like patterns (e.g., @click.command) should not trigger."""
    repo = _init_repo(tmp_path)
    (repo / "app.py").write_text("@click.command()\ndef main(): pass")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)

    report = run_scan(str(repo), only_step="emails")
    check = next(c for c in report.checks if c.name == "Email addresses")
    assert check.passed
