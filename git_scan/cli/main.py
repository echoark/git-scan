"""CLI — thin wrapper around the SDK."""

import sys

import click

from git_scan.sdk.scanner import run_scan, InvalidStepError
from git_scan.sdk.steps import get_step_names
from git_scan.sdk import user_patterns as up
from git_scan.sdk.hooks import install_hook, HookError


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """Scan git repositories for sensitive data."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@cli.command()
@click.argument("path", default=".")
@click.option("--deep", is_flag=True, help="Reserved: history scanning (see 'audit').")
@click.option("--step", default=None, help="Run only this step.")
@click.option("--unstaged", is_flag=True, help="Also scan unstaged changes.")
@click.option("--untracked", is_flag=True, help="Also scan untracked files.")
@click.option("--verbose", "-v", is_flag=True, help="Show skipped checks and details.")
def run(path, deep, step, unstaged, untracked, verbose):
    """Scan staged changes for sensitive data.

    Reads the staged diff (added and removed lines) plus every file, branch,
    and tag name, and runs the configured checks. Exit code 1 on findings,
    so it serves as a pre-commit hook (see 'git-scan hook install').
    """
    try:
        report = run_scan(
            repo_path=path,
            deep=deep,
            only_step=step,
            include_untracked=untracked,
            include_unstaged=unstaged,
        )
    except InvalidStepError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    for check in report.checks:
        if check.skipped and not verbose:
            continue
        if check.passed:
            status, style = ("PASS", "green") if not check.skipped else ("SKIP", "yellow")
        else:
            status, style = "FAIL", "red"
        info = f" ({check.info})" if check.info else ""
        click.echo(click.style(f"  [{status}] ", fg=style) + check.name + info)
        if not check.passed and not check.skipped:
            for finding in check.findings[:10]:
                click.echo(f"         {finding}")
            if len(check.findings) > 10:
                click.echo(f"         ... and {len(check.findings) - 10} more")

    click.echo()
    if report.failed:
        click.echo(click.style(f"FAILED: {report.failed_count} check(s) found issues",
                               fg="red", bold=True))
        sys.exit(1)
    click.echo(click.style(f"PASSED: {report.passed_count} check(s) clean", fg="green"))


@cli.command("steps")
def list_steps():
    """List available scanner steps."""
    for name in sorted(get_step_names()):
        click.echo(f"  {name}")


def _fail(e: Exception) -> None:
    click.echo(f"Error: {e}", err=True)
    sys.exit(1)


# --- patterns ---------------------------------------------------------------

@cli.group()
def patterns():
    """Personal patterns: strings that must never appear in a commit."""


@patterns.command("add")
@click.argument("id")
@click.option("--pattern", required=True, help="Regex to block (escape literal '.' etc.).")
@click.option("--category", default=None, help="Label shown in findings, e.g. name, employer.")
@click.option("--word-boundary", is_flag=True, help="Match whole words only.")
@click.option("--not-followed-by", multiple=True, metavar="TEXT",
              help="Don't match when this text follows (repeatable).")
@click.option("--project", is_flag=True, help="Write the repo's git-scan.yaml instead of yours.")
def patterns_add(id, pattern, category, word_boundary, not_followed_by, project):
    """Add a pattern to your personal patterns (or the project's)."""
    try:
        up.add_pattern(id, pattern, category, word_boundary, list(not_followed_by),
                       project=project)
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)
    click.echo(f"added '{id}'")


@patterns.command("remove")
@click.argument("id")
@click.option("--project", is_flag=True, help="Act on the repo's git-scan.yaml.")
def patterns_remove(id, project):
    """Stop applying a pattern: deletes yours, or turns off a built-in."""
    try:
        click.echo(up.remove_pattern(id, project=project))
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)


@patterns.command("restore")
@click.argument("id")
@click.option("--project", is_flag=True, help="Act on the repo's git-scan.yaml.")
def patterns_restore(id, project):
    """Turn a built-in pattern back on."""
    try:
        click.echo(up.restore_pattern(id, project=project))
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)


@patterns.command("clear")
@click.option("--project", is_flag=True, help="Act on the repo's git-scan.yaml.")
def patterns_clear(project):
    """Declare that you have no personal patterns (opts out of that check)."""
    try:
        click.echo(up.clear_patterns(project=project))
    except up.ConfigError as e:
        _fail(e)


@patterns.command("list")
@click.option("--all", "include_disabled", is_flag=True, help="Include turned-off patterns.")
def patterns_list(include_disabled):
    """Show every active pattern with the layer it comes from."""
    try:
        rows = up.list_patterns(include_disabled=include_disabled)
    except up.ConfigError as e:
        _fail(e)
    width = max((len(r["id"]) for r in rows), default=2)
    for r in rows:
        state = "  (off; restore with: git-scan patterns restore " + r["id"] + ")" if r["disabled"] else ""
        cat = f" [{r['category']}]" if r["category"] else ""
        click.echo(f"  {r['id']:<{width}}  {r['layer']:<8} {r['pattern']}{cat}{state}")


# --- config -----------------------------------------------------------------

@cli.group()
def config():
    """Settings (thresholds, entropy) and config file locations."""


@config.command("path")
def config_path():
    """Show each config layer's file and whether it exists."""
    for row in up.layer_status():
        mark = "present" if row["exists"] else "absent"
        click.echo(f"  {row['layer']:<9} {row['path']}  ({mark})")


def _setting_command(name: str):
    section, key, typ, desc = up.SETTINGS[name]

    @config.command(name, help=f"{desc}. No value shows the current one.")
    @click.argument("value", required=False)
    @click.option("--project", is_flag=True, help="Write the repo's git-scan.yaml instead of yours.")
    def _cmd(value, project):
        if value is None:
            current = up.get_setting(name)
            click.echo("on" if current is True else "off" if current is False else current)
            return
        try:
            up.set_setting(name, value, project=project)
        except (up.PatternError, up.ConfigError) as e:
            _fail(e)
        click.echo(f"{name} set to {value}")


for _name in up.SETTINGS:
    _setting_command(_name)


# --- hook -------------------------------------------------------------------

@cli.group()
def hook():
    """Git hook installation."""


@hook.command("install")
@click.option("--hooks-dir", default=None, help="Directory for the hook (default: ~/.config/git/hooks).")
@click.option("--force", is_flag=True, help="Replace an existing pre-commit hook.")
def hook_install(hooks_dir, force):
    """Install 'git-scan run' as the pre-commit hook for every repository."""
    try:
        click.echo(install_hook(hooks_dir, force=force))
    except HookError as e:
        _fail(e)
