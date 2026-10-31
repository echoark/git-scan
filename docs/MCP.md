# MCP server

git-scan ships a small MCP server over stdio so an agent can run the same
scans the hooks run (the staged change, a commit message, a range of
commits), before committing or on request, and check how the hooks are
installed. It has no network transport and no
authentication: it runs on the same machine as the agent, as the same
user, and reads that user's configuration.

## Install

The server needs the `mcp` extra:

```bash
pipx install "git-scan[mcp] @ git+https://github.com/echoark/git-scan.git@v0.7.0"
```

This installs `git-scan` (the CLI) and `git-scan-mcp` (the server). An
existing install without the extra can be replaced with the same command
plus `--force`.

## Register

Claude Code:

```bash
claude mcp add --scope user git-scan -- git-scan-mcp
claude mcp list
```

Antigravity CLI:

```bash
agy mcp add git-scan -- git-scan-mcp
agy mcp list
```

Any other client: run `git-scan-mcp` as a stdio server.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `scan_repo` | `repo_path` (default `.`), `only_step`, `include_unstaged`, `include_untracked`, `message`, `rev_range`, `deep` (reserved) | `passed`, `passed_count`, `failed_count`, and `checks`: one entry per check with `name`, `passed`, `findings`, and `info`. With `rev_range`, also `commits` (read) and `marked` (clean, now marked `scanned`) |
| `hook_status` | `repo_path` (default `.`) | `global` and `local` (each `path` and `state` of the pre-commit hook), `hooks_path`, `effective` (the pre-commit hook git runs for that repository), and `hooks`: one row per hook in that directory (`pre-commit`, `commit-msg`, `post-commit`, `pre-push`), each with `hook`, `path`, `state`, and `protects`, a sentence saying what it protects or what goes unprotected |
| `list_steps` | none | `steps`: the check module names `only_step` accepts |

`message` and `rev_range` each replace the staged diff; give at most one.
`message` is the text of a commit message (the check the `commit-msg` hook
runs). `rev_range` is `A..B`: each commit's own changes, message, and
author and committer emails, in one pass, with findings starting
`[commit <short sha>]`.

States in `hook_status` are `installed`, `outdated`, `other`, or `absent`,
decided by exact match against the scripts git-scan has shipped; the
content of an `other` hook is never inspected. See the README's
[Hook installation](../README.md#hook-installation) for what each state
means.

## Notes for agents

- A failed `scan_repo` lists each finding as `file:line`, with `(removed)`
  on lines being deleted. Fix the content or, for a false positive, adjust
  the configuration with the `git-scan patterns` / `emails` commands;
  don't bypass the hook.
- `scan_repo` reads the repository's staged change. Stage first, then
  scan, or pass `include_unstaged=true` to scan work in progress.
- Check a commit message before committing with `message`. A full
  40-character SHA is refused like any long hex string: cite commits by a
  short SHA. If the `commit-msg` hook refuses a message anyway, git keeps
  it in `.git/COMMIT_EDITMSG`; fix it there and run
  `git commit -e -F .git/COMMIT_EDITMSG`.
- Before pushing work that skipped the hooks (a rebase, a rewrite), scan it
  with `rev_range` (for example `origin/main..HEAD`); the push gate, if
  installed, scans it at push time anyway.
- The server exposes no way to change configuration or hooks. Those are
  CLI commands, run in a shell.
