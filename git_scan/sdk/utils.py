"""Shared utilities for scanner modules."""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class CheckResult:
    """Result of a single check."""
    name: str
    passed: bool
    findings: List[str] = field(default_factory=list)
    skipped: bool = False
    error: Optional[str] = None
    info: Optional[str] = None


def run_cmd(cmd: str, cwd: str, timeout: int = 300) -> Tuple[int, str, str]:
    """Run a shell command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=cwd,
            capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


def get_staged_files(repo_path: str) -> List[str]:
    """Get list of staged files (what will actually be committed)."""
    rc, stdout, _ = run_cmd("git diff --cached --name-only --diff-filter=d", repo_path)
    if rc != 0 or not stdout.strip():
        return []
    return [f for f in stdout.strip().split("\n") if f]


def get_staged_content(repo_path: str, file_path: str) -> Optional[str]:
    """Get staged content of a file using git show.

    This returns the content as it will appear in the commit,
    not the diff output.
    """
    rc, stdout, _ = run_cmd(f"git show ':{file_path}'", repo_path)
    if rc != 0:
        return None
    return stdout


def get_unstaged_files(repo_path: str) -> List[str]:
    """Get list of files with unstaged modifications."""
    rc, stdout, _ = run_cmd("git diff --name-only", repo_path)
    if rc != 0 or not stdout.strip():
        return []
    return [f for f in stdout.strip().split("\n") if f]


def read_working_file(repo_path: str, file_path: str) -> Optional[str]:
    """Read a file from the working tree."""
    full = Path(repo_path) / file_path
    if not full.is_file():
        return None
    try:
        return full.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
