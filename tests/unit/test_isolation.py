"""The test sandbox hides the developer's machine: config, identity, hooks.

These tests fail if conftest's isolation ever regresses. Without it, tests
would read the developer's real personal patterns, commit under their real
identity, and run whatever global pre-commit hook the machine has
installed, which makes the suite slow and machine-dependent.
"""

import os
import subprocess
import tempfile
import time
from pathlib import Path

from git_scan.sdk.config import user_config_path, layer_paths
from git_scan.sdk.hooks import default_hooks_dir

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


def test_git_reads_no_config_outside_the_sandbox(tmp_path):
    repo = _repo(tmp_path)
    origins = _git(repo, "config", "--list", "--show-origin")
    for line in origins.splitlines():
        origin = line.split("\t", 1)[0]
        inside = str(tmp_path) in origin or origin == "file:.git/config"
        assert inside, f"git read config from outside the sandbox: {origin}"


def test_machine_hooks_path_is_not_visible(tmp_path):
    repo = _repo(tmp_path)
    res = subprocess.run(["git", "-C", str(repo), "config", "--get", "core.hooksPath"],
                         capture_output=True, text=True)
    assert res.returncode == 1 and res.stdout == ""


def test_commit_without_no_verify_runs_no_external_hook(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("x\n")
    _git(repo, "add", ".")
    start = time.monotonic()
    out = subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "x"],
                         capture_output=True, text=True)
    elapsed = time.monotonic() - start
    assert out.returncode == 0, out.stderr
    assert out.stdout == "" and out.stderr == ""      # a scanner hook would print
    assert elapsed < 3, f"commit took {elapsed:.1f}s; is a machine hook running?"


def test_commit_identity_is_the_sandbox_identity(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "x")
    assert _git(repo, "log", "-1", "--format=%an <%ae>").strip() == "Test User <test@example.com>"


def test_config_and_hook_paths_point_into_the_sandbox(tmp_path):
    assert str(user_config_path()).startswith(str(tmp_path))
    assert str(default_hooks_dir()).startswith(str(tmp_path))
    assert str(layer_paths(str(tmp_path))["user"]).startswith(str(tmp_path))
    assert os.environ["USER"] == "testuser"
    assert "GIT_CONFIG_GLOBAL" in os.environ


def test_temp_dirs_live_under_the_system_temp_base(tmp_path):
    """pytest prunes these (it keeps the 3 most recent runs), so a killed
    run leaves nothing permanent, and nothing is written inside the repo."""
    assert str(tmp_path.resolve()).startswith(str(Path(tempfile.gettempdir()).resolve()))
    assert not str(tmp_path.resolve()).startswith(str(REPO_ROOT))
