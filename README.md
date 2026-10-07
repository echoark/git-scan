# git-scan

A pre-commit scanner for sensitive data: credentials, personal
identifiers, and anything else you decide must never reach a repository.
It reads the change you are about to commit, plus every file, branch, and
tag name, and blocks the commit when something matches.

What it checks:

| Check | What it looks for |
|---|---|
| Tokens and keys | GitHub, Google, AWS, OpenAI, Slack tokens; private-key headers; generic API keys; UUIDs and long hex strings |
| Entropy | random-looking strings that resemble secrets, with exclusions for lockfile hashes and the like |
| Personal patterns | strings you configure: your name, employer, customers, properties, account numbers |
| Environment | your OS username, home directory name, git name and email, values from a `.env` file |
| Emails | addresses outside reserved example domains |
| SSN / EIN | US tax identifier formats |
| Google Drive IDs | document and folder IDs in Drive URLs |
| Dollar amounts | large, non-round, or cents-precise figures, with thresholds you can set |
| Names | personal patterns, usernames, emails, and SSN/EINs in file, branch, and tag names |
| Git identity | the commit's email must fit the remote host (see below) |

## Install

```bash
pipx install git+https://github.com/krisrowe/git-scan.git@v0.2.0
```

Then install the hook and declare your personal patterns:

```bash
git-scan hook install
git-scan patterns add my-name --pattern 'Jane Doe' --category name
git-scan patterns add my-employer --pattern 'Example Corp' --category employer
```

`hook install` writes a one-line `pre-commit` hook to `~/.config/git/hooks`
and points git's global `core.hooksPath` there, so every repository on the
machine is covered. If you already have a hooks directory, pass
`--hooks-dir`; if a pre-commit hook already exists, `--force` replaces it.

A scan with no personal patterns configured fails and says so: either add
patterns, or opt out with `git-scan patterns clear`.

## What a scan reads

`git-scan run` scans the **staged diff**: lines being added *and* lines
being removed, so a secret being deleted is still reported (it remains in
history until that is rewritten). Unchanged lines in a touched file are
not scanned. Findings name the file and line:

```
  [FAIL] Patterns (3 personal, 17 built-in, 5 environment; 2 files)
         config/settings.py:14 [token] ghp_****
         notes/plan.md:3 (removed) [employer] Example Corp
```

Optional scopes:

| Flag | Adds |
|---|---|
| `--unstaged` | changes on disk not yet staged |
| `--untracked` | files git does not track yet |

Names (every tracked file path, branch, and tag) are always checked, since
a bad name is published with every push regardless of what the commit
touches.

## Git identity

The commit's author email must be safe for where the repository pushes.
No network calls are made:

1. A repository-level `user.email` is trusted.
2. Otherwise, for a github.com remote, the email must be the noreply
   address of an account the GitHub CLI is logged into
   (`<id>+<login>@users.noreply.github.com`).
3. For any other host, the email's domain must be the host's domain or a
   parent of it (`you@example.com` for `git.example.com`).

A failure says which rule applied and how to fix it (set the repository's
email, or `gh auth login`).

## Configuration

Settings merge from three layers, later ones winning:

| Layer | Where | Holds |
|---|---|---|
| built-in | shipped with the package | token patterns, entropy settings, thresholds |
| user | `~/.config/git-scan/git-scan.yaml` | your personal patterns and overrides |
| project | `git-scan.yaml` in a repository root | per-repository additions and overrides |

Manage them with the CLI rather than editing files:

```bash
git-scan patterns add ID --pattern REGEX [--category C] [--word-boundary] [--not-followed-by TEXT]...
git-scan patterns remove ID          # deletes yours, or turns off a built-in
git-scan patterns restore ID         # turns a built-in back on
git-scan patterns list [--all]       # merged view, with each entry's layer
git-scan patterns clear              # declare you have no personal patterns
git-scan config path                 # where each layer lives
git-scan config large-amount 500000  # thresholds and entropy settings
git-scan config entropy-enabled off
```

Add `--project` to write the repository's `git-scan.yaml` instead of
yours. Every layer is validated when loaded; a broken file fails the scan
with the file, the entry, and the command that fixes it.

Back up `~/.config/git-scan/git-scan.yaml`: it holds the personal patterns
that make the scanner yours.

## Exit codes

`0` clean, `1` findings or an invalid configuration, `2` bad arguments.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the scanning model, repository
layout, test conventions, planned work (whole-repository and history
audits), and the release process.
