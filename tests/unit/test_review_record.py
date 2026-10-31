"""Recording a privacy review from the reviewer's result: the core layer
directly, then the command and the hooks it feeds."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from git_scan.cli.main import cli
from git_scan.sdk import marks, review


def _git(repo, *args, check=True, input=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True, input=input)


def _repo_with_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", str(remote))
    return repo


def _commit(repo, name):
    (repo / name).write_text("clean\n")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


REPORT = """# Privacy Guard report

## Findings
None.

```privacy-guard-result
{
  "status": "completed",
  "findings": [],
  "commits": ["%s"],
  "summary": "clean"
}
```
"""


def test_result_is_read_from_the_fenced_block_of_a_full_report():
    data = review.load_result(REPORT % "abc123")
    assert data["status"] == "completed" and data["commits"] == ["abc123"]


def test_bare_json_and_garbage():
    assert review.load_result('{"status": "completed", "findings": []}')["findings"] == []
    with pytest.raises(review.ReviewError, match="no JSON found"):
        review.load_result("Findings: none.")
    with pytest.raises(review.ReviewError, match="expected a JSON object"):
        review.load_result("[1, 2]")


def test_record_marks_the_tip_when_the_result_covers_every_unpushed_commit(tmp_path, monkeypatch):
    repo = _repo_with_remote(tmp_path)
    monkeypatch.chdir(repo)
    first = _commit(repo, "a.txt")
    second = _commit(repo, "b.txt")
    done = review.record(review.load_result(REPORT % f'", "'.join([first[:12], second[:12]])))
    assert done == {"sha": second, "covered": 2}
    assert marks.marks_on(second) == {"reviewed"}
    assert marks.marks_on(first) == set()          # one record on the tip covers both


def test_record_refuses_when_nothing_is_unpushed(tmp_path, monkeypatch):
    repo = _repo_with_remote(tmp_path)
    monkeypatch.chdir(repo)
    _commit(repo, "a.txt")
    _git(repo, "push", "-q", "origin", "main")
    with pytest.raises(review.ReviewError, match="no unpushed commits"):
        review.record({"status": "completed", "findings": [], "commits": ["whatever"]})


def test_record_command_reads_a_file_holding_the_whole_report(tmp_path, monkeypatch):
    repo = _repo_with_remote(tmp_path)
    monkeypatch.chdir(repo)
    sha = _commit(repo, "a.txt")
    report = tmp_path / "report.md"
    report.write_text(REPORT % sha[:12])
    r = CliRunner().invoke(cli, ["review", "record", str(report)])
    assert r.exit_code == 0, r.output
    assert f"recorded a clean review of 1 unpushed commit(s) on {sha[:12]}" in r.output
    r = CliRunner().invoke(cli, ["review", "record", str(tmp_path / "missing.md")])
    assert r.exit_code == 2


def test_require_review_is_a_config_setting_with_on_and_off(tmp_path, monkeypatch):
    repo = _repo_with_remote(tmp_path)
    monkeypatch.chdir(repo)
    run = lambda *a: CliRunner().invoke(cli, list(a))
    assert run("config", "require-review").output.strip() == "off"
    assert run("config", "require-review", "on").exit_code == 0
    assert run("config", "require-review").output.strip() == "on"
    from git_scan.sdk.config import load_config
    assert load_config(".").review_required is True
    assert run("config", "require-review", "off").exit_code == 0
    assert load_config(".").review_required is False
    assert run("config", "require-review", "maybe").exit_code == 1
