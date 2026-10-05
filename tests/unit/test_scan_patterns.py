"""Unified patterns step — tokens, keys, identifiers, dynamic, user-defined."""

import subprocess
from git_scan.sdk.scanner import run_scan


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    return repo


RSA_HEADER = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
OPENSSH_HEADER = "-----BEGIN " + "OPENSSH PRIVATE" + " KEY-----"


def test_rsa_private_key_detected(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "key.pem").write_text(f"{RSA_HEADER}\nfakedata\n-----END RSA PRIVATE KEY-----")
    subprocess.run(["git", "-C", str(repo), "add", "key.pem"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert not check.passed


def test_openssh_private_key_detected(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "id_ed25519").write_text(f"{OPENSSH_HEADER}\nfakedata\n-----END OPENSSH PRIVATE KEY-----")
    subprocess.run(["git", "-C", str(repo), "add", "id_ed25519"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert not check.passed


def test_aws_access_key_detected(tmp_path):
    repo = _init_repo(tmp_path)
    fake_key = "AKIA" + "A" * 16
    (repo / "creds.txt").write_text(f"aws_access_key_id = {fake_key}")
    subprocess.run(["git", "-C", str(repo), "add", "creds.txt"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert not check.passed


def test_uuid_detected(tmp_path):
    repo = _init_repo(tmp_path)
    fake_uuid = "a1b2c3d4-" + "e5f6-7890-" + "abcd-ef1234567890"
    (repo / "config.yaml").write_text(f"user_id: {fake_uuid}")
    subprocess.run(["git", "-C", str(repo), "add", "config.yaml"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert not check.passed


def test_gh_personal_access_token_detected(tmp_path):
    repo = _init_repo(tmp_path)
    fake_pat = "ghp_" + "a" * 36
    (repo / "config.txt").write_text(f"token: {fake_pat}")
    subprocess.run(["git", "-C", str(repo), "add", "config.txt"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert not check.passed


def test_google_oauth_token_detected(tmp_path):
    repo = _init_repo(tmp_path)
    fake_token = "ya29." + "x" * 40
    (repo / "creds.json").write_text(f'{{"access_token": "{fake_token}"}}')
    subprocess.run(["git", "-C", str(repo), "add", "creds.json"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert not check.passed


def test_git_username_in_content_detected(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "John Testsmith"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "john@test.com"],
                   check=True, capture_output=True)

    (repo / "notes.txt").write_text("Author: John Testsmith did this work")
    subprocess.run(["git", "-C", str(repo), "add", "notes.txt"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert not check.passed


def test_local_username_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("USER", "uniquetestuser99")
    repo = _init_repo(tmp_path)
    (repo / "config.txt").write_text("path: /home/uniquetestuser99/secrets")
    subprocess.run(["git", "-C", str(repo), "add", "config.txt"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert not check.passed


def test_license_copyright_false_positive_ignored(tmp_path):
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Jane Devtester"],
                   check=True, capture_output=True)

    (repo / "LICENSE").write_text("Copyright 2026 Jane Devtester\nMIT License\n")
    subprocess.run(["git", "-C", str(repo), "add", "LICENSE"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert check.passed or "ignored" in (check.info or "")


def test_clean_python_code_passes(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "app.py").write_text("def main():\n    print('hello')\n")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert check.passed


def test_user_pattern_from_project_config(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "git-scan.yaml").write_text(
        "patterns:\n"
        "  - id: secret-keyword\n"
        "    pattern: SUPERSECRETPROJECT\n"
    )
    (repo / "notes.txt").write_text("Working on SUPERSECRETPROJECT today")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert not check.passed


def test_local_username_absent_from_content_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("USER", "uniquetestuser99")
    repo = _init_repo(tmp_path)
    (repo / "config.txt").write_text("path: /opt/app/data")
    subprocess.run(["git", "-C", str(repo), "add", "config.txt"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert check.passed


def test_disabled_pattern_not_detected(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "git-scan.yaml").write_text(
        "patterns:\n"
        "  - id: secret-keyword\n"
        "    pattern: SUPERSECRETPROJECT\n"
        "    disabled: true\n"
    )
    (repo / "notes.txt").write_text("Working on SUPERSECRETPROJECT today")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert check.passed
