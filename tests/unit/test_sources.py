"""What a scan reads: the staged diff, optional unstaged/untracked, and names."""

import subprocess

from git_scan.sdk.scanner import run_scan
from git_scan.sdk.sources import parse_diff, collect

FAKE_PAT = "ghp_" + "a" * 36


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    (repo / "readme.md").write_text("clean\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "--no-verify", "-m", "init")
    return repo


def _check(repo, name, **kw):
    report = run_scan(str(repo), **kw)
    return next(c for c in report.checks if c.name == name)


def test_parse_diff_reports_added_and_removed_with_line_numbers():
    diff = (
        "diff --git a.txt a.txt\n--- a.txt\n+++ a.txt\n"
        "@@ -3,2 +3,1 @@\n-old secret\n-gone\n+new line\n"
        "@@ -10 +9,2 @@\n+x\n+y\n"
    )
    lines = parse_diff(diff, "staged")
    assert [(l.kind, l.file, l.line, l.text) for l in lines] == [
        ("removed", "a.txt", 3, "old secret"),
        ("removed", "a.txt", 4, "gone"),
        ("added", "a.txt", 3, "new line"),
        ("added", "a.txt", 9, "x"),
        ("added", "a.txt", 10, "y"),
    ]
    assert lines[0].where() == "a.txt:3 (removed)"
    assert lines[2].where() == "a.txt:3"


def test_parse_diff_handles_new_deleted_and_binary_files():
    diff = (
        "diff --git new.txt new.txt\nnew file mode 100644\n--- /dev/null\n+++ new.txt\n"
        "@@ -0,0 +1 @@\n+hello\n"
        "diff --git old.txt old.txt\ndeleted file mode 100644\n--- old.txt\n+++ /dev/null\n"
        "@@ -1 +0,0 @@\n-bye\n"
        "diff --git img.png img.png\nBinary files img.png and img.png differ\n"
    )
    lines = parse_diff(diff, "staged")
    assert [(l.kind, l.file, l.text) for l in lines] == [
        ("added", "new.txt", "hello"), ("removed", "old.txt", "bye")]


def test_removed_secret_is_still_reported(tmp_path):
    repo = _repo(tmp_path)
    (repo / "cfg.txt").write_text(f"token = {FAKE_PAT}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "--no-verify", "-m", "add")
    (repo / "cfg.txt").write_text("token = removed\n")
    _git(repo, "add", ".")
    check = _check(repo, "Patterns")
    assert not check.passed
    assert "cfg.txt:1 (removed)" in check.findings[0]


def test_unchanged_lines_in_touched_file_are_not_scanned(tmp_path):
    repo = _repo(tmp_path)
    (repo / "cfg.txt").write_text(f"token = {FAKE_PAT}\nother\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "--no-verify", "-m", "add")
    (repo / "cfg.txt").write_text(f"token = {FAKE_PAT}\nother changed\n")
    _git(repo, "add", ".")
    assert _check(repo, "Patterns").passed


def test_unstaged_changes_only_with_flag(tmp_path):
    repo = _repo(tmp_path)
    (repo / "readme.md").write_text(f"clean\n{FAKE_PAT}\n")
    assert _check(repo, "Patterns").passed
    check = _check(repo, "Patterns", include_unstaged=True)
    assert not check.passed
    assert check.findings[0].startswith("[unstaged] readme.md:2")


def test_untracked_files_only_with_flag(tmp_path):
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text(f"{FAKE_PAT}\n")
    assert _check(repo, "Patterns").passed
    check = _check(repo, "Patterns", include_untracked=True)
    assert not check.passed
    assert check.findings[0].startswith("[untracked] notes.txt:1")


def test_names_are_collected(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "branch", "feature-x")
    _git(repo, "tag", "v1")
    names = {(n.source, n.text) for n in collect(str(repo)).names}
    assert {("file", "readme.md"), ("branch", "main"), ("branch", "feature-x"),
            ("tag", "v1")} <= names


def test_email_and_ssn_in_names_are_reported(tmp_path):
    repo = _repo(tmp_path)
    # Built at runtime so this source file holds no address- or SSN-shaped text.
    address = "jane.doe@" + "corp.invalid"
    ssn_tag = "doc-" + "-".join(["123", "45", "6789"])
    (repo / f"{address}.json").write_text("{}")
    _git(repo, "add", ".")
    _git(repo, "tag", ssn_tag)
    emails = _check(repo, "Email addresses")
    ssn = _check(repo, "SSN/EIN patterns")
    assert not emails.passed and f"file name: {address}.json" in emails.findings[0]
    assert not ssn.passed and f"tag: {ssn_tag}" in ssn.findings[0]
