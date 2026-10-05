"""Core scanner orchestration — coordinates all steps."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .config import load_config, MergedConfig
from .utils import CheckResult
from .steps import run_all_steps, get_step_names


class InvalidStepError(ValueError):
    pass


@dataclass
class ScanReport:
    """Complete scan report with all check results."""
    repo_path: str
    checks: List[CheckResult] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(not c.passed and not c.skipped for c in self.checks)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed and not c.skipped)

    def summary(self) -> str:
        lines = [f"Scan: {self.repo_path}",
                 f"Passed: {self.passed_count}, Failed: {self.failed_count}"]
        for c in self.checks:
            status = "PASS" if c.passed else ("SKIP" if c.skipped else "FAIL")
            lines.append(f"  [{status}] {c.name}")
            for f in c.findings[:5]:
                lines.append(f"    - {f}")
            if len(c.findings) > 5:
                lines.append(f"    ... and {len(c.findings) - 5} more")
        return "\n".join(lines)


def _check_git_repo(repo_path: str) -> CheckResult:
    """Verify path is a git repository."""
    git_dir = Path(repo_path) / ".git"
    if git_dir.exists():
        return CheckResult("Git repository", True)
    if os.environ.get("GIT_DIR"):
        return CheckResult("Git repository", True)
    return CheckResult("Git repository", False, ["Not a git repository"])


def run_scan(
    repo_path: str = ".",
    deep: bool = False,
    only_step: Optional[str] = None,
    include_untracked: bool = False,
    on_check_complete: Optional[callable] = None,
) -> ScanReport:
    """Run all checks on a repository.

    Args:
        repo_path: Path to git repository
        deep: If True, also scan git history (slower)
        only_step: If specified, run only this step
        include_untracked: If True, also scan untracked files
        on_check_complete: Optional callback(check_result, current, total)
    """
    if only_step is not None and only_step not in set(get_step_names()):
        raise InvalidStepError(
            f"Invalid step: '{only_step}'. Valid: {sorted(get_step_names())}"
        )

    config = load_config(repo_path)
    report = ScanReport(repo_path=repo_path)

    # Check it's a git repo first
    result = _check_git_repo(repo_path)
    report.checks.append(result)
    if not result.passed:
        return report

    # Run all steps
    step_results = run_all_steps(
        repo_path, config=config, deep=deep,
        only_step=only_step, include_untracked=include_untracked,
    )

    total = 1 + len(step_results)
    if on_check_complete:
        on_check_complete(result, 1, total)

    for i, result in enumerate(step_results, start=2):
        report.checks.append(result)
        if on_check_complete:
            on_check_complete(result, i, total)

    return report
