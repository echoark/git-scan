# Changelog

Notable changes to git-scan, newest first. Versions follow
[CONTRIBUTING.md](CONTRIBUTING.md#version-management).

## 0.11.0 (unreleased)

### Added

- **Policy per exposure.** An entry may say what a match does in a
  `public` and in a `relaxed` clone: `finding`, `judgment` or `allow`
  (`patterns add / edit --public POLICY --relaxed POLICY`, shown by
  `patterns list`). Category defaults fill the gaps: secrets, emails,
  financial and account identifiers are findings in both; names, domains,
  usernames, employers and cities are findings in public and allowed in a
  relaxed clone; a `--judgment` entry is a judgment call where the default
  would be a finding; any other category is a finding in both. The commit
  scan and the privacy review follow the same policy, so the two never
  disagree about a value you named. A note is context for the reviewer,
  never policy.
- `git-scan review --public` reviews as public whatever the clone's
  exposure. The result of the MCP `scan_repo` tool carries the exposure.
- **Gates an agent cannot loosen unilaterally.** Every command that loosens
  a gate (`ack`, `relax`, `patterns edit / remove / clear / exceptions`,
  `emails allow`, a `config` change that doesn't only turn a requirement
  on, `hook uninstall`, `hook install --force`) prints a one-line notice,
  asks the person at the terminal to confirm (`--yes` confirms
  non-interactively and takes responsibility) and writes an audit record;
  `git-scan audit` lists who did what, when, in which repository and how
  it was confirmed. `git-scan lock` records the catalog's hash, and a scan
  or review whose catalog changed since fails closed until a person locks
  it again at a terminal; a change made with `--yes` leaves the lock stale
  on purpose. `git-scan ack --range A..B` acknowledges every commit in a
  range. `docs/agent-permissions.md` gives the harness rules that make
  these commands prompt the human, and `.github/workflows/gate.yml` is a
  reusable GitHub Action that re-scans (and optionally reviews) every push
  or pull request from a catalog in a secret. The README says what each
  layer can and cannot enforce.

### Changed

- **The reviewer is held to your entries' policy.** The catalog it gets
  carries each entry's policy for the exposure and flags the entries the
  scanner found in the batch; a finding names the entry it applied, and
  git-scan checks the verdict against the entry itself, so a judgment
  entry is never escalated to "a finding in every mode" by inference and
  an allowed value is reported as a note naming the entry. The exposure
  table applies only to values no entry names.
- **One exposure for the scan and the review.** A clone made eligible with
  `git-scan relax` is relaxed for both: the scan does not apply entries
  allowed in a relaxed clone (its output says `exposure: relaxed`), and
  `git-scan review` is relaxed without `--relaxed`, after asking the host
  again; an eligible clone the host no longer reports private refuses the
  review instead of running it public. `--relaxed` still asks explicitly
  and is refused when the clone isn't eligible. A clone never made
  eligible is public, as before.

- **Patterns ignore letter case by default.** Every pattern, built in,
  derived or your own, now matches regardless of case unless its entry
  says `ignore_case: false` (`patterns add --no-ignore-case`, `patterns
  edit --no-ignore-case`), for a value that is personal in one spelling
  only. An explicit `ignore_case: false` in an existing file is honoured;
  `ignore_case: true` is now the default and no longer written.
  `word_boundary` still defaults to false.
- **The identity patterns derived from the machine are entries.** The git
  author name and its parts, the git email, the login name, the home
  directory name and `.env` values used to be compiled with no flags, no
  id and no way to adjust them. They now form the `environment` layer of
  the configuration, between the built-in and the user layer, with stable
  ids (`git-name-full`, `git-name-first` / `git-name-last` or
  `git-name-part-N`, `git-email`, `local-username`, `home-dir`,
  `env-KEY`). `patterns list` shows them, and `patterns edit`, `patterns
  exceptions`, `patterns remove` and `patterns restore` act on them by id;
  a change is stored in your layer as an override, and the derived value
  is never written to a file. Name parts, the login name and the home
  directory name are judgment entries by default: a match is listed for a
  look, not refused. The full name, the email and `.env` values remain
  findings. An entry of yours whose pattern equals a derived value governs
  and the derived entry is dropped.
- Findings on derived patterns are labelled by category (`[name]`,
  `[email]`, `[username]`, `[env]`) like every other finding, and the
  `Patterns` check's count of environment patterns now counts the derived
  entries that are findings.
- Judgment entries are not applied to file, branch and tag names, so the
  login name and the name parts no longer block there; the git name and
  email still do.
