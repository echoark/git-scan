"""Google Drive ID detection in staged content."""

import subprocess
from git_scan.sdk.scanner import run_scan


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)],
                   check=True, capture_output=True)
    return repo


def test_drive_url_id_detected(tmp_path):
    repo = _init_repo(tmp_path)
    fake_id = "1BxiMVs0XRA5" + "nFMdKvBdBZjgm" + "UUqptlbs74OgVE2upms"
    (repo / "links.md").write_text(f"https://docs.google.com/d/{fake_id}/edit")
    subprocess.run(["git", "-C", str(repo), "add", "links.md"], check=True)

    report = run_scan(str(repo), only_step="drive_ids")
    check = next(c for c in report.checks if c.name == "Google Drive IDs")
    assert not check.passed


def test_variable_name_not_triggered(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "config.py").write_text("drive_folder_id = None\n")
    subprocess.run(["git", "-C", str(repo), "add", "config.py"], check=True)

    report = run_scan(str(repo), only_step="drive_ids")
    check = next(c for c in report.checks if c.name == "Google Drive IDs")
    assert check.passed
