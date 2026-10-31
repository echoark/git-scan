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


def test_system_account_names_are_not_personal(tmp_path, monkeypatch):
    """A container or CI runner logged in as root (USER unset, HOME=/root)
    must not turn the word "root" into a personal pattern."""
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "root"))
    repo = _init_repo(tmp_path)
    (repo / "docs.md").write_text("Run it from the repo root; the MCP endpoint is the root path.")
    subprocess.run(["git", "-C", str(repo), "add", "docs.md"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert check.passed, check.findings


def test_personal_home_dir_name_still_detected(tmp_path, monkeypatch):
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "jdevtester"))
    repo = _init_repo(tmp_path)
    (repo / "config.txt").write_text("cache: /Users/jdevtester/.cache")
    subprocess.run(["git", "-C", str(repo), "add", "config.txt"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert not check.passed


def test_git_name_comes_from_commit_identity_not_global_config(tmp_path, monkeypatch):
    """GIT_AUTHOR_NAME decides who commits, so it, not the global config's
    user.name, is the name to protect. A hosted environment that writes a
    placeholder name into the global config must not make that placeholder
    a personal pattern."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Jane Devtester")
    repo = _init_repo(tmp_path)
    # The sandbox's global config says "Test User"; the environment wins.
    (repo / "notes.md").write_text("Test User guide.\n")
    subprocess.run(["git", "-C", str(repo), "add", "notes.md"], check=True)
    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert check.passed, check.findings

    (repo / "notes.md").write_text("Written by Jane Devtester.\n")
    subprocess.run(["git", "-C", str(repo), "add", "notes.md"], check=True)
    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert not check.passed


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


def _scan_text(tmp_path, text):
    repo = _init_repo(tmp_path)
    (repo / "notes.txt").write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    report = run_scan(str(repo), only_step="patterns")
    return next(c for c in report.checks if c.name == "Patterns")


def test_generic_four_group_api_key_detected(tmp_path):
    key = "-".join(["Ab3dEf7h", "Q2w9Zx4k", "Lm8nPq1r", "St5uVw6y"])
    assert not _scan_text(tmp_path, f"key = {key}").passed


def test_three_groups_not_flagged_as_api_key(tmp_path):
    text = "-".join(["Ab3dEf7h", "Q2w9Zx4k", "Lm8nPq1r"])
    assert _scan_text(tmp_path, f"ref = {text}").passed


def test_uuid_underscore_detected(tmp_path):
    uuid = "_".join(["3f2b8c1a", "9d4e", "4a7b", "8c6d", "1e2f3a4b5c6d"])
    assert not _scan_text(tmp_path, f"id = {uuid}").passed


def _judgment_repo(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "git-scan.yaml").write_text(
        "patterns:\n"
        "  - id: town\n"
        "    pattern: Springfield\n"
        "    category: city\n"
        "    judgment: true\n"
        "    note: only beside an address\n"
    )
    return repo


def test_judgment_match_is_not_a_finding_but_needs_acknowledgement(tmp_path):
    repo = _judgment_repo(tmp_path)
    (repo / "notes.txt").write_text("Springfield is a common town name")
    subprocess.run(["git", "-C", str(repo), "add", "notes.txt"], check=True)

    report = run_scan(str(repo), only_step="patterns")
    patterns = next(c for c in report.checks if c.name == "Patterns")
    assert patterns.passed                              # the match is not a finding
    judgment = next(c for c in report.checks if c.name == "Judgment")
    assert not judgment.passed
    text = "\n".join(judgment.findings)
    assert "notes.txt:1 [city] Springfield" in text
    assert "note: only beside an address" in text
    assert "git-scan ack" in text
    assert "1 match(es), not acknowledged" in judgment.info


def test_acknowledgement_is_bound_to_the_staged_tree(tmp_path):
    from git_scan.sdk import marks
    repo = _judgment_repo(tmp_path)
    (repo / "notes.txt").write_text("Springfield is a common town name")
    subprocess.run(["git", "-C", str(repo), "add", "notes.txt"], check=True)
    assert marks.record_ack(str(repo))

    judgment = next(c for c in run_scan(str(repo), only_step="patterns").checks
                    if c.name == "Judgment")
    assert judgment.passed and "1 match(es) acknowledged" in judgment.info

    (repo / "more.txt").write_text("another line")       # staging more voids it
    subprocess.run(["git", "-C", str(repo), "add", "more.txt"], check=True)
    judgment = next(c for c in run_scan(str(repo), only_step="patterns").checks
                    if c.name == "Judgment")
    assert not judgment.passed


def test_no_judgment_entries_means_no_judgment_check(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("plain\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    report = run_scan(str(repo), only_step="patterns")
    assert not any(c.name == "Judgment" for c in report.checks)


def test_ignore_case_matches_any_letter_case(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "git-scan.yaml").write_text(
        "patterns:\n"
        "  - id: me\n"
        "    pattern: jane doe\n"
        "    category: name\n"
        "    ignore_case: true\n"
        "  - id: strict\n"
        "    pattern: acme corp\n"
        "    category: customer\n"
    )
    (repo / "a.txt").write_text("Contact: Jane Doe\n")
    (repo / "b.txt").write_text("Vendor: ACME CORP\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)

    report = run_scan(str(repo), only_step="patterns")
    check = next(c for c in report.checks if c.name == "Patterns")
    assert not check.passed
    text = "\n".join(check.findings)
    assert "a.txt" in text and "Jane Doe" in text
    assert "b.txt" not in text                    # the case-sensitive entry stays strict
