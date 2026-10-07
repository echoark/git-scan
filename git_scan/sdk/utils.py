"""Shared utilities for scanner modules."""

import subprocess
from dataclasses import dataclass, field
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
