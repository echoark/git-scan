"""MCP server — thin wrapper around SDK."""

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from git_scan.sdk.scanner import run_scan as sdk_run_scan
from git_scan.sdk.steps import get_step_names

mcp = FastMCP("git-scan")


@mcp.tool()
async def scan_repo(
    repo_path: str = ".",
    deep: bool = False,
    only_step: Optional[str] = None,
    include_untracked: bool = False,
) -> dict[str, Any]:
    """Scan a git repository for sensitive data.

    Checks staged file content against regex patterns, entropy detection,
    OAuth tokens, SSN/EIN, email addresses, dollar amounts, Drive IDs,
    local username, and git identity.

    The scan itself is context-agnostic — it examines staged changes
    regardless of how it was invoked. It becomes a pre-commit check,
    commit-msg check, or pre-push check when installed as a git hook
    (see install_hook). It can also be run on demand.

    Config is loaded from three layers (package defaults, user
    ~/.config/git-scan/git-scan.yaml, project ./git-scan.yaml).

    Args:
        repo_path: Path to the git repository to scan.
        deep: If True, also scan git history (slower).
        only_step: Run only this scanner step. Use list_steps for valid names.
        include_untracked: If True, also scan untracked files.
    """
    report = sdk_run_scan(
        repo_path=repo_path,
        deep=deep,
        only_step=only_step,
        include_untracked=include_untracked,
    )

    checks = []
    for c in report.checks:
        entry = {
            "name": c.name,
            "passed": c.passed,
            "findings": c.findings,
        }
        if c.skipped:
            entry["skipped"] = True
        if c.info:
            entry["info"] = c.info
        checks.append(entry)

    return {
        "passed": not report.failed,
        "passed_count": report.passed_count,
        "failed_count": report.failed_count,
        "checks": checks,
    }


@mcp.tool()
async def list_steps() -> dict[str, Any]:
    """List available scanner step names.

    Use these names with the only_step parameter of run_precommit_scan
    to run a single check type.
    """
    return {"steps": sorted(get_step_names())}
