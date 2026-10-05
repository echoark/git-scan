"""CLI — thin wrapper around SDK."""

import sys

import click

from git_scan.sdk.scanner import run_scan, InvalidStepError
from git_scan.sdk.steps import get_step_names


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """Scan git repositories for sensitive data."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@cli.command()
@click.argument("path", default=".")
@click.option("--deep", is_flag=True, help="Also scan git history (slower).")
@click.option("--step", default=None, help="Run only this step.")
@click.option("--untracked", is_flag=True, help="Also scan untracked files.")
@click.option("--verbose", "-v", is_flag=True, help="Show skipped checks and details.")
def run(path, deep, step, untracked, verbose):
    """Scan staged changes for sensitive data.

    Examines staged file content against configured checks. Context-agnostic —
    becomes a pre-commit, commit-msg, or pre-push check when installed as a
    git hook via 'git-scan hook install'.
    """
    try:
        report = run_scan(
            repo_path=path,
            deep=deep,
            only_step=step,
            include_untracked=untracked,
        )
    except InvalidStepError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    # Display results
    for check in report.checks:
        if check.skipped and not verbose:
            continue

        if check.passed:
            status = "PASS" if not check.skipped else "SKIP"
            style = "green" if not check.skipped else "yellow"
        else:
            status = "FAIL"
            style = "red"

        info = f" ({check.info})" if check.info else ""
        click.echo(click.style(f"  [{status}] ", fg=style) + check.name + info)

        if not check.passed and not check.skipped:
            for finding in check.findings[:10]:
                click.echo(f"         {finding}")
            if len(check.findings) > 10:
                click.echo(f"         ... and {len(check.findings) - 10} more")

    # Summary
    click.echo()
    if report.failed:
        click.echo(click.style(
            f"FAILED: {report.failed_count} check(s) found issues",
            fg="red", bold=True,
        ))
        sys.exit(1)
    else:
        click.echo(click.style(
            f"PASSED: {report.passed_count} check(s) clean",
            fg="green",
        ))


@cli.command("steps")
def list_steps():
    """List available scanner steps."""
    for name in sorted(get_step_names()):
        click.echo(f"  {name}")
