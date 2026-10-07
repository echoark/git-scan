# MCP server

git-scan ships a small MCP server over stdio so an agent can run the same
scan the pre-commit hook runs, before committing or on request, and check
how the hook is installed. It has no network transport and no
authentication: it runs on the same machine as the agent, as the same
user, and reads that user's configuration.

## Install

The server needs the `mcp` extra:

```bash
pipx install "git-scan[mcp] @ git+https://github.com/krisrowe/git-scan.git@v0.2.0"
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
| `scan_repo` | `repo_path` (default `.`), `only_step`, `include_unstaged`, `include_untracked`, `deep` (reserved) | `passed`, `passed_count`, `failed_count`, and `checks`: one entry per check with `name`, `passed`, `findings`, and `info` |
| `hook_status` | `repo_path` (default `.`) | `global` and `local` (each `path` and `state`), `hooks_path`, and `effective`: where git will run a pre-commit hook for that repository |
| `list_steps` | none | `steps`: the check module names `only_step` accepts |

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
- The server exposes no way to change configuration or hooks. Those are
  CLI commands, run in a shell.
