# git-scan

A scanner for sensitive data in git repositories: credentials, personal
identifiers, or any string you decide must never reach a repository. It
reads the change you are about to commit, plus every file, branch, and tag
name, and reports what matches. Install it as a pre-commit hook to block
such commits, run it by hand on work in progress, or let an agent run it
through the MCP server.

## Features

- **Scans what the commit changes.** Added *and* removed lines: a secret
  being deleted is still reported, because it stays in history. Unchanged
  lines are never re-scanned, so busy files don't block unrelated commits.
- **Reports the file and line** of every finding.
- **Checks names too.** Tracked file paths, branches, and tags are checked
  on every commit, since they are published with every push.
- **Your own patterns.** Names, employers, customers, properties, account
  numbers: whatever must stay out of your repositories.
- **Built-in detectors** for common credentials and identifiers.
- **One command for every repository.** The hook is installed once per
  machine.
- **No network.** Every check runs locally, including the git identity
  check, which enforces only the rules you set.

## Checks

| Check | Looks for |
|---|---|
| Tokens and keys | GitHub, Google, AWS, OpenAI, and Slack tokens; private-key headers; generic API keys; UUIDs; long hex strings |
| Entropy | random-looking strings that resemble secrets, with exclusions for lockfile hashes and the like |
| Patterns | the strings you configure (see [Configuration](#configuration)) |
| Environment | your OS username, home directory name, git name and email, and values from a `.env` file in the repository |
| Emails | addresses outside reserved example domains and your allowed list |
| SSN / EIN | US tax identifier formats |
| Google Drive IDs | document and folder IDs in Drive URLs |
| Dollar amounts | large, non-round, or cents-precise figures, with thresholds you can set |
| Names | patterns, usernames, emails, and SSN/EINs in file, branch, and tag names |
| Git identity | the commit's author email is one you allowed for the remote (see [Git identity](#git-identity)) |

## Installation

Requires Python 3.11+ and git.

```bash
pipx install git+https://github.com/echoark/git-scan.git@v0.3.0
git-scan hook install
```

`hook install` writes a one-line `pre-commit` hook to `~/.config/git/hooks`
and points git's global `core.hooksPath` there, so every repository on the
machine is covered, including ones cloned later. See
[Hook installation](#hook-installation) for the other levels.

## Quick start

A scan refuses to run until you have decided about personal patterns, so
the first step is to add some (or opt out):

```bash
git-scan patterns add my-name --pattern 'Jane Doe' --category name
git-scan patterns add my-employer --pattern 'Example Corp' --category employer
git-scan run            # scans the staged change of the current repository
```

To run without any personal patterns:

```bash
git-scan patterns clear
```

## Usage

```bash
git-scan run [PATH]            # scan the staged change (what the hook runs)
git-scan run --unstaged        # also scan changes not yet staged
git-scan run --untracked       # also scan files git does not track yet
git-scan run --step patterns   # run one check
git-scan run -v                # show skipped checks
git-scan steps                 # list the checks
```

Exit codes: `0` clean, `1` findings or an invalid configuration, `2` bad
arguments. Findings look like this:

```
  [FAIL] Patterns (3 personal, 17 built-in, 5 environment; 2 files)
         config/settings.py:14 [token] ghp_****
         notes/plan.md:3 (removed) [employer] Example Corp
  [FAIL] Patterns in branch names (4 names)
         branch: acme-migration [customer]
```

## Configuration

Settings merge from three layers; later layers win:

| Layer | Where | Holds |
|---|---|---|
| built-in | shipped with the package | token patterns, entropy settings, thresholds |
| user | `~/.config/git-scan/git-scan.yaml` | your patterns, allowed emails, identity rules, overrides |
| project | `git-scan.yaml` in a repository root | per-repository additions and overrides |

Change them with the commands below rather than editing the files. Every
layer is validated when loaded; a broken file fails the scan and names the
file, the entry, and the fix. Add `--project` to any writing command to
target the repository's `git-scan.yaml` instead of your own.

### Patterns

A pattern is a regular expression with an id and, optionally, a category
shown in findings. Matching is case-sensitive; escape literal dots.

```bash
git-scan patterns add ID --pattern REGEX [--category CATEGORY] [OPTIONS]
```

| Option | Effect |
|---|---|
| `--word-boundary` | match whole words only |
| `--not-followed-by TEXT` | don't match when this text follows (repeatable) |
| `--exclude-file GLOB` | don't apply in files whose repository path matches (repeatable) |

Examples by category:

```bash
# People
git-scan patterns add my-name     --pattern 'Jane Doe'     --category name
git-scan patterns add my-nickname --pattern 'JD'           --category name --word-boundary

# Employer: allow its product names
git-scan patterns add my-employer --pattern 'Example Corp' --category employer \
    --not-followed-by ' Cloud' --not-followed-by '-sdk'

# Customers: name, short code, and project keywords
git-scan patterns add cust-acme      --pattern 'Acme Corporation' --category customer
git-scan patterns add cust-acme-code --pattern 'ACME-'            --category customer
git-scan patterns add cust-acme-proj --pattern 'Project Falcon'   --category customer

# Places and accounts
git-scan patterns add my-property --pattern '12 Example Lane'     --category property
git-scan patterns add my-policy   --pattern 'POL-[0-9]{8}'        --category policy
git-scan patterns add my-bank     --pattern 'Example Savings Bank' --category financial
```

Manage them afterwards:

```bash
git-scan patterns list [--all]       # merged view with each entry's layer
git-scan patterns edit ID [--category C] [--not-followed-by T]... [--exclude-file G]...
git-scan patterns remove ID          # deletes yours, or turns off a built-in
git-scan patterns restore ID         # turns a built-in back on
git-scan patterns clear              # declare that you have no personal patterns
```

`remove` on a pattern you don't own writes an override rather than touching
its source: a built-in is turned off in your layer, and with `--project` any
pattern, yours included, is turned off for that repository only. `restore`
(with the same `--project` if used) drops the override. `edit` with an empty value
(`--exclude-file ''`) clears that field.

### Allowed emails

Addresses the email check should ignore, such as test fixtures and bot
accounts. Reserved example domains (`example.com`, `example.org`,
`example.net`, `test.com`) and GitHub noreply addresses are always ignored.

```bash
git-scan emails allow bot@example.org
git-scan emails disallow bot@example.org
git-scan emails list
git-scan identity allow REMOTE EMAIL...   # who may commit to remotes under REMOTE (see Git identity)
git-scan identity remove REMOTE [EMAIL]
git-scan identity list
```

### Thresholds and entropy

Each setting is its own command; no value shows the current one.

```bash
git-scan config large-amount 500000     # flag amounts at or above this
git-scan config suspicious-nonround 10000
git-scan config cents-review 500
git-scan config entropy-enabled off
git-scan config entropy-threshold 4.2
git-scan config entropy-min-len 20
git-scan config path                     # where each layer lives
```

### Converting an existing pattern list

If you already keep sensitive strings in another tool's configuration,
feed each entry to `patterns add`. Most lists map directly: a pattern, a
category, and sometimes word-boundary or exception flags. For a YAML list
of entries with `pattern`, `category`, `word_boundary`, and
`not_followed_by` fields:

```bash
python3 - <<'EOF'
import subprocess, yaml
for i, p in enumerate(yaml.safe_load(open("old-patterns.yaml"))["patterns"], 1):
    cmd = ["git-scan", "patterns", "add", p.get("id", f"migrated-{i}"),
           "--pattern", p["pattern"]]
    if p.get("category"):
        cmd += ["--category", p["category"]]
    if p.get("word_boundary"):
        cmd += ["--word-boundary"]
    for t in p.get("not_followed_by", []):
        cmd += ["--not-followed-by", t]
    subprocess.run(cmd, check=True)
EOF
git-scan patterns list
```

Allowed addresses convert the same way with `emails allow`.

### Verifying a setup

```bash
git-scan patterns list                       # everything active, with its layer
git-scan config path                         # the user layer exists
cd "$(mktemp -d)" && git init -q && echo 'Jane Doe' > a.txt && git add a.txt && git-scan run
```

The last command should fail on `a.txt:1 [name]` if a name pattern is
configured.

### Backing up

`~/.config/git-scan/git-scan.yaml` holds everything that makes the scanner
yours. Keep it in whatever private place you back up dotfiles to; it is
not something to commit to a shared repository.

## Hook installation

Three ways to run git-scan on every commit:

| Level | Command | Covers |
|---|---|---|
| Global | `git-scan hook install` | every repository on the machine, via git's global `core.hooksPath` |
| Local | `git-scan hook install --local` | one repository, via its own `.git/hooks` |
| pre-commit framework | add this repository to `.pre-commit-config.yaml` (see `.pre-commit-hooks.yaml`) | a team's repository, through `pre-commit install` |

Git ignores a repository's own `.git/hooks` while `core.hooksPath` is set
at any level, so a local install warns when that is the case.

The hook script is a fixed three lines, identical on every machine (it
adds `~/.local/bin` to `PATH` so GUI git clients find `git-scan`). Install
and uninstall decide what to do by exact match against the scripts git-scan
has shipped, and nothing else:

| Existing file | `install` | `uninstall` |
|---|---|---|
| none | writes the hook | nothing to do |
| the current git-scan script | already installed | removes it |
| an earlier git-scan script | upgrades it | removes it |
| anything else | refuses unless `--force` | refuses; remove it yourself |

"Anything else" includes an edited git-scan script: git-scan cannot tell a
deliberate edit from another tool's hook, so it leaves both alone.

```bash
git-scan hook status            # what is installed at each level, and which one git runs
git-scan hook uninstall         # or --local
```

```
  global  ~/.config/git/hooks/pre-commit  (installed)
  local   .git/hooks/pre-commit  (absent)
  core.hooksPath = ~/.config/git/hooks
  git runs:  ~/.config/git/hooks/pre-commit  (installed)
```

The last line is where git will actually run a pre-commit hook for the
current repository, and what is there. Uninstalling leaves
`core.hooksPath` as it is, since other hooks may live in that directory.

## MCP server

An agent can run the same scan through a stdio MCP server, `git-scan-mcp`,
installed with the `mcp` extra:

```bash
pipx install "git-scan[mcp] @ git+https://github.com/echoark/git-scan.git@v0.3.0"
claude mcp add --scope user git-scan -- git-scan-mcp
```

It exposes `scan_repo`, `hook_status`, and `list_steps`. Registration for
other clients, the tools' arguments and results, and notes for agents are
in [MCP server](docs/MCP.md).

## Git identity

The commit's author email must be one you have allowed for the remote. You
say which emails may commit where; nothing is inferred and no network call
is made:

```bash
git-scan identity allow github.com/octo-dev   '*+octo-dev@users.noreply.github.com'
git-scan identity allow github.com/octo-tools '*+octo-dev@users.noreply.github.com'
git-scan identity allow git.example-corp.example '*@example-corp.example'
git-scan identity allow github.com/acme-corp you@example.org
git-scan identity list
git-scan identity remove github.com/acme-corp [you@example.org]
```

How a commit is judged:

1. A repository-level `user.email` is trusted.
2. Otherwise the remote is normalized to `host/owner/repo` and compared to
   each rule, segment by segment: `github.com/octo-dev` covers every
   repository of that owner, `github.com/octo-dev/app` one repository, and
   `git.example-corp.example` a whole host. (`github.com/echo` never
   matches `github.com/echoes/app`.) The email must match one of the
   emails of a matching rule; `*` in a rule email matches anything, so
   `*@example-corp.example` is any address at that domain and
   `*+octo-dev@users.noreply.github.com` is that account's GitHub noreply
   address with or without its numeric id.
3. No rule matches: the check is **skipped**, and the output says so with
   the command to add a rule. A fresh install enforces nothing until you
   decide what to enforce.

A repository with no remote, or a remote that is a filesystem path, passes.
A failure names the rule and the allowed emails, and how to fix it.

### Optional setup: identity rules

Two example setups; adapt the owners, hosts, and addresses to your own.

**A personal machine.** One GitHub account, `octo-dev`, two orgs you own,
and your global git email already set to the account's noreply address:

```bash
git-scan identity allow github.com/octo-dev   '*+octo-dev@users.noreply.github.com'
git-scan identity allow github.com/octo-tools '*+octo-dev@users.noreply.github.com'
git-scan identity allow github.com/octo-labs  '*+octo-dev@users.noreply.github.com'
```

Every commit to your own repositories now has to carry that address; a
work or customer address inherited by mistake is refused.

**A work laptop.** The global git email is your employer address; your
employer's own git host should take it, your personal repositories must
use your personal noreply, and a customer's organization gave you an
address of its own:

```bash
git-scan identity allow git.example-corp.example '*@example-corp.example'
git-scan identity allow github.com/octo-dev      '*+octo-dev@users.noreply.github.com'
git-scan identity allow github.com/acme-corp     you@example.org
```

With these, committing to a personal repository with the inherited work
address is refused until that repository sets its own `user.email` (or the
commit uses the noreply address), and the same for the customer's
organization. Repositories under owners with no rule are reported as
skipped, so you can add rules as you meet them.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the scanning model, repository
layout, test conventions, planned work (whole-repository and history
audits, a commit-msg hook), and the release process.
