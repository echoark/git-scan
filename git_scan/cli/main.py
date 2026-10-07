"""CLI — thin wrapper around the SDK."""

import sys

import click

from git_scan.sdk.scanner import run_scan, InvalidStepError
from git_scan.sdk.steps import get_step_names
from git_scan.sdk import user_patterns as up
from git_scan.sdk.hooks import install_hook, uninstall_hook, status, format_status, HookError


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """Scan git repositories for sensitive data.

    \b
    Setup, in order:
      git-scan hook install                      run on every commit, machine-wide
      git-scan patterns add ID --pattern REGEX   strings that must never be committed
      git-scan identity allow REMOTE EMAIL...    who may commit where (optional)
      git-scan emails allow ADDRESS              addresses the email check ignores
      git-scan config path                       where the config files live

    Each group's --help lists its commands; each command's --help has
    examples. 'git-scan run' is what the hook runs; use it to pre-check.
    """
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
@click.option("--exclude-file", multiple=True, metavar="GLOB",
              help="Don't apply in files matching this glob (repeatable).")
@click.option("--project", is_flag=True, help="Write the repo's git-scan.yaml instead of yours.")
def patterns_add(id, pattern, category, word_boundary, not_followed_by, exclude_file, project):
    """Add a pattern: a regex that must never appear in a commit.

    \b
    Examples:
      git-scan patterns add my-name --pattern 'Jane Doe' --category name
      git-scan patterns add cust-acme --pattern 'Acme Corporation' --category customer
      git-scan patterns add my-co --pattern 'Example Corp' --category employer --not-followed-by ' Cloud'

    ID is a handle for later edits (letters, digits, '.', '_', '-'); keep it
    free of the pattern text, since ids can appear in a repository's
    git-scan.yaml.
    """
    try:
        up.add_pattern(id, pattern, category, word_boundary, list(not_followed_by),
                       list(exclude_file), project=project)
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)
    click.echo(f"added '{id}'")


@patterns.command("edit")
@click.argument("id")
@click.option("--pattern", default=None, help="New regex.")
@click.option("--category", default=None)
@click.option("--word-boundary/--no-word-boundary", default=None)
@click.option("--not-followed-by", multiple=True, metavar="TEXT", help="Replaces the list (repeatable).")
@click.option("--exclude-file", multiple=True, metavar="GLOB",
              help="Replaces the list of file globs the pattern skips (repeatable).")
@click.option("--project", is_flag=True, help="Act on the repo's git-scan.yaml.")
def patterns_edit(id, pattern, category, word_boundary, not_followed_by, exclude_file, project):
    """Change a pattern's fields.

    \b
    Examples:
      git-scan patterns edit my-co --not-followed-by ' Cloud' --not-followed-by '-sdk'
      git-scan patterns edit my-co --exclude-file 'docs/vendors.md'
      git-scan patterns edit my-co --exclude-file ''      (clears the list)

    Lists replace the previous value. Editing a built-in writes an override
    into your layer rather than touching the package.
    """
    try:
        up.edit_pattern(id, project=project, pattern=pattern, category=category,
                        word_boundary=word_boundary,
                        not_followed_by=list(not_followed_by) or None,
                        exclude_files=list(exclude_file) or None)
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)
    click.echo(f"updated '{id}'")


@patterns.command("remove")
@click.argument("id")
@click.option("--project", is_flag=True, help="Act on the repo's git-scan.yaml.")
def patterns_remove(id, project):
    """Stop applying a pattern.

    Deletes an entry you own; a built-in (or, with --project, one of yours)
    is turned off for that layer's scope instead. 'patterns restore' undoes
    the latter.
    """
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
    """Declare that you have no personal patterns.

    Writes an empty list to your config so the personal-patterns check
    passes without any entries. Use 'patterns add' to start adding later.
    """
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


# --- emails -----------------------------------------------------------------

@cli.group()
def emails():
    """Addresses the email check may ignore (test fixtures, bots)."""


@emails.command("allow")
@click.argument("address")
@click.option("--project", is_flag=True, help="Write the repo's git-scan.yaml instead of yours.")
def emails_allow(address, project):
    """Allow an address."""
    try:
        up.allow_email(address, project=project)
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)
    click.echo(f"allowed {address}")


@emails.command("disallow")
@click.argument("address")
@click.option("--project", is_flag=True, help="Act on the repo's git-scan.yaml.")
def emails_disallow(address, project):
    """Stop allowing an address."""
    try:
        up.disallow_email(address, project=project)
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)
    click.echo(f"disallowed {address}")


@emails.command("list")
def emails_list():
    """Show allowed addresses after merging all layers."""
    try:
        for a in up.list_allowed_emails():
            click.echo(f"  {a}")
    except up.ConfigError as e:
        _fail(e)


# --- identity ---------------------------------------------------------------

@cli.group()
def identity():
    """Emails allowed per remote (a host/owner/repo prefix). Optional.

    Git config says which address to use; it can't refuse the wrong one. If
    one account and one address serve every repository on this machine, set
    it once in your global git config and skip this. Rules matter when the
    right address depends on the repository (personal vs employer vs a
    customer organization): a new clone inherits the global address, and a
    rule refuses the commit instead of letting it into history.

    A rule names a remote prefix and the emails allowed to commit there.
    Prefixes match whole segments: github.com/octo covers github.com/octo/*
    and nothing else. With no matching rule the check is skipped (shown in
    the scan output), so nothing is enforced until you add rules.
    """


@identity.command("allow")
@click.argument("remote")
@click.argument("emails", nargs=-1, required=True)
@click.option("--project", is_flag=True, help="Write the repo's git-scan.yaml instead of yours.")
def identity_allow_cmd(remote, emails, project):
    """Allow EMAILS for every remote under REMOTE.

    \b
    Examples:
      git-scan identity allow github.com/octo '*+octo@users.noreply.github.com'
      git-scan identity allow git.example.com '*@example.com'
      git-scan identity allow github.com/acme-corp/billing you@example.org

    REMOTE is a host, optionally followed by an owner and a repository.
    '*' in an email matches anything. Repeating the command adds emails
    to the same rule.
    """
    try:
        rule = up.identity_allow(remote, list(emails), project=project)
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)
    click.echo(f"{rule['remote']}: {', '.join(rule['emails'])}")


@identity.command("remove")
@click.argument("remote")
@click.argument("email", required=False)
@click.option("--project", is_flag=True, help="Act on the repo's git-scan.yaml.")
def identity_remove_cmd(remote, email, project):
    """Remove one email from a rule, or the whole rule."""
    try:
        click.echo(up.identity_remove(remote, email, project=project))
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)


@identity.command("list")
def identity_list_cmd():
    """Show every identity rule after merging all layers."""
    try:
        rules = up.identity_list()
    except up.ConfigError as e:
        _fail(e)
    if not rules:
        click.echo("  no identity rules (the git identity check is skipped everywhere)")
    for r in rules:
        click.echo(f"  {r.get('remote')}: {', '.join(r.get('emails') or [])}")


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
    """The pre-commit hook: install for every repository, or for one."""


_LOCAL = click.option("--local", is_flag=True,
                      help="This repository's .git/hooks instead of the global hooks directory.")
_DIR = click.option("--hooks-dir", default=None,
                    help="Global hooks directory (default: ~/.config/git/hooks).")


@hook.command("install")
@_LOCAL
@_DIR
@click.option("--force", is_flag=True, help="Replace a pre-commit hook that is not a git-scan script.")
def hook_install(local, hooks_dir, force):
    """Install 'git-scan run' as the pre-commit hook.

    An earlier git-scan script is upgraded. Anything else already there is
    left alone unless --force.
    """
    try:
        click.echo(install_hook(hooks_dir, force=force, local=local))
    except HookError as e:
        _fail(e)


@hook.command("uninstall")
@_LOCAL
@_DIR
def hook_uninstall(local, hooks_dir):
    """Remove the git-scan pre-commit hook. Any other hook is left alone."""
    try:
        click.echo(uninstall_hook(hooks_dir, local=local))
    except HookError as e:
        _fail(e)


@hook.command("status")
@_DIR
def hook_status(hooks_dir):
    """What is installed at each level, and which one git runs."""
    click.echo(format_status(status(hooks_dir)))
