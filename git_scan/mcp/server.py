"""MCP server — thin wrapper around SDK."""

from typing import Any, Optional

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # the mcp extra is optional so the hook stays lean
    raise SystemExit(
        "git-scan-mcp needs the 'mcp' extra: "
        'pipx install "git-scan[mcp] @ git+https://github.com/echoark/git-scan.git@<tag>" --force'
    )

from git_scan.sdk.scanner import run_scan as sdk_run_scan, InvalidStepError, ScanUsageError
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
    message: Optional[str] = None,
    rev_range: Optional[str] = None,
) -> dict[str, Any]:
    """Scan a git repository for sensitive data.

    By default reads the staged diff (added and removed lines) plus every
    tracked file, branch, and tag name, and runs every check: configured
    patterns, tokens and keys, entropy, emails, SSN/EIN, Drive IDs, dollar
    amounts, the user's own names from the environment, and git identity.
    The same scan the pre-commit hook runs; call it before committing, or
    on demand.

    Two other inputs replace the staged diff (give at most one):

    - ``message``: the text of a commit message you are about to use. The
      commit-msg hook runs the same check and refuses the commit on a
      finding. Lines starting with '#' are dropped, as git drops them. A
      full 40-character commit SHA is refused like any long hex string:
      cite commits by a short SHA (12 characters).
    - ``rev_range``: ``A..B``; every commit in it, in one pass: each
      commit's own patch (a merge against its first parent), its message,
      and its author and committer emails (checked against the identity
      rules). Findings start with ``[commit <short sha>]``. The push gate
      will not scan the clean commits again; the result's ``commits`` and
      ``marked`` list them.

    Config is loaded from three layers (package defaults, user
    ~/.config/git-scan/git-scan.yaml, project ./git-scan.yaml).

    Args:
        repo_path: Path to the git repository to scan.
        deep: Reserved; has no effect.
        only_step: Run only this scanner step. Use list_steps for valid names.
        include_untracked: If True, also scan untracked files (staged scan only).
        include_unstaged: If True, also scan unstaged changes (staged scan only).
        message: Commit message text to scan instead of the staged diff.
        rev_range: Commit range (``A..B``) to scan instead of the staged diff.
    """
    try:
        report = sdk_run_scan(
            repo_path=repo_path,
            deep=deep,
            only_step=only_step,
            include_untracked=include_untracked,
            include_unstaged=include_unstaged,
            message=message,
            rev_range=rev_range,
        )
    except (InvalidStepError, ScanUsageError) as e:
        return {"passed": False, "error": str(e), "checks": []}

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

    if report.refusal:
        return {"passed": False, "refusal": "\n".join(report.refusal), "checks": []}

    result = {
        "passed": not report.failed,
        "passed_count": report.passed_count,
        "failed_count": report.failed_count,
        "checks": checks,
    }
    if report.scope == "commits":
        result["commits"] = report.commits
        result["marked"] = report.marked
    return result


@mcp.tool()
async def list_steps() -> dict[str, Any]:
    """List available scanner step names.

    Use these names with the only_step parameter of scan_repo
    to run a single check type.
    """
    return {"steps": sorted(get_step_names())}


@mcp.tool()
async def hook_status(repo_path: str = ".") -> dict[str, Any]:
    """Report the git-scan hooks: what each protects, and which one git runs.

    States are exact matches against scripts git-scan has shipped:
    ``installed`` (current), ``outdated`` (an earlier git-scan script),
    ``other`` (a hook git-scan did not write, or an edited one; its content
    is not inspected), or ``absent``. ``global`` and ``local`` are the
    pre-commit hook at each level. ``effective`` is the pre-commit hook git
    runs for this repository: in the ``core.hooksPath`` directory if set,
    else in the repository's own ``.git/hooks``. ``hooks`` has one row per
    hook in that directory (pre-commit, commit-msg, post-commit, pre-push):
    ``hook``, ``path``, ``state``, and ``protects``, a sentence saying what
    it protects or, when it is not git-scan's, what goes unprotected (for
    example "commit messages are not scanned here"). ``hooks`` is null when
    no repository and no ``core.hooksPath`` say where git runs hooks.

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
