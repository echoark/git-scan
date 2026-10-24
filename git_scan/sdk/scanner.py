"""Core scanner orchestration — coordinates all steps."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from .config import load_config, ConfigError, read_missing_config_help, user_config_path
from .sources import collect, ScanInput, RevisionError
from .utils import CheckResult
from .steps import run_all_steps, get_step_names
from . import marks


class InvalidStepError(ValueError):
    pass


class ScanUsageError(ValueError):
    """Scan options that can't be combined; the message says which."""


@dataclass
class ScanReport:
    """Complete scan report with all check results."""
    repo_path: str
    checks: List[CheckResult] = field(default_factory=list)
    refusal: List[str] = field(default_factory=list)   # set: no checks ran
    scope: str = "staged"                                # staged | message | commits
    commits: List[str] = field(default_factory=list)     # commits read (commits scope)
    marked: List[str] = field(default_factory=list)      # of those, marked 'scanned'

    @property
    def failed(self) -> bool:
        return bool(self.refusal) or any(not c.passed and not c.skipped for c in self.checks)

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


def _check_personal_patterns(config) -> CheckResult:
    """Reached only when the user layer defines ``patterns`` (even empty)."""
    return CheckResult("Personal patterns", True,
                       info=f"{config.pattern_counts['personal']} configured")


def _not_configured_refusal(config) -> List[str]:
    """Why a full scan won't run without the user layer, and what to do."""
    path = user_config_path()
    if config.layers.get("user") is None:
        first = f"git-scan: no user config on this machine ({path}), so"
    else:
        first = f"git-scan: the user config ({path}) has no personal patterns, so"
    lines = [first, "nothing personal can be checked. Commit refused."]
    help_text = read_missing_config_help()
    if help_text:
        lines += help_text.splitlines()
    else:
        lines += ["To set it up: git-scan patterns add ID --pattern REGEX",
                  "To declare you have none: git-scan patterns clear"]
    return lines


def _failing(results: Iterable[CheckResult]) -> bool:
    return any(not c.passed and not c.skipped for c in results)


def _clean_commits(repo_path: str, config, scan_input: ScanInput) -> List[str]:
    """After a failed scan of commits: the ones whose own lines pass.

    Names belong to no commit, so a names finding leaves none clean.
    Otherwise failing groups are halved until each finding is pinned to
    its commit, so a few bad commits among many cost a few extra passes
    over small inputs, not one scan per commit.
    """
    names_only = ScanInput(content=[], names=scan_input.names, scope=scan_input.scope,
                           remote=scan_input.remote)
    if _failing(run_all_steps(repo_path, config=config, scan_input=names_only)):
        return []
    clean: List[str] = []

    def visit(group: List[str]) -> None:
        if not group:
            return
        if not _failing(run_all_steps(repo_path, config=config,
                                      scan_input=scan_input.subset(group))):
            clean.extend(group)
        elif len(group) > 1:
            visit(group[:len(group) // 2])
            visit(group[len(group) // 2:])

    commits = scan_input.commits
    visit(commits[:len(commits) // 2])
    visit(commits[len(commits) // 2:])
    return clean


def run_scan(
    repo_path: str = ".",
    deep: bool = False,
    only_step: Optional[str] = None,
    include_untracked: bool = False,
    include_unstaged: bool = False,
    on_check_complete: Optional[callable] = None,
    commit: Optional[str] = None,
    message: Optional[str] = None,
    rev_range: Optional[str] = None,
    commits: Optional[List[str]] = None,
    remote: Optional[str] = None,
) -> ScanReport:
    """Run all checks on a repository.

    Args:
        repo_path: Path to git repository
        deep: Reserved for history scanning (not implemented in ``run``)
        only_step: If specified, run only this step
        include_untracked: Also scan untracked files
        include_unstaged: Also scan unstaged changes
        on_check_complete: Optional callback(check_result, current, total)
        commit: Scan this commit (patch, message, identities) instead of the staged diff
        message: Scan this commit message text instead of the staged diff
        rev_range: Scan every commit in this range (``A..B``) instead of the staged diff
        commits: Scan exactly these commits instead of the staged diff
        remote: The remote the commits are for (identity rules), if known

    A full scan that passes is remembered for the push gate: a staged scan
    records the staged tree (the post-commit hook marks the commit made
    from it), and a scan of commits marks each clean commit ``scanned``. A
    refused message drops the staged record, so the commit it belongs to
    can't be marked by a retry that skips the hooks.
    """
    if only_step is not None and only_step not in set(get_step_names()):
        raise InvalidStepError(
            f"Invalid step: '{only_step}'. Valid: {sorted(get_step_names())}"
        )
    chosen = [n for n, v in (("commit", commit), ("message", message), ("range", rev_range),
                             ("commits", commits)) if v is not None]
    if len(chosen) > 1:
        raise ScanUsageError(f"choose one of {', '.join(chosen)}: each replaces the staged diff")
    if chosen and (include_unstaged or include_untracked):
        raise ScanUsageError(f"unstaged and untracked apply to the staged diff, not to {chosen[0]}")

    report = ScanReport(repo_path=repo_path)

    result = _check_git_repo(repo_path)
    report.checks.append(result)
    if not result.passed:
        return report

    try:
        config = load_config(repo_path)
    except ConfigError as e:
        report.checks.append(CheckResult("Configuration", False, str(e).splitlines()))
        return report

    if only_step is None:
        if not config.user_patterns_defined:
            report.checks = []
            report.refusal = _not_configured_refusal(config)
            return report
        report.checks.append(_check_personal_patterns(config))

    commit_sha = None
    if commit:
        res = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"],
                             cwd=repo_path, capture_output=True, text=True)
        commit_sha = res.stdout.strip()
        if not commit_sha:
            report.checks.append(CheckResult("Commit", False, [f"'{commit}' is not a commit"]))
            return report

    try:
        scan_input: ScanInput = collect(
            repo_path, include_unstaged=include_unstaged,
            include_untracked=include_untracked, commit=commit_sha, message=message,
            rev_range=rev_range, commits=commits, remote=remote,
        )
    except RevisionError as e:
        report.checks.append(CheckResult("Range", False, str(e).splitlines()))
        return report
    report.scope = scan_input.scope
    report.commits = scan_input.commits

    step_results = run_all_steps(
        repo_path, config=config, scan_input=scan_input, deep=deep,
        only_step=only_step, include_untracked=include_untracked,
    )

    first = len(report.checks)
    total = first + len(step_results)
    if on_check_complete:
        for i, c in enumerate(report.checks, start=1):
            on_check_complete(c, i, total)

    for i, result in enumerate(step_results, start=first + 1):
        report.checks.append(result)
        if on_check_complete:
            on_check_complete(result, i, total)

    if only_step is None:
        if scan_input.scope == "commits":
            report.marked = (report.commits if not report.failed
                             else _clean_commits(repo_path, config, scan_input))
            marks.add_marks(marks.SCANNED, report.marked, repo_path)
        elif scan_input.scope == "message":
            if report.failed:
                marks.forget_passed_scan(repo_path)
        elif not report.failed:
            marks.record_passed_scan(repo_path)

    return report


def push_scan(push_lines: Iterable[str], remote: Optional[str] = None,
              required: Iterable[str] = (), repo_path: str = ".") -> marks.PushResult:
    """The push gate: scan what a push would send, and check required marks.

    Every outgoing commit not yet marked ``scanned`` is scanned in one pass
    (patch, message, author and committer); clean ones are marked, so the
    next push skips them. Required marks are checked as ``marks.push_check``
    always has.
    """
    result = marks.push_check(push_lines, remote=remote, required=required, repo_path=repo_path)
    if result.unscanned:
        result.scan = run_scan(repo_path, commits=result.unscanned, remote=remote)
    return result
