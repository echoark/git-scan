# Contributing to git-scan

## Project structure

```
git_scan/
  sdk/                  # all behavior
    sources.py          # what a scan reads: diff parsing, names, optional scopes
    scanner.py          # orchestration: config check, input collection, steps
    config.py           # three-layer config, validation, pattern provenance
    user_patterns.py    # patterns add/remove/restore/clear/list, settings
    hooks.py            # pre-commit hook installation
    entropy.py          # Shannon-entropy detector and exclusions
    yaml_merge.py       # layer merge (managed snippet; see below)
    steps/              # one module per check; exposes run_checks(...)
  cli/main.py           # thin Click wrapper over the SDK
  mcp/server.py         # thin MCP wrapper over the SDK
  data/git-scan.yaml    # built-in patterns, thresholds, exclusions
tests/unit/             # sociable unit tests
```

All behavior lives in `git_scan/sdk/`. The CLI and MCP layers parse input,
call the SDK, and format output. Logic found in `cli/` or `mcp/` moves to
the SDK.

## Scanning model

**One input, many checks.** `sources.collect()` builds a `ScanInput` once
per scan, and every check reads from it. A check never runs git itself.

- `ScanInput.content` is a list of `Line` items parsed from git's own diff
  output: `git diff --cached -U0 -M` for the staged change, plus `git diff`
  with `--unstaged` and whole untracked files with `--untracked`. Each item
  carries its text, file, line number, kind (`added` / `removed`), and
  source. `Line.where()` is the location label every finding starts with.
- `ScanInput.names` is every tracked file path, branch, and tag, as `Line`
  items of kind `name`.

Design decisions behind this:

- **Removed lines are scanned.** Deleting a secret does not remove it from
  history; reporting it on the way out is the reminder to rewrite history.
- **No context lines (`-U0`).** Unchanged neighbours of a change are never
  reported, so a busy file does not get re-audited on every commit. Content
  already in the repository is the job of an audit, not the hook.
- **Rename detection stays on (`-M`).** A pure move produces no content
  lines; an edited move shows its edits.
- **Binary files produce no lines**, so they never reach the entropy check.
- **`core.quotepath=false`** so non-ASCII file names arrive as text rather
  than octal escapes.
- **Checks match one line at a time.** Multi-line secrets such as private
  keys are caught by their header line.

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
- **`patterns remove` means "stop applying this"**, whatever layer the
  entry lives in: it deletes the user's own entry, or writes
  `disabled: true` for a built-in (built-ins cannot be deleted).
  `patterns restore` drops that override. Users never choose between
  deleting and disabling.
- **Single settings are named `config` subcommands** with their own
  validation (`large-amount`, `entropy-enabled`, …); lists of things
  (`patterns`) are a command group with `add` / `remove` / `list`. Don't
  add a generic `config set KEY VALUE`.
- **The CLI is the documented way to change config.** Writing a layer
  rewrites it from its parsed form, so hand-written comments are lost;
  that is accepted.

## Testing

```bash
make test                       # creates .venv and runs the unit tests
.venv/bin/python -m pytest -q   # after make setup
.venv/bin/python -m pytest -q --durations=10   # find slow tests
```

Sociable unit tests only: real temporary git repositories, real config
files, real `git` subprocesses, the real CLI through Click's `CliRunner`,
and for the hook, a real `git commit` that runs the installed hook. Nothing
is mocked. The suite is around 125 tests in under 10 seconds; any single
test over half a second is suspect (the end-to-end hook test, which spawns
git twice, is the slowest).

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

One test is marked `xfail`: history scanning (see below) is not built.

## Planned

**`git-scan audit`** — a manual, whole-repository scan, separate from the
hook because it is slow on large repositories and reports content the
current commit did not touch:

- all tracked files, read from disk, through every check;
- `--history`: every past commit's changed lines, commit messages, stash
  entries, and reflog, by streaming `git log -p` through the same diff
  parser `run` uses (commit messages arrive interleaved with each diff, so
  they come free), plus `git stash list -p` and `git reflog`.

**Commit messages at commit time** — git runs the pre-commit hook before
the message exists, so no pre-commit scanner can see it. A `commit-msg`
hook receives the final message file and can block; it would feed that
file to the same checks as one more source.

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
