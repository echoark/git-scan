"""MCP server — thin wrapper around SDK."""

from typing import Any, Optional

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # the mcp extra is optional so the hook stays lean
    raise SystemExit(
        "git-scan-mcp needs the 'mcp' extra: "
        'pipx install "git-scan[mcp] @ git+https://github.com/echoark/git-scan.git@<tag>" --force'
    )

from git_scan.sdk.scanner import run_scan as sdk_run_scan
from git_scan.sdk.steps import get_step_names
from git_scan.sdk.hooks import status as hook_status_sdk

mcp = FastMCP("git-scan")


@mcp.tool()
async def scan_repo(
    repo_path: str = ".",
    deep: bool = False,
    only_step: Optional[str] = None,
    include_untracked: bool = False,
    include_unstaged: bool = False,
) -> dict[str, Any]:
    """Scan a git repository for sensitive data.

    Reads the staged diff (added and removed lines) plus every tracked file,
    branch, and tag name, and runs every check: configured patterns,
    tokens and keys, entropy, emails, SSN/EIN, Drive IDs, dollar amounts,
    the user's own names from the environment, and git identity. The same
    scan the pre-commit hook runs; call it before committing, or on demand.

    Config is loaded from three layers (package defaults, user
    ~/.config/git-scan/git-scan.yaml, project ./git-scan.yaml).

    Args:
        repo_path: Path to the git repository to scan.
        deep: If True, also scan git history (slower).
        only_step: Run only this scanner step. Use list_steps for valid names.
        include_untracked: If True, also scan untracked files.
        include_unstaged: If True, also scan unstaged changes.
    """
    report = sdk_run_scan(
        repo_path=repo_path,
        deep=deep,
        only_step=only_step,
        include_untracked=include_untracked,
        include_unstaged=include_unstaged,
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


@mcp.tool()
async def hook_status(repo_path: str = ".") -> dict[str, Any]:
    """Report the git-scan pre-commit hook at each level, and which one git runs.

    States are exact matches against scripts git-scan has shipped:
    ``installed`` (current), ``outdated`` (an earlier git-scan script),
    ``other`` (a hook git-scan did not write, or an edited one; its content
    is not inspected), or ``absent``. ``effective`` is where git runs
    pre-commit hooks from for this repository: the ``core.hooksPath``
    directory if set, else the repository's own ``.git/hooks``.

    Args:
        repo_path: A path inside the repository to report on. Outside a
            repository only the global level is reported.
    """
    return hook_status_sdk(repo_path=repo_path)


def main() -> None:
    """Entry point for the ``git-scan-mcp`` command: serve over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
