"""CLI — thin wrapper around the SDK."""

import os
import re
import sys

import click

from git_scan.sdk.scanner import run_scan, push_scan, InvalidStepError, ScanUsageError
from git_scan.sdk.steps import get_step_names
from git_scan.sdk import user_patterns as up
from git_scan.sdk.hooks import install_hook, uninstall_hook, status, format_status, HookError
from git_scan.sdk import marks
from git_scan.sdk.config import load_config, ConfigError, write_missing_config_help


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


_FULL_SHA = re.compile(r"\b[0-9a-f]{40}\b")


def _echo_checks(report, verbose=False, hide_skipped=False, failed_only=False, err=False):
    """One line per check, with findings under a failing one."""
    for check in report.checks:
        if check.skipped and (hide_skipped or failed_only):
            continue
        if check.passed and failed_only:
            continue
        if check.passed:
            status, style = ("PASS", "green") if not check.skipped else ("SKIP", "yellow")
        else:
            status, style = "FAIL", "red"
        info = f" ({check.info})" if check.info else ""
        click.echo(click.style(f"  [{status}] ", fg=style) + check.name + info, err=err)
        if not check.passed and not check.skipped:
            shown = check.findings if verbose else check.findings[:10]
            for finding in shown:
                click.echo(f"         {finding}", err=err)
            if len(check.findings) > len(shown):
                click.echo(f"         ... and {len(check.findings) - len(shown)} more "
                           "(-v shows all)", err=err)


def _message_refusal(report, message_file):
    lines = ["", click.style("git-scan: commit message refused.", fg="red", bold=True)]
    if os.path.basename(message_file) == "COMMIT_EDITMSG":
        lines += [f"Git kept the message in {message_file}. Edit it and retry:",
                  f"  git commit -e -F {message_file}"]
    if any(_FULL_SHA.search(f) for c in report.checks for f in c.findings):
        lines.append("Cite commits by a short SHA (12 characters is plenty); a full "
                     "40-character SHA is refused like any long hex string.")
    return "\n".join(lines)


@cli.command()
@click.argument("path", default=".")
@click.option("--deep", is_flag=True, help="Reserved: history scanning (see 'audit').")
@click.option("--step", default=None, help="Run only this step.")
@click.option("--unstaged", is_flag=True, help="Also scan unstaged changes.")
@click.option("--untracked", is_flag=True, help="Also scan untracked files.")
@click.option("--verbose", "-v", is_flag=True, help="Show every finding, not just the first ten per check.")
@click.option("--hide-skipped", is_flag=True, help="Don't list checks that were skipped.")
@click.option("--commit", "commit", default=None, metavar="REV",
              help="Scan one commit (changes, message, author) instead of the staged diff; "
                   "marks it 'scanned' if clean.")
@click.option("--range", "rev_range", default=None, metavar="A..B",
              help="Scan every commit in a range in one pass; marks the clean ones 'scanned'.")
@click.option("--message", "message_file", default=None, metavar="FILE",
              help="Scan a commit message ('-' reads standard input) instead of the staged diff.")
def run(path, deep, step, unstaged, untracked, verbose, hide_skipped, commit, rev_range,
        message_file):
    """Scan staged changes for sensitive data.

    Reads the staged diff (added and removed lines) plus every file, branch,
    and tag name, and runs the configured checks. Exit code 1 on findings,
    so it serves as a pre-commit hook (see 'git-scan hook install').

    \b
    Instead of the staged diff:
      --message FILE  a commit message; what the commit-msg hook runs, and
                      a way to check a draft. Lines starting with '#' are
                      dropped, as git drops them.
      --range A..B    every commit in the range: each commit's own patch
                      (merges against their first parent), message, and
                      author and committer emails, in one pass. A secret
                      added in one commit and deleted in a later one is
                      still found. Findings name the commit.
      --commit REV    one commit, the same way.
    """
    message = None
    if message_file is not None:
        try:
            if message_file == "-":
                message = click.get_text_stream("stdin").read()
            else:
                with open(message_file, encoding="utf-8", errors="replace") as f:
                    message = f.read()
        except OSError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(2)
    try:
        report = run_scan(
            repo_path=path,
            deep=deep,
            only_step=step,
            include_untracked=untracked,
            include_unstaged=unstaged,
            commit=commit,
            message=message,
            rev_range=rev_range,
        )
    except (InvalidStepError, ScanUsageError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(2)

    if report.refusal:
        click.echo("\n".join(report.refusal))
        sys.exit(1)

    if report.scope == "message":
        if report.failed:
            _echo_checks(report, verbose=verbose, failed_only=True)
            click.echo(_message_refusal(report, message_file))
            sys.exit(1)
        click.echo(f"git-scan: commit message clean ({report.passed_count} checks)")
        return

    _echo_checks(report, verbose=verbose, hide_skipped=hide_skipped)
    click.echo()
    if report.scope == "commits" and step is None:
        click.echo(f"{len(report.commits)} commit(s) read; {len(report.marked)} clean and "
                   "marked 'scanned'")
    if report.failed:
        click.echo(click.style(f"FAILED: {report.failed_count} check(s) found issues",
                               fg="red", bold=True))
        sys.exit(1)
    click.echo(click.style(f"PASSED: {report.passed_count} check(s) clean", fg="green"))


@cli.command("mark")
@click.argument("label")
def mark(label):
    """Mark the current commit with LABEL.

    Run it after a check the push gate requires has passed (see
    'git-scan hook install --pre-push --require-mark LABEL'). One mark on
    the newest commit covers every unpushed commit before it.
    """
    try:
        sha = marks.add_mark(label)
    except marks.MarkError as e:
        _fail(e)
    click.echo(f"marked {sha[:12]} '{label}'")


@cli.command("push-check")
@click.argument("remote", required=False)
@click.argument("url", required=False)
def push_check(remote, url):
    """Scan the commits a push would send, and refuse it on findings.

    Every commit the remote doesn't have yet is scanned in one pass (its
    changes, message, author and committer), except commits already marked
    'scanned', which are skipped; clean commits are marked. Marks required
    with 'hook install --require-mark' must also be present.

    This is what the pre-push hook runs; git passes REMOTE and URL and the
    refs being pushed on standard input. Run by hand, it checks the current
    branch against its upstream: would a push be accepted?
    """
    stdin = click.get_text_stream("stdin")
    if remote is None or stdin.isatty():
        lines, remote = marks.current_branch_push(remote)
    else:
        lines = stdin.read().splitlines()
    try:
        required = load_config(".").required_marks
    except ConfigError as e:
        _fail(e)
    result = push_scan(lines, remote=remote, required=required)
    if result.scan is not None and result.scan.refusal:
        click.echo("\n".join(result.scan.refusal), err=True)
        sys.exit(1)
    if result.scan is not None and result.scan.failed:
        _echo_checks(result.scan, failed_only=True, err=True)
        click.echo(err=True)
    click.echo(marks.format_push_result(result), err=not result.ok)
    if not result.ok:
        sys.exit(1)


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
@click.option("--not-followed-by", multiple=True, metavar="TEXT",
              help="Replaces the whole list (repeatable); to add one exception, use "
                   "'patterns exceptions add'.")
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

    Lists replace the previous value: --not-followed-by sets the whole
    exception list, so an omitted value is dropped. To add or drop one
    exception and keep the rest, use 'patterns exceptions add / remove'.
    Editing a built-in writes an override into your layer rather than
    touching the package.
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


@patterns.group("exceptions")
def patterns_exceptions():
    """One pattern's exceptions: texts after which it does not match.

    These are the pattern's --not-followed-by values. 'add' and 'remove'
    change one value at a time and keep the rest, where 'patterns edit
    --not-followed-by' replaces the whole list.
    """


@patterns_exceptions.command("list")
@click.argument("id")
def exceptions_list(id):
    """Print a pattern's exceptions, one per line, as the scan applies them."""
    try:
        values = up.list_exceptions(id)
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)
    for v in values:
        click.echo(v)


@patterns_exceptions.command("add", context_settings={"ignore_unknown_options": True})
@click.argument("id")
@click.argument("values", nargs=-1, required=True, metavar="VALUE...")
@click.option("--project", is_flag=True, help="Act on the repo's git-scan.yaml.")
def exceptions_add(id, values, project):
    """Add exceptions to a pattern and print the resulting list.

    \b
    Example:
      git-scan patterns exceptions add my-co ' Cloud' '-sdk'

    A value already present is not added again; the existing values are
    kept. For a built-in (or, with --project, one of yours) the full list
    is written as an override in the target layer. A value that starts
    with '-' (such as '-sdk') is taken as a value; put '--' before the
    values if one could be read as an option.
    """
    try:
        result = up.add_exceptions(id, list(values), project=project)
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)
    for v in result:
        click.echo(v)


@patterns_exceptions.command("remove", context_settings={"ignore_unknown_options": True})
@click.argument("id")
@click.argument("values", nargs=-1, required=True, metavar="VALUE...")
@click.option("--project", is_flag=True, help="Act on the repo's git-scan.yaml.")
def exceptions_remove(id, values, project):
    """Remove exceptions from a pattern and print the remaining list.

    Every VALUE must be present; a value that is not fails the command and
    nothing is written.
    """
    try:
        result = up.remove_exceptions(id, list(values), project=project)
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)
    for v in result:
        click.echo(v)


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
    """The hooks: install for every repository, or for one."""


_LOCAL = click.option("--local", is_flag=True,
                      help="This repository's .git/hooks instead of the global hooks directory.")
_DIR = click.option("--hooks-dir", default=None,
                    help="Global hooks directory (default: ~/.config/git/hooks).")


@hook.command("install")
@_LOCAL
@_DIR
@click.option("--force", is_flag=True, help="Replace hooks that are not git-scan scripts.")
@click.option("--pre-push", "pre_push", is_flag=True,
              help="Also refuse pushes of commits that were not scanned (the push gate).")
@click.option("--require-mark", "require_mark", multiple=True, metavar="LABEL",
              help="With --pre-push: also require this mark (repeatable).")
@click.option("--missing-config-help", "missing_config_help", default=None, metavar="TEXT",
              help="Shown instead of setup instructions when a commit is refused "
                   "because the user config is missing.")
def hook_install(local, hooks_dir, force, pre_push, require_mark, missing_config_help):
    """Install the pre-commit and commit-msg hooks.

    pre-commit scans each staged change; commit-msg scans each commit
    message. An earlier git-scan script is upgraded. Anything else already
    there is left alone unless --force: another tool's commit-msg hook is
    kept and the rest installed (commit messages are then not scanned);
    any other hook in the way stops the install.

    \b
    --pre-push adds the push gate: a pre-push hook that scans every commit
    being pushed (skipping ones already marked 'scanned'; a post-commit
    hook marks commits whose scan passed) and refuses the push on findings,
    plus any mark named with --require-mark, which a separate check writes
    with 'git-scan mark LABEL'.
    """
    if require_mark and not pre_push:
        _fail(click.UsageError("--require-mark needs --pre-push"))
    try:
        click.echo(install_hook(hooks_dir, force=force, local=local, pre_push=pre_push))
        if missing_config_help:
            click.echo(f"missing-config help: {write_missing_config_help(missing_config_help)}")
        if require_mark:
            labels = up.require_marks(list(require_mark))
            click.echo(f"push requires: scanned, {', '.join(labels)}")
    except (HookError, up.PatternError) as e:
        _fail(e)
    if pre_push:
        click.echo("\n".join([
            "",
            "Every push now scans the commits it sends, skipping ones already marked",
            "'scanned' (committing through the hooks marks them).",
        ] + ([f"Marks a separate check must add: {', '.join(require_mark)} "
              "(git-scan mark <label> once it passes)."] if require_mark else [])))


@hook.command("uninstall")
@_LOCAL
@_DIR
def hook_uninstall(local, hooks_dir):
    """Remove the git-scan hooks. Any other hook is left alone."""
    try:
        click.echo(uninstall_hook(hooks_dir, local=local))
    except HookError as e:
        _fail(e)


@hook.command("post-commit", hidden=True)
def hook_post_commit():
    """What the post-commit hook runs: mark the commit if its scan passed."""
    marks.mark_if_scanned(".")


@hook.command("status")
@_DIR
def hook_status(hooks_dir):
    """What each hook protects, at each level, and which one git runs."""
    click.echo(format_status(status(hooks_dir)))
