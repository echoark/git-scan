# git-scan

A scanner for sensitive data in git repositories: credentials, personal
identifiers, or any string you decide must never reach a repository. It
reads the change you are about to commit, its commit message, and every
file, branch, and tag name, and reports what matches. Install it as git
hooks to block such commits (and, optionally, such pushes), run it by hand
on work in progress or a range of commits, or let an agent run it through
the MCP server.

## Features

- **Scans what the commit changes.** Added *and* removed lines: a secret
  being deleted is still reported, because it stays in history. Unchanged
  lines are never re-scanned, so busy files don't block unrelated commits.
- **Reports the file and line** of every finding.
- **Checks commit messages.** A `commit-msg` hook runs the same content
  checks on the message before git records it.
- **Checks names too.** Tracked file paths, branches, and tags are checked
  on every commit, since they are published with every push.
- **Scans commits already made.** `--range A..B` reads every commit in a
  range in one pass: each commit's own changes, message, and author and
  committer. The optional push gate does this for every push.
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
| Judgment | matches on the entries you marked for judgment, reported for acknowledgement rather than as findings (see [Judgment entries and notes](#judgment-entries-and-notes)) |
| Environment | your OS username, home directory name, git name and email, and values from a `.env` file in the repository |
| Emails | addresses outside reserved example domains and your allowed list |
| SSN / EIN | US tax identifier formats |
| Google Drive IDs | document and folder IDs in Drive URLs |
| Dollar amounts | large, non-round, or cents-precise figures, with thresholds you can set |
| Names | patterns, usernames, emails, and SSN/EINs in file, branch, and tag names |
| Git identity | the commit's author email is one you allowed for the remote; for commits already made, every author and committer email (see [Git identity](#git-identity)) |

Commit messages go through the same content checks as changed lines, with
the same rules. One consequence: a full 40-character commit SHA in a
message is refused like any long hex string (`hex-40`). Cite commits by a
short SHA instead; 12 characters is plenty.

## Installation

Requires Python 3.11+ and git.

```bash
pipx install git+https://github.com/echoark/git-scan.git
git-scan hook install
```

That installs the default branch. To pin a machine to a release, append
`@vX.Y.Z` to the URL with a tag from the repository's releases.

`hook install` writes one-line `pre-commit` and `commit-msg` hooks to
`~/.config/git/hooks` and points git's global `core.hooksPath` there, so
every repository on the machine is covered, including ones cloned later.
See [Hook installation](#hook-installation) for the other levels.

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
git-scan run --message FILE    # scan a commit message instead ('-' reads stdin)
git-scan run --range A..B      # scan every commit in a range instead
git-scan run --commit REV      # scan one commit instead
git-scan run --step patterns   # run one check
git-scan run -v                # show every finding, not just the first ten per check
git-scan run --hide-skipped    # omit checks that were skipped (shown by default)
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

**Commit messages.** `--message FILE` is what the `commit-msg` hook runs,
and a way to check a draft before committing. Lines starting with `#` and
anything below git's scissors line (`git commit -v`) are dropped, as git
drops them. File, branch, and tag names and the git identity are left to
the commit's own scan. A finding reads `commit message:3 [customer] Acme`.
When the hook refuses a message, git keeps it in `.git/COMMIT_EDITMSG`;
edit it and retry with `git commit -e -F .git/COMMIT_EDITMSG`.

**Commits already made.** `--range A..B` runs one `git log -p` over the
range and scans each commit in it: its own changes (a merge against its
first parent, so a hand-edited conflict resolution shows), its message, and
its author and committer emails, which are checked against your
[identity rules](#git-identity) rather than the email check. Commit ids and
dates are not scanned. Each commit is scanned on its own changes, so a
secret added in one commit and deleted in a later one is reported in both,
where a net `git diff A B` would show nothing. Findings name the commit:

```
  [FAIL] Patterns (3 personal, 17 built-in, 5 environment; 1 files)
         [commit 4f2a9c1e07b3] config/settings.py:14 [token] ghp_****
         [commit 9be01d44a2c6] config/settings.py:14 (removed) [token] ghp_****
  [FAIL] Git identity (Email not allowed for this remote)
         [commit 1c5d7e0a9f32] author you@work.example is not allowed for github.com/octo-dev/app
```

Clean commits are marked `scanned` (see [Push gate](#push-gate-optional)).
`--commit REV` does the same for one commit.

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
shown in findings. Matching is case-sensitive unless the entry says
`--ignore-case`; escape literal dots.

```bash
git-scan patterns add ID --pattern REGEX [--category CATEGORY] [OPTIONS]
```

| Option | Effect |
|---|---|
| `--word-boundary` | match whole words only |
| `--not-followed-by TEXT` | don't match when this text follows (repeatable) |
| `--exclude-file GLOB` | don't apply in files whose repository path matches (repeatable) |
| `--ignore-case` | match regardless of letter case |
| `--note TEXT` | guidance shown to a reviewer beside this entry (see [Judgment entries and notes](#judgment-entries-and-notes)) |
| `--judgment` | a match is a judgment call to acknowledge, not a finding |

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
git-scan patterns exceptions list ID             # one pattern's not-followed-by values
git-scan patterns exceptions add ID VALUE...     # add some, keep the rest
git-scan patterns exceptions remove ID VALUE...  # drop some, keep the rest
git-scan patterns remove ID          # deletes yours, or turns off a built-in
git-scan patterns restore ID         # turns a built-in back on
git-scan patterns clear              # declare that you have no personal patterns
```

`remove` on a pattern you don't own writes an override rather than touching
its source: a built-in is turned off in your layer, and with `--project` any
pattern, yours included, is turned off for that repository only. `restore`
(with the same `--project` if used) drops the override. `edit` with an empty value
(`--exclude-file ''`) clears that field.

**Exceptions: replace or add.** `edit --not-followed-by` *replaces* the
pattern's whole exception list, so adding one exception that way means
re-passing every existing value, and a forgotten one is silently dropped.
`patterns exceptions` works on one value at a time: `list` prints the
pattern's exceptions one per line (and nothing else), `add` appends the
values not already present (adding one twice is a no-op), and `remove`
drops the named values, failing if one is absent. `add` and `remove` print
the resulting list. They take the same `--project` flag as `edit`, and a
built-in pattern gets an override in your layer the same way.

```bash
git-scan patterns exceptions list my-employer
git-scan patterns exceptions add my-employer ' Cloud'     # keeps '-sdk' etc.
git-scan patterns exceptions remove my-employer '-sdk'
```

### Judgment entries and notes

Some values are sensitive only in context. The last four digits of an
account collide with line numbers and years; a city or a given name is
also an ordinary word; an area code is any three digits. As a regex each
would block ordinary commits, so a pattern list leaves them out, and
nothing catches them.

A **judgment entry** keeps such a value in the configuration with a
different rule for a match: it is a judgment call, not a finding. The
`Judgment` check lists every match with its note and refuses the commit
until someone has looked and acknowledged them:

```bash
git-scan patterns add card-a --pattern '1234' --category last4 --judgment --word-boundary \
    --note 'a finding only beside account, card, checking or a **** mask'
```

```
  [PASS] Patterns (3 personal, 17 built-in, 5 environment; 2 files)
  [FAIL] Judgment (9 entries; 2 match(es), not acknowledged)
         Each match below is a judgment call, not a finding. Look at every one;
         fix the content, or if all of them are fine, acknowledge them with:
           git-scan ack
         README.md:14 [employer] Example Corp
             note: a finding when the text says the author works there
         tests/fixtures/ledger.json:7 [last4] 1234
             note: a finding only beside account, card, checking or a **** mask
```

`git-scan ack` applies to exactly the staged content: stage anything more
and it must be given again, after looking again. It clears judgment matches
only; every other finding still refuses the commit. For a commit the hooks
did not see, the push gate names `git-scan ack --commit <sha>`, which scans
that commit with its judgment matches acknowledged and records it as
scanned if everything else is clean.

Judgment entries take the same options as any other (`--word-boundary`
keeps four digits from matching inside a longer number). Move an entry
between the two modes with `patterns edit ID --judgment` and
`--no-judgment`. Prefer `--judgment` over `patterns remove` for an entry
that trips as a regex but still matters: it keeps getting looked at.

**Notes** carry guidance that belongs to no single entry:

```bash
git-scan notes add 'a city name is a finding only beside an address or a property'
git-scan notes list
git-scan notes remove 1
```

**The reviewer.** The privacy review (below) reads the same entries and
notes: git-scan hands them to the reviewer with the commits, so an entry
that is too noisy for the scanner still gets judged, with its note, when a
push is reviewed.

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
| pre-commit framework | add this repository to `.pre-commit-config.yaml` (see `.pre-commit-hooks.yaml`) | a team's repository, through `pre-commit install` (the staged change only) |

Both levels install two hooks: `pre-commit`, which scans the staged change
and the repository's names, and `commit-msg`, which scans the message.
Re-running `hook install` upgrades earlier git-scan scripts in place and
adds any hook a previous version didn't install.

Git ignores a repository's own `.git/hooks` while `core.hooksPath` is set
at any level, so a local install warns when that is the case.

**When the user config is missing.** A full scan with no user config (or one
without a `patterns` key) runs no checks: it refuses the commit and says how
to set up. Where the config is restored some other way, replace those setup
instructions with your own text:

```bash
git-scan hook install --missing-config-help "Restore the config with your team's setup tool."
```

The text is kept in `missing-config-help` beside the user config file, since
the config itself is what's missing when the text is needed. Commits blocked
by findings and refused pushes are unaffected.

Each hook script is a fixed few lines, identical on every machine (it
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
deliberate edit from another tool's hook, so it leaves both alone. One
exception to refusing: another tool's `commit-msg` hook (a message linter,
say) is kept and the rest is installed; `hook status` then says that commit
messages are not scanned there, and `--force` replaces it.

```bash
git-scan hook status            # what each hook protects, and which one git runs
git-scan hook uninstall         # or --local
```

```
  global  ~/.config/git/hooks/pre-commit  (installed)
  local   .git/hooks/pre-commit  (absent)
  core.hooksPath = ~/.config/git/hooks
  pre-commit   installed  scans each commit's changes and every file, branch, and tag name
  commit-msg   installed  scans each commit message
  post-commit  absent     clean commits are not remembered; a push scans every unpushed commit (git-scan hook install --pre-push)
  pre-push     absent     pushes are not scanned (git-scan hook install --pre-push)
  git runs:  ~/.config/git/hooks/pre-commit  (installed)
```

The rows describe the hooks in the directory git will actually use for the
current repository; the last line is the pre-commit hook there, and its
state. Uninstalling leaves `core.hooksPath` as it is, since other hooks may
live in that directory.

### Push gate (optional)

The commit hooks can be skipped: `git commit --no-verify`, a rebase or
cherry-pick, a history rewrite, a commit made on another machine. The push
gate scans what a push sends:

```bash
git-scan hook install --pre-push
```

This adds two hooks beside `pre-commit` and `commit-msg`:

- `pre-push` runs `git-scan push-check`, which scans every commit the remote
  doesn't have yet in one pass (changes, message, author and committer, as
  `run --range` does) and refuses the push on findings, naming the commits;
- `post-commit` records each commit whose commit-time scan passed as `scanned`.

`scanned` is a cache: the gate skips commits that carry it and marks the
clean ones it scans, so a commit is scanned once. A rebased or rewritten
commit is a new commit without the mark; the gate scans it on the next
push, so a rewrite of many commits costs one scan rather than a refusal.
A message refused by the `commit-msg` hook keeps that commit from being
marked even if it is then committed with `--no-verify`.

```
git-scan: 3 commit(s) to push: 2 scanned now, 1 already scanned
```

**Requiring a review.** A judgment review of the unpushed commits can be
made a condition of every push:

```bash
git-scan hook install --pre-push --require-review     # or: git-scan config require-review on
git-scan review                                       # before pushing; a minute or more
```

A push is refused while any commit it sends lacks a clean review, with the
command to run. The scan does not stand in for the review and the review
does not stand in for the scan: both must pass. A project's `git-scan.yaml`
can require the review but not lift the requirement.

**Upgrading from 0.7 or earlier.** `git-scan mark` is gone, and so is
`push.require_marks`: a configuration that still has it fails to load with
the fix in the message. Turn the requirement into `git-scan config
require-review on` (or reinstall with `--require-review`); the review itself
is `git-scan review`. Commits marked `scanned` before the upgrade stay
scanned.

**Limitation:** the check is made against the remote being pushed to. With
several remotes, commits marked relative to one may be pushed to another
that is further behind; only the remote's own tracking refs and the tips
git reports are treated as already present.

## The privacy review

Regexes catch secret formats and the strings you listed. They cannot tell a
colleague's name in a commit message from a word, a personal use case in a
README from documentation, or an employer from a vendor. `git-scan review`
puts a judgment reviewer in front of every push for exactly those:

```bash
git-scan review                        # the unpushed commits of this branch
git-scan review --publish pr-body.md   # and the text that will be published with them
git-scan review --range A..B           # commits already made
```

What happens: git-scan computes the outgoing commits, builds one prompt
per batch from its packaged review instructions, your patterns and notes,
each commit's message and patch, and any `--publish` text, and runs a
headless agent CLI on it with no tools, so the prompt is the reviewer's
whole world and nothing on the machine can steer it. The JSON that comes
back is validated; every commit of a clean batch is recorded as reviewed.
Findings are printed with the value and the location, and nothing is
recorded for that batch:

```
  [FAIL] Privacy review (public; 2 commit(s); 2 finding(s))
         3f9c2a1b7d0e:contacts.txt:1 [names/high] Jane Doe
         3f9c2a1b7d0e:message [contextual/medium] ask Dana about the deploy
FAILED: fix the content in those commits and review again; nothing was recorded for them
```

Rules the command keeps:

- **The reviewer's own account is shown.** Each batch ends with the
  reviewer's one-line summary, the model that answered and the time it
  took, so a clean result is never just the word clean. A result that does
  not list every commit it was sent is refused and records nothing.
- **The reviewer's problems never pass as a clean review.** A runner that is
  not installed, exits non-zero, times out, or returns anything but a valid
  result records nothing and says the problem is the reviewer, not the
  content.
- **Reviewed commits are skipped on a rerun**, so an interrupted run or a
  new commit costs only what is new. A rewritten commit is a new commit and
  is reviewed again.
- **The default mode is public** for every repository. The mode is
  git-scan's decision, never the caller's: there is no flag for it. Relaxed
  is only for one repository marked as described in
  [Relaxed review](#relaxed-review).
- **Nothing accepts a result from outside.** The only writer of the review
  record is this command, after it has run the reviewer itself.

**The runner.** git-scan uses the first headless agent CLI it finds on
`PATH`, `claude` or `agy`, each started with its prompt on standard input,
no tools, and JSON output; it runs on whatever login that CLI already has,
so no API key is involved. Choose one, or name any command that reads a
prompt on stdin and prints the result JSON:

```bash
git-scan config review-runner claude
git-scan config review-runner "my-reviewer --json"
git-scan config review-timeout 900       # seconds per batch (default 600)
```

Large pushes are reviewed in batches of about 200 KB of patch text each
(`review.batch_bytes` in the configuration). The run takes a minute or
more per batch; an agent should start it as a background command.

### Relaxed review

Relaxed mode is for a private repository its owner keeps for personal
material: names and paths become notes, and framing is not judged. Every
other repository is reviewed as public. A repository is relaxed only when it
has been marked, one exact repository at a time:

```bash
git-scan review relax             # mark this repository (its exact origin URL)
git-scan review relax --list      # every marking on this machine
git-scan review relax --remove    # back to public
```

- **GitHub remotes** are marked only when GitHub reports the repository
  `private` at that moment (`gh api repos/OWNER/REPO --jq .visibility`, the
  REST API). Any other answer (`public`, `internal`), an error, no network,
  or no `gh` refuses. The review asks GitHub again every time, and a
  repository that is no longer reported private is reviewed as public.
- **Other hosts** can't be checked, so only the repository's owner can mark
  one, by typing its `host/owner/repo` at an interactive terminal to say
  that it is private. With no terminal (an agent's shell, a script) the
  command refuses. An agent can't make that statement.
- **A repository with no remote** (or a filesystem-path remote) is never
  relaxed.
- **Public-prospective.** A repository that may become public declares it
  in its own `git-scan.yaml`, and is then always reviewed as public and
  can't be marked:

  ```bash
  git-scan config public-prospective on --project
  ```

  A configuration layer can declare it but never lift it, and no
  configuration can make a repository relaxed.

The markings are kept in `${XDG_CACHE_HOME:-~/.cache}/git-scan/relaxed.json`,
not in any configuration file. A fresh machine or cloud session starts with
none, which is intended: losing the cache only makes reviews stricter.

**Upgrading from 0.8.** `git-scan review --relaxed` and `git-scan review
relaxed add/remove/list` are gone, and so is the `review.relaxed` list of
remote prefixes: a configuration that still has it fails to load until the
key is deleted. Mark each private repository with `git-scan review relax`
instead.

## MCP server

An agent can run the same scan through a stdio MCP server, `git-scan-mcp`,
installed with the `mcp` extra:

```bash
pipx install "git-scan[mcp] @ git+https://github.com/echoark/git-scan.git"
claude mcp add --scope user git-scan -- git-scan-mcp
```

It exposes `scan_repo` (the staged change, a commit message, or a range of
commits), `hook_status`, and `list_steps`. Registration for
other clients, the tools' arguments and results, and notes for agents are
in [MCP server](docs/MCP.md).

## Git identity

Optional. The commit's author email must be one you have allowed for the
remote. You say which emails may commit where; nothing is inferred and no
network call is made. Before a commit, the check reads the email git will
author it with; for commits already made (`--range`, `--commit`, the push
gate), it reads each commit's author and committer emails, judged by the
same rules against the remote being pushed to.

**Why this exists when git already has `user.email`.** Git's config answers
"which address do I use here"; it can't say "this address must never be used
there". If you use one GitHub account and one author address in every
repository on the machine, set it once in your global git config and skip
this section. The rules earn their keep when the right address depends on
the repository: a personal account plus an employer address, or a customer
organization that issued you an address of its own. Then every new clone
starts with the inherited global address, and nothing stops a commit under
the wrong one until someone notices, possibly much later, when the fix is a
history rewrite. A rule turns that into a refused commit.

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
audits), and the release process.
