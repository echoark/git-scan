# Contributing to git-scan

## Project structure

```
git_scan/
  sdk/                  # all behavior
    sources.py          # what a scan reads: staged diff, message, commit log, names
    scanner.py          # orchestration: config check, input, steps, marks, push scan
    config.py           # three-layer config, validation, pattern provenance
    user_patterns.py    # patterns add/remove/restore/clear/list/exceptions, settings
    hooks.py            # hook installation and status: pre-commit, commit-msg, push gate
    marks.py            # marks on commits (git notes) and outgoing commits for a push
    entropy.py          # Shannon-entropy detector and exclusions
    yaml_merge.py       # layer merge (managed snippet; see below)
    steps/              # one module per check; exposes run_checks(...)
  cli/main.py           # thin Click wrapper over the SDK
  mcp/server.py         # thin MCP wrapper over the SDK (git-scan-mcp, stdio)
  data/git-scan.yaml    # built-in patterns, thresholds, exclusions
tests/unit/             # sociable unit tests
docs/MCP.md             # MCP server: install, registration, tools
```

All behavior lives in `git_scan/sdk/`. The CLI and MCP layers parse input,
call the SDK, and format output. Logic found in `cli/` or `mcp/` moves to
the SDK.

## Development setup

```bash
git clone https://github.com/echoark/git-scan.git && cd git-scan
make setup                      # python3 -m venv .venv; pip install -e ".[dev]"
                                # dev extras include mcp (<2, the FastMCP API) so the server is tested
.venv/bin/python -m pytest -q
.venv/bin/git-scan run          # the editable install, against this repository
```

The editable install is for development only. A machine's hook should run
a pinned release (`pipx install git+...@vX.Y.Z`), so that edits in a
working tree never change what the hook does.

## Scanning model

**One input, many checks.** `sources.collect()` builds a `ScanInput` once
per scan, and every check reads from it. A check never reads git for the
text it scans; only the text source differs between kinds of scan.

- `ScanInput.content` is a list of `Line` items. Each carries its text,
  file, line number, kind (`added` / `removed`), source, and for history
  the commit it came from. `Line.where()` is the location label every
  finding starts with. The sources, one per `ScanInput.scope`:
  - `staged`: git's own diff output, `git diff --cached -U0 -M`, plus
    `git diff` with `--unstaged` and whole untracked files with
    `--untracked`.
  - `message`: a commit message being written (`message_lines`). Comment
    lines and everything from the scissors line on are dropped, as git's
    cleanup drops them; the comment prefix is read from git config.
  - `commits`: one `git log -p --diff-merges=first-parent -U0 -M` over a
    range, one commit, or an explicit list (`range_lines`, `commit_lines`,
    `commits_lines`). Each record is NUL-separated (id, author email,
    committer email, message, patch), so no message text can be taken for
    a field boundary. The patch goes through the same diff parser as the
    staged diff; the message becomes `message` lines; the commit id and
    dates are never scanned.
- `ScanInput.names` is every tracked file path, branch, and tag, as `Line`
  items of kind `name`; empty for a message scan, whose commit has its own.
- `ScanInput.identities` holds a history scan's author and committer
  emails as `Line` items of kind `identity`. Only the git identity check
  reads them, against the identity rules; they are not content, so the
  email check never sees them.

Design decisions behind this:

- **Removed lines are scanned.** Deleting a secret does not remove it from
  history; reporting it on the way out is the reminder to rewrite history.
- **No context lines (`-U0`).** Unchanged neighbours of a change are never
  reported, so a busy file does not get re-audited on every commit. Content
  already in the repository is the job of an audit, not the hook.
- **Rename detection stays on (`-M`).** A pure move produces no content
  lines; an edited move shows its edits.
- **Binary files produce no lines**, so they never reach the entropy check.
- **History is scanned commit by commit, in one process.** Each commit's
  own patch against its first parent, as a per-commit scan would read it,
  but from a single `git log -p`. A net diff of the range would be one
  process too, and would hide anything added and removed inside it.
- **Messages get the content rules unchanged.** No message-only
  exceptions: a full SHA is refused by `hex-40` like anywhere else, and the
  docs tell people to cite short SHAs.
- **`core.quotepath=false`** so non-ASCII file names arrive as text rather
  than octal escapes.
- **Checks match one line at a time.** Multi-line secrets such as private
  keys are caught by their header line.

**Which checks apply to identities and messages.** The git identity check
is the only reader of `identities`, and reports nothing for a message scan
(the commit's pre-commit scan checks the identity it will use). The names
checks likewise report nothing for a message scan.

**Which checks apply to names.** Literal-string and anchored-format checks
do: personal patterns, the user's own names from the environment, emails,
SSN/EIN. Shape and statistical detectors do not: tokens, UUIDs, hex
strings, entropy, dollar amounts. Names are full of hashes and version
strings and those detectors would misfire. Personal patterns with
`word_boundary` or `not_followed_by` are also excluded from names: they are
tuned for prose and misfire on names like `vendor-tasks/`.

**Checks declare themselves by existing.** Any module in `sdk/steps/` with a
`run_checks(repo_path, config=None, scan_input=None, **kwargs)` function is
discovered and run. A step returns `CheckResult`s and never raises; the
runner converts an exception into a failing check.

### Adding a check

1. Add `sdk/steps/<name>.py` with `run_checks(...)`. Read lines from
   `scan_input.content` (and `scan_input.names` if the check belongs on
   names; see the rule above). Start every finding with `line.where()`.
2. Return one `CheckResult` per thing the output should report, with a
   short `info` string (counts, thresholds) for the pass line.
3. Add `tests/unit/test_scan_<name>.py`: real repositories, staged content,
   both a hit and a miss, and the removed-line case if it applies.
4. Add the check to the table in the README.

## Configuration

Three layers (built-in, user, project) merge with
`sdk/yaml_merge.py`, a managed copy of the `yaml-config-merge` snippet:
sections merge key by key, lists of entries merge by `id` with later layers
updating fields, scalars are replaced. Do not edit that file without
updating the snippet it is copied from.

Rules:

- **Every layer is validated on load** (`config.validate_layer`): known
  sections and fields, types, compiling regexes. A bare `patterns:` (YAML
  `None`) is rejected rather than merged, because `None` would replace the
  built-in list. Validation failures become a failing `Configuration`
  check so the hook blocks.
- **`pattern` is required after the merge, not per layer**, so a project
  layer can patch an inherited entry (add `word_boundary`, say) without
  restating it.
- **The user layer's `patterns` key is the personal-patterns switch.**
  Absent: the scan fails with instructions. Present, even as `[]`: the
  user has decided. `patterns add` creates the key; `patterns remove` on the
  last entry or `patterns clear` leaves `[]`.
- **`patterns remove` means "stop applying this" for the layer being
  written.** It deletes an entry that layer defines; for a pattern inherited
  from a lower layer (a built-in, or with `--project` one of the user's own)
  it writes `disabled: true` into the target layer, which turns the pattern
  off for that layer's scope only. `patterns restore` drops that override.
  Users never choose between deleting and disabling.
- **A pattern entry's fields:** `id`, `pattern`, `category`,
  `word_boundary`, `not_followed_by` (list), `exclude_files` (list of
  globs matched against the whole repo-relative path, never the base name
  alone, so one file's exclusion can't cover same-named files elsewhere;
  the pattern is skipped there, and for a name check, when the name itself
  matches),
  `disabled`. `patterns edit` writes only the changed fields, which for a
  built-in means an override entry the merge applies.
- **`patterns exceptions add / remove` change one `not_followed_by` value;
  `edit --not-followed-by` replaces the list.** Both read the merged list
  the scan applies and write the whole result into the target layer, since
  the merge replaces a list field per entry: a project override therefore
  restates the user's values beside the new one. An emptied list in an
  override is written as `[]`, not dropped, so the lower layer's values
  don't come back. `remove` checks every value before writing anything.
- **`allowed_emails`** is a top-level list the email check ignores,
  managed by `git-scan emails allow / disallow / list`.
- **`identity`** is a top-level list of `{remote, emails}` rules, managed by
  `git-scan identity allow / remove / list`. `remote` is a `host/owner/repo`
  prefix matched segment by segment (never a glob); `emails` may use `*`.
  With no matching rule the identity check is *skipped*, never failed:
  enforcement exists only where the user has said what is allowed. The
  check makes no network calls and reads no CLI login state.
- **Single settings are named `config` subcommands** with their own
  validation (`large-amount`, `entropy-enabled`, …); lists of things
  (`patterns`) are a command group with `add` / `remove` / `list`. Don't
  add a generic `config set KEY VALUE`.
- **The CLI is the documented way to change config.** Writing a layer
  rewrites it from its parsed form, so hand-written comments are lost;
  that is accepted.

### Judgment entries

A pattern entry with `judgment: true` is validated like any other but never
compiled: `MergedConfig.scan_patterns` is what the patterns step compiles,
`judgment_patterns` what it skips, and the step's info line counts the
skipped ones so a user can see they are there. The split exists because one
configuration now serves two readers with opposite needs. The scanner wants
few, precise regexes, or it blocks ordinary commits; a reviewer that weighs
context wants every value that is sensitive somewhere, with the guidance
that says where. Deleting an entry to quiet the scanner used to cost the
reviewer that value. Moving it to judgment keeps it for the reader that
can use it. The regex check is skipped for judgment entries so a literal
with regex metacharacters is accepted; `patterns edit --no-judgment` runs
the check before handing an entry back to the scanner.

`note` (any entry) and the top-level `notes` list are text the scanner
never reads. `notes` combine across layers the way `push.require_marks`
do, since a merge would otherwise let a project layer replace the user's.
`patterns list --json` (`user_patterns.export_patterns`) is the one
exporter of all this for a reviewer; built-in patterns are left out of it
because they describe token formats, not the user.

## The hook script is a contract

`sdk/hooks.py` classifies each hook file by exact byte match against the
scripts git-scan has shipped for that hook name (`ALL_SCRIPTS`: `SCRIPTS`
for pre-commit, `MESSAGE_SCRIPTS` for commit-msg, `GATE_SCRIPTS` for the
push gate), and nothing else: `installed`, `outdated`, `other`, `absent`.
`hook install` always writes pre-commit and commit-msg; `--pre-push` adds
the gate's two. Rules:

- **The script is machine-independent.** No absolute paths, nothing
  derived from the installing machine. It adds `~/.local/bin` to `PATH` at
  run time instead of embedding an executable path.
- **Append to `SCRIPTS`, never edit an entry.** A changed script is a new
  entry; earlier ones stay so existing installs classify as `outdated` and
  upgrade cleanly. Changing the script is rare and deliberate.
- **Never inspect a hook's content.** An `other` file may be another tool's
  hook, a wrapper that calls git-scan, or an edited copy; git-scan can't
  tell and doesn't guess, so it reports `other`, refuses to replace it
  without `--force`, and never deletes it. No marker lines, no substring
  checks, no provenance records.
- **Another tool's commit-msg hook is kept, not a blocker.** Commit-message
  hooks are common (linters, ticket-id hooks), so an `other` commit-msg
  is left in place and the rest installs; the install output and the
  status row say commit messages are not scanned there. Any other `other`
  hook stops the install. `--force` replaces either.
- **Status reports facts:** the pre-commit state at each level, one row
  per hook (`HOOKS`) in the directory git will actually use
  (`core.hooksPath` if set at any level, else the repository's
  `.git/hooks`) with what it protects or what goes unprotected, then the
  pre-commit hook git runs. The MCP `hook_status` tool returns the same
  structure.

## The push gate

`sdk/marks.py` owns marks and finds the commits a push sends;
`scanner.push_scan` scans them. Rules:

- **Hooks stay one-line calls into the CLI.** `pre-push` runs `git-scan
  push-check "$@"`, `post-commit` runs `git-scan hook post-commit`,
  `commit-msg` runs `git-scan run --message "$1"`; their scripts follow the
  same contract as pre-commit (append never edit). All behavior is in
  Python.
- **The notes are internal.** Only `add_mark` / `add_marks` write
  `refs/notes/git-scan` and only `marks_on` / `marks_of` read it; callers
  use `git-scan mark LABEL`. One label per line. Bulk reads and writes are
  one git process each (`git log --notes`, `git fast-import`), so marking a
  few hundred rewritten commits is not a few hundred processes.
- **The gate scans; `scanned` is its cache.** Every outgoing commit without
  `scanned` goes through one `commits` scan, and the clean ones are marked.
  `scanned` is per commit: it says that commit's own changes, message, and
  identities passed, and nothing about its ancestors. When a scan of many
  commits fails, the failing groups are halved until each finding is
  pinned to a commit, so the clean ones are still marked at a cost of a
  few extra small passes; a finding in a name belongs to no commit and
  leaves none marked.
- **Other marks cover history.** A required mark is written after a check
  of the whole unpushed range, so it covers the marked commit and every
  unpushed commit before it. The scan never satisfies a required mark.
- **A pre-commit hook runs before the commit exists.** A passing staged scan
  records the staged tree in the git directory; a refused commit message
  deletes that record; the post-commit hook marks HEAD only if its tree is
  the recorded one, and consumes the record either way. So a message
  refused by the hook and then committed with `--no-verify` is unmarked,
  and the gate scans it.
- **Requirements only accumulate.** `required_marks` is read per layer and
  combined, never from the merged view (merging replaces lists), so a
  project layer can't drop a requirement the user layer sets.
- **Opt-in.** Without `--pre-push` nothing about pushing changes.

## No user config

A full scan without the user layer's `patterns` key runs no checks and
returns a refusal (`ScanReport.refusal`) instead: listing passes from checks
that had nothing personal to check would mislead. The refusal ends with setup
instructions, or with the text in `missing-config-help` beside the user layer
(`hook install --missing-config-help`). That text is kept outside the user
layer because the user layer is what's missing; it never appears in other
output. A `--step` run is not refused.

## Testing

```bash
make test                       # creates .venv and runs the unit tests
.venv/bin/python -m pytest -q   # after make setup
.venv/bin/python -m pytest -q --durations=10   # find slow tests
```

Sociable unit tests only: real temporary git repositories, real config
files, real `git` subprocesses, the real CLI through Click's `CliRunner`,
and for the hooks, a real `git commit` or `git push` that runs the
installed hooks. The MCP tools are called in-process
(`tests/unit/test_mcp.py`). Nothing is mocked. The suite is about 200
tests in about 30 seconds. The end-to-end hook tests are the slow ones:
each commit through the hooks starts git-scan twice (pre-commit and
commit-msg), so they take about a second each. Any other test over half a
second is suspect.

### The sandbox

`tests/conftest.py` applies one autouse fixture to every test. It hides the
machine the tests run on:

| Variable | Set to | Hides |
|---|---|---|
| `XDG_CONFIG_HOME` | a temp dir holding a user layer with `patterns: []` | the developer's personal patterns; also where `hooks.default_hooks_dir()` resolves |
| `GIT_CONFIG_GLOBAL` | a temp file with a test name and email | the developer's global git config, including `core.hooksPath` |
| `GIT_CONFIG_NOSYSTEM` | `1` | the system git config |
| `USER` | `testuser` | the developer's username in dynamic patterns |
| `GIT_AUTHOR_EMAIL` etc. | unset | any identity override in the shell |

Consequences, each pinned by a test in `tests/unit/test_isolation.py`:

- **Machine hooks never run.** With the global config hidden, a test
  repository has no `core.hooksPath`, so a `git commit` in a test runs no
  scanner the developer has installed. Tests do not need `--no-verify`
  (some use it anyway when the commit's content is deliberately bad).
- **Commits carry the sandbox identity**, never the developer's.
- **Nothing is written inside this repository.** Test repositories,
  config layers, hook directories, and git's "global" config all live
  under pytest's `tmp_path`, which is under the system temp directory.
  pytest keeps the three most recent runs and deletes older ones, so a run
  killed halfway leaves nothing that persists and nothing that could be
  committed. The only files a run creates here are `__pycache__/` and
  `.pytest_cache/`, both ignored.
- `install_hook` writes `core.hooksPath` into the temp global config, so
  the end-to-end hook test changes nothing on the machine.

Tests that need a logged-in GitHub CLI write their own `hosts.yml` under a
temp `GH_CONFIG_DIR`. No test reads the network.

**Test fixtures never contain the strings the scanner exists to catch.**
Use an obviously fake form (`"ghp_" + "a" * 36`, `example.com`,
`Vendorco`) or assemble the value at runtime (`"-".join(["123", "45",
"6789"])`) so no token-, address-, or ID-shaped text sits in the source.
The same applies to code comments and docstrings. A real-looking value in
a test is a leak the scanner would rightly block, and this repository's
own commits are scanned.

One test is marked `xfail`: history scanning (see below) is not built.

## Planned

**`git-scan audit`** — a manual, whole-repository scan, separate from the
hook because it is slow on large repositories and reports content the
current commit did not touch:

- all tracked files, read from disk, through every check;
- `--history`: every past commit, through the same `commits` source
  `run --range` uses, plus stash entries and the reflog (`git stash list
  -p`, `git reflog`).

**Table output** with per-check details and a `--summary` mode.

## Version management

Source of truth: `git_scan/__init__.py` `__version__`, mirrored in
`pyproject.toml` `version`. Both move together in the same commit as the
change that needs them. Documentation-only changes need no bump.

| Change | Bump |
|---|---|
| Bug fix | patch |
| Backwards-compatible feature | minor |
| Breaking change | major (0.x → 1.0.0 is a product decision, not mechanical) |

If a runtime change shipped without a bump, follow up with a commit titled
`Bump version to X.Y.Z` whose body names the unreleased changes it covers.

Release:

```bash
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main --tags
```

Users install from a tag (`pipx install git+...@vX.Y.Z`), so the README's
install line moves with each release.
