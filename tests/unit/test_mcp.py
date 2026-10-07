"""The MCP server is a thin wrapper: its tools return the SDK's results."""

import asyncio
import subprocess

from git_scan.mcp.server import scan_repo, hook_status, list_steps
from git_scan.sdk.hooks import install_hook

FAKE_PAT = "ghp_" + "c" * 36


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    return repo


def test_scan_repo_reports_a_staged_finding(tmp_path):
    repo = _repo(tmp_path)
    (repo / "k.txt").write_text(f"{FAKE_PAT}\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    result = asyncio.run(scan_repo(repo_path=str(repo)))
    patterns = next(c for c in result["checks"] if c["name"] == "Patterns")
    assert not result["passed"] and not patterns["passed"]
    assert patterns["findings"][0].startswith("k.txt:1")


def test_scan_repo_unstaged_flag(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("clean\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    (repo / "a.txt").write_text(f"{FAKE_PAT}\n")
    assert asyncio.run(scan_repo(repo_path=str(repo)))["passed"]
    assert not asyncio.run(scan_repo(repo_path=str(repo), include_unstaged=True))["passed"]


def test_hook_status_matches_cli_facts(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    before = asyncio.run(hook_status(repo_path=str(repo)))
    assert before["global"]["state"] == "absent" and before["local"]["state"] == "absent"
    msg = install_hook()                                   # the default (sandboxed) global dir
    after = asyncio.run(hook_status(repo_path=str(repo)))
    assert after["global"]["state"] == "installed"
    assert after["effective"]["path"] == after["global"]["path"]
    assert after["global"]["path"] in msg


def test_list_steps_names_every_check_module():
    steps = asyncio.run(list_steps())["steps"]
    assert {"patterns", "names", "git_identity", "entropy_scan"} <= set(steps)
