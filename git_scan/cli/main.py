"""CLI — thin wrapper around the SDK."""

import json
import os
import re
import sys

import click

from git_scan.sdk.scanner import run_scan, push_scan, InvalidStepError, ScanUsageError
from git_scan.sdk.steps import get_step_names
from git_scan.sdk import user_patterns as up
from git_scan.sdk.hooks import install_hook, uninstall_hook, status, format_status, HookError
from git_scan.sdk import marks
from git_scan.sdk import review as review_sdk
from git_scan.sdk import relaxed as relaxed_sdk
from git_scan.sdk import hosts as hosts_sdk
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
                   "the push gate will not scan it again if clean.")
@click.option("--range", "rev_range", default=None, metavar="A..B",
              help="Scan every commit in a range in one pass; the push gate will not scan the clean ones again.")
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
        click.echo(f"{len(report.commits)} commit(s) read; {len(report.marked)} clean; "
                   "the push gate will not scan those again")
    if report.failed:
        click.echo(click.style(f"FAILED: {report.failed_count} check(s) found issues",
                               fg="red", bold=True))
        sys.exit(1)
    click.echo(click.style(f"PASSED: {report.passed_count} check(s) clean", fg="green"))


@cli.command("ack")
@click.option("--commit", "rev", default=None, metavar="REV",
              help="Acknowledge a commit already made instead of the staged change.")
def ack(rev):
    """Acknowledge the judgment matches of the staged change.

    A judgment entry (patterns add --judgment) names a value that is
    sensitive only in context. When one appears in a change, the scan lists
    every match with its note and refuses the commit until someone has
    looked. Run this after looking, when every match is fine; fix the
    content otherwise. The acknowledgement applies to exactly the staged
    content: stage anything more and it must be given again. It clears
    judgment matches only; every other finding still refuses the commit.

    \b
    --commit REV does the same for a commit the hooks did not see (the push
    gate names the command). The commit is scanned with its judgment
    matches acknowledged and, if everything else is clean, recorded as
    scanned so the push proceeds.
    """
    if rev:
        report = run_scan(repo_path=".", commit=rev, acknowledge_judgment=True)
        if report.refusal:
            click.echo("\n".join(report.refusal))
            sys.exit(1)
        _echo_checks(report, failed_only=True)
        if report.failed:
            click.echo(click.style("not acknowledged: the commit has findings that are not "
                                   "judgment calls", fg="red", bold=True))
            sys.exit(1)
        judged = next((c for c in report.checks if c.name == "Judgment"), None)
        what = judged.info if judged else "no judgment entries configured"
        click.echo(f"acknowledged {rev}: {what}; clean, the push gate will not scan it again")
        return
    report = run_scan(repo_path=".", only_step="patterns")
    judged = next((c for c in report.checks if c.name == "Judgment"), None)
    if judged is None:
        click.echo("nothing to acknowledge: no judgment entries are configured")
        return
    where = "the staged change"
    if judged.passed:
        # Nothing in the staged content; the match may be in the message git
        # kept when the commit-msg hook refused it.
        message = marks.pending_message(".")
        judged = None
        if message:
            msg_report = run_scan(repo_path=".", only_step="patterns", message=message)
            judged = next((c for c in msg_report.checks if c.name == "Judgment"), None)
            where = "the staged change and its pending commit message"
        if judged is None or judged.passed:
            click.echo("nothing to acknowledge: no judgment matches in the staged change"
                       + (" or its pending commit message" if message else ""))
            return
    tree = marks.record_ack(".")
    if not tree:
        _fail(marks.MarkError("not a git repository, or nothing is staged"))
    shown = [f for f in judged.findings if not f.startswith(("Each match", "  git-scan", "    note:"))]
    click.echo(f"acknowledged {len(shown)} judgment match(es) for {where}:")
    for f in shown:
        click.echo(f"  {f}")
    click.echo("Stage anything more and the acknowledgement must be given again.")


@cli.group(invoke_without_command=True)
@click.option("--relaxed", "relaxed_review", is_flag=True,
              help="This review only: relaxed (personal content becomes notes). Refused unless "
                   "the repository is eligible (git-scan relax) and still private.")
@click.option("--publish", "publish", multiple=True, metavar="FILE",
              help="Text published beside the push (a pull-request body) to review too (repeatable).")
@click.option("--range", "rev_range", default=None, metavar="A..B",
              help="Review these commits instead of the unpushed ones.")
@click.option("--runner", default=None, metavar="NAME|CMD",
              help="The headless agent CLI for this run (claude, agy, or a command).")
@click.option("--timeout", type=int, default=None, metavar="SECONDS", help="Per batch.")
@click.option("--quiet", is_flag=True, help="No progress lines on standard error.")
@click.pass_context
def review(ctx, relaxed_review, publish, rev_range, runner, timeout, quiet):
    """Run the privacy review of the unpushed commits and record a clean result.

    git-scan builds the reviewer's prompt itself: its packaged review
    instructions, your personal patterns and notes, each commit's message
    and patch, and any --publish text. It runs the configured headless agent
    CLI with no tools (the prompt is the reviewer's whole world), validates
    the JSON that comes back, and records a clean batch as reviewed. With
    'config require-review on', a push needs that for every commit it sends.
    The reviewer's one-line summary of each batch is printed with the model
    and the time it took.

    Every review is public unless --relaxed is given, and that is refused
    unless the repository is eligible (git-scan relax) and its host still
    reports it private. In a relaxed review, low-severity notes are shown
    but don't block.

    \b
    Findings are printed with the value and location; nothing is recorded
    for that batch. A reviewer problem (not installed, failed, timed out,
    unreadable output) is reported as such and records nothing. Commits
    already reviewed are skipped, so a rerun costs only what is new. With
    --publish and no commit left to review (a pull-request body edited after
    the push, a comment, an issue), the text is reviewed on its own and
    nothing is recorded. The run takes a minute or more: start it as a
    background command if you can.
    """
    if ctx.invoked_subcommand is not None:
        return
    say = (lambda _m: None) if quiet else (lambda m: click.echo(f"git-scan review: {m}", err=True))
    try:
        result = review_sdk.review(".", rev_range=rev_range, relaxed_review=relaxed_review,
                                  publish=list(publish), runner=runner, timeout=timeout,
                                  progress=say)
    except (review_sdk.ReviewError, ConfigError, marks.MarkError) as e:
        _fail(e)
    for line in result.reports:
        click.echo(f"  reviewer: {line}")
    for w in result.warnings:
        click.echo(f"  warning: {w}")
    for f in result.notes:
        note = f" ({f['note']})" if f.get("note") else ""
        click.echo(f"  note: {f['location']} [{f['category']}/low] {f['value']}{note}")
    if result.findings:
        click.echo(click.style(f"  [FAIL] Privacy review ({result.mode}; {len(result.commits)} "
                               f"commit(s); {len(result.findings)} finding(s))", fg="red"))
        for f in result.findings:
            note = f" ({f['note']})" if f.get("note") else ""
            click.echo(f"         {f['location']} [{f['category']}/{f.get('severity', '?')}] "
                       f"{f['value']}{note}")
        click.echo(click.style("FAILED: fix the publish text and review it again; nothing is recorded "
                               "for text" if result.text_only and not result.commits else
                               "FAILED: fix the content in those commits and review again; "
                               "nothing was recorded for them", fg="red", bold=True))
        sys.exit(1)
    n = len(result.commits)
    if result.text_only and not n:
        click.echo(click.style(f"PASSED: privacy review ({result.mode}, {result.runner}) clean for "
                               f"publish text only ({', '.join(result.text_only)}); no commit was "
                               "reviewed and nothing was recorded", fg="green"))
        return
    skipped = f", {len(result.skipped)} already reviewed" if result.skipped else ""
    if n:
        click.echo(click.style(f"PASSED: privacy review ({result.mode}, {result.runner}) clean for "
                               f"{n} commit(s){skipped}; recorded", fg="green"))
    else:
        click.echo(f"nothing new to review{skipped}")


@cli.command("relax")
@click.option("--remove", is_flag=True, help="Drop this repository's eligibility.")
def relax(remove):
    """Make this repository eligible for a relaxed review (once per clone).

    Eligibility only allows 'git-scan review --relaxed'; no review is relaxed
    by default. It is recorded in this repository's .git/config for its exact
    origin URL, and needs:

    \b
      github.com, or a host added as 'github': the host reports the
        repository private (REST API through gh). Any other answer, or none,
        refuses. The relaxed review asks again each time.
      a host added as 'other' with --allow-relaxed: no check is possible;
        the user has vouched for the host.
    Never: no remote, an unregistered host, or a repository whose
    git-scan.yaml declares it public-prospective.
    """
    try:
        if remove:
            click.echo("relaxed-review eligibility removed" if relaxed_sdk.unmark(".")
                       else "this repository was not eligible")
            return
        m = relaxed_sdk.mark(".")
        state = "already eligible" if m.already else "eligible"
        click.echo(f"{state} for a relaxed review: {m.url} ({m.basis})")
        click.echo("Each relaxed review is still asked for explicitly: git-scan review --relaxed")
    except (relaxed_sdk.RelaxedError, ConfigError) as e:
        _fail(e)


@cli.group("hosts")
def hosts_group():
    """Git hosts and how git-scan learns whether a repository is private.

    \b
      git-scan hosts list
      git-scan hosts add github git.corp.example          # GitHub Enterprise
      git-scan hosts add other git.internal.example --allow-relaxed
      git-scan hosts remove git.internal.example

    github.com is built in. Types: github (asked through gh api), bitbucket
    (not supported yet), other (can't be asked; --allow-relaxed vouches for
    the whole host), and any installed plugin. Kept in the global git config.
    """


@hosts_group.command("list")
def hosts_list():
    """Registered hosts and their types."""
    for h in hosts_sdk.list_hosts():
        extra = " (built in)" if h.builtin else (", allow-relaxed" if h.allow_relaxed else "")
        click.echo(f"  {h.host}  {h.type}{extra}")


@hosts_group.command("add")
@click.argument("kind", metavar="TYPE")
@click.argument("host")
@click.option("--allow-relaxed", is_flag=True,
              help="For a host git-scan can't ask: vouch that its repositories may be made "
                   "eligible for relaxed review without a check.")
def hosts_add(kind, host, allow_relaxed):
    """Register HOST as TYPE (github, bitbucket, other, or a plugin's type)."""
    try:
        h = hosts_sdk.add_host(kind, host, allow_relaxed=allow_relaxed)
    except hosts_sdk.HostError as e:
        _fail(e)
    click.echo(f"added {h.host} ({h.type}{', allow-relaxed' if h.allow_relaxed else ''})")


@hosts_group.command("remove")
@click.argument("host")
def hosts_remove(host):
    """Forget HOST."""
    try:
        hosts_sdk.remove_host(host)
    except hosts_sdk.HostError as e:
        _fail(e)
    click.echo(f"removed {host}")


@cli.command("push-check")
@click.argument("remote", required=False)
@click.argument("url", required=False)
def push_check(remote, url):
    """Scan the commits a push would send, and refuse it on findings.

    Every commit the remote doesn't have yet is scanned in one pass (its
    changes, message, author and committer), except commits a scan has
    already cleared, which are skipped. With 'config
    require-review on', every commit must also carry a clean privacy
    review ('git-scan review').

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
        require_review = load_config(".").review_required
    except ConfigError as e:
        _fail(e)
    result = push_scan(lines, remote=remote, require_review=require_review)
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
@click.option("--ignore-case", is_flag=True, help="Match regardless of letter case.")
@click.option("--judgment", is_flag=True,
              help="A value for a reviewer to weigh in context; never scanned as a regex.")
@click.option("--note", default=None, metavar="TEXT",
              help="Guidance shown to a reviewer beside this entry.")
@click.option("--project", is_flag=True, help="Write the repo's git-scan.yaml instead of yours.")
def patterns_add(id, pattern, category, word_boundary, not_followed_by, exclude_file,
                 ignore_case, judgment, note, project):
    """Add a pattern: a regex that must never appear in a commit.

    \b
    Examples:
      git-scan patterns add my-name --pattern 'Jane Doe' --category name --ignore-case
      git-scan patterns add cust-acme --pattern 'Acme Corporation' --category customer
      git-scan patterns add my-co --pattern 'Example Corp' --category employer --not-followed-by ' Cloud'
      git-scan patterns add card-a --pattern '1234' --category last4 --judgment \\
          --note 'flag only beside account, card, checking or a **** mask'

    ID is a handle for later edits (letters, digits, '.', '_', '-'); keep it
    free of the pattern text, since ids can appear in a repository's
    git-scan.yaml.

    A --judgment entry is for a reviewer that weighs context (see
    the privacy review), not for the scanner alone: a value that collides
    with ordinary text as a regex but is sensitive in context.
    """
    try:
        up.add_pattern(id, pattern, category, word_boundary, list(not_followed_by),
                       list(exclude_file), project=project, ignore_case=ignore_case,
                       judgment=judgment, note=note)
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
@click.option("--ignore-case/--no-ignore-case", default=None)
@click.option("--judgment/--no-judgment", default=None,
              help="Hand the entry to a reviewer, or back to the scanner.")
@click.option("--note", default=None, metavar="TEXT", help="Replaces the note ('' clears it).")
@click.option("--project", is_flag=True, help="Act on the repo's git-scan.yaml.")
def patterns_edit(id, pattern, category, word_boundary, not_followed_by, exclude_file,
                  ignore_case, judgment, note, project):
    """Change a pattern's fields.

    \b
    Examples:
      git-scan patterns edit my-co --not-followed-by ' Cloud' --not-followed-by '-sdk'
      git-scan patterns edit my-co --exclude-file 'docs/vendors.md'
      git-scan patterns edit my-co --exclude-file ''      (clears the list)
      git-scan patterns edit my-town --judgment --note 'only beside an address'

    Lists replace the previous value: --not-followed-by sets the whole
    exception list, so an omitted value is dropped. To add or drop one
    exception and keep the rest, use 'patterns exceptions add / remove'.
    Editing a built-in writes an override into your layer rather than
    touching the package. Prefer --judgment over 'patterns remove' for an
    entry that trips as a regex but still matters: the reviewer keeps it.
    """
    try:
        up.edit_pattern(id, project=project, pattern=pattern, category=category,
                        word_boundary=word_boundary,
                        not_followed_by=list(not_followed_by) or None,
                        exclude_files=list(exclude_file) or None,
                        ignore_case=ignore_case, judgment=judgment,
                        note=note if note is None else (note or False))
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
        kind = "  (judgment)" if r["judgment"] else ""
        click.echo(f"  {r['id']:<{width}}  {r['layer']:<8} {r['pattern']}{cat}{kind}{state}")


# --- notes ------------------------------------------------------------------

@cli.group()
def notes():
    """Free-text guidance for a reviewer that reads your configuration."""


@notes.command("add")
@click.argument("text")
@click.option("--project", is_flag=True, help="Write the repo's git-scan.yaml instead of yours.")
def notes_add(text, project):
    """Add a note: guidance that belongs to no single entry.

    \b
    Example:
      git-scan notes add 'a city name is a finding only beside an address or a property'
    """
    try:
        click.echo(up.add_note(text, project=project))
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)


@notes.command("remove")
@click.argument("number", type=int)
@click.option("--project", is_flag=True, help="Act on the repo's git-scan.yaml.")
def notes_remove(number, project):
    """Remove a note by its number in 'notes list'."""
    try:
        click.echo(up.remove_note(number, project=project))
    except (up.PatternError, up.ConfigError) as e:
        _fail(e)


@notes.command("list")
def notes_list():
    """Show the notes from every layer."""
    try:
        rows = up.list_notes()
    except up.ConfigError as e:
        _fail(e)
    for i, n in enumerate(rows, 1):
        click.echo(f"  {i}. {n}")


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
            if typ is bool:
                click.echo("on" if current is True else "off")
            else:
                click.echo("unset" if current is None else current)
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
@click.option("--require-review", "require_review", is_flag=True,
              help="With --pre-push: also refuse a push until a privacy review is recorded.")
@click.option("--missing-config-help", "missing_config_help", default=None, metavar="TEXT",
              help="Shown instead of setup instructions when a commit is refused "
                   "because the user config is missing.")
def hook_install(local, hooks_dir, force, pre_push, require_review, missing_config_help):
    """Install the pre-commit and commit-msg hooks.

    pre-commit scans each staged change; commit-msg scans each commit
    message. An earlier git-scan script is upgraded. Anything else already
    there is left alone unless --force: another tool's commit-msg hook is
    kept and the rest installed (commit messages are then not scanned);
    any other hook in the way stops the install.

    \b
    --pre-push adds the push gate: a pre-push hook that scans every commit
    being pushed (skipping ones the hooks already cleared; a post-commit
    hook remembers each commit whose scan passed) and refuses the push on findings.
    --require-review makes it also refuse a push until 'git-scan review' has
    passed for the unpushed commits.
    """
    if require_review and not pre_push:
        _fail(click.UsageError("--require-review needs --pre-push"))
    try:
        click.echo(install_hook(hooks_dir, force=force, local=local, pre_push=pre_push))
        if missing_config_help:
            click.echo(f"missing-config help: {write_missing_config_help(missing_config_help)}")
        if require_review:
            up.set_setting("require-review", True)
            click.echo("push requires: a scan of every commit, and a clean privacy review")
    except (HookError, up.PatternError, up.ConfigError) as e:
        _fail(e)
    if pre_push:
        click.echo("\n".join([
            "",
            "Every push now scans the commits it sends, skipping ones the hooks",
            "already cleared (committing through the hooks does that).",
        ] + (["A push is also refused until 'git-scan review' has passed for its commits."]
             if require_review else [])))


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
