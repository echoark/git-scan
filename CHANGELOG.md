# Changelog

Notable changes to git-scan, newest first. Versions follow
[CONTRIBUTING.md](CONTRIBUTING.md#version-management).

## 0.12.0 (unreleased)

### Added

- **Acknowledgements reach the review.** `git-scan ack` now records which
  matches it accepted (entry, file, added or removed, line, text), and the
  commit carries them. The privacy review lists them beside the commit;
  the reviewer may leave an acknowledged match out of its findings when
  its entry is a judgment entry in that mode, and reports a claim on any
  other entry as a finding. `git-scan review` prints the acknowledgements
  it honoured.
- **`git-scan review ack <sha>`.** A review that flags a commit saves its
  findings with the commit; `review ack` shows them and records the commit
  as reviewed when none of them is block. A finding that fits no entry
  counts as a judgment call there. The commits of a flagged batch that
  have no findings are recorded as reviewed by that same run.

### Changed

- The built-in `people` entry is now `match: judgment` (a note when
  relaxed, as before): another person's name is sensitive only in context.

## 0.11.0 (unreleased)

### Added

- **Every entry says what a match does.** `match` is one of `block` (the
  default), `judgment` (the scan fails until you acknowledge the match;
  the reviewer applies the entry's specifics and reports the match for a
  look, never escalating it), `warn` (the scan lists the match under a
  passing `Warnings` check, printed as `WARN`; the review shows a note) or
  `ignore` (not compiled, not sent to the reviewer). `relaxed` takes the
  same values and applies in a relaxed review alone. `patterns add` and
  `patterns edit` take `--match` and `--relaxed`; `patterns list` shows
  them. Any layer may set either, tighter or looser.
- **Entries with specifics only.** An entry may name what it covers in
  words (`--specifics`) instead of, or beside, a pattern; such an entry is
  for the privacy review alone. git-scan ships eight of them beside the
  token formats: `user-home-path` (a pattern), `credentials`, `financial`,
  `account-ids`, `people`, `trackers`, `personal-framing` and `history`,
  each with its own `match` and `relaxed`, so the review's rules are
  entries you can see and tune.
- `git-scan config migrate` rewrites your user file and the repository's
  `git-scan.yaml` from the fields earlier versions wrote (`note` to
  `specifics`, `judgment: true` to `match: judgment`, `disabled: true` to
  `match: ignore`), showing the diff first and writing on confirmation.

### Changed

- **The reviewer is bound to the entry.** The prompt is now the merged
  entries by id, built in, derived and yours, each with its pattern,
  specifics and the match for the review's mode, plus one standing
  instruction about variants; the exposure table and the prose about what
  to look for beyond the catalog are gone. The reviewer reports each
  finding under the entry it matched, and git-scan applies that entry's
  `match`, never the reviewer's label. A finding that matches no entry
  blocks in both modes.
- **Every review is public unless asked otherwise.** `git-scan review
  --relaxed` asks for a relaxed review of an eligible clone, after asking
  the host again; without it a review is public whatever the clone's
  eligibility, and the commit scan never reads that eligibility. The
  derived identity entries say `relaxed: warn`, so a relaxed review shows
  the owner's name, email and login as notes.
- `patterns remove` on a built-in or derived entry writes `match: ignore`
  instead of `disabled: true`; `patterns restore` drops it.
- **Removed:** the entry fields `note`, `judgment` and `disabled` (refused
  with a message naming `config migrate`), the `--note` and `--judgment`
  flags, `review --public`, the `exposure:` line of the scan output and
  the `exposure` field of the MCP `scan_repo` result.

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
  is never written to a file. The parts of the name are judgment entries
  by default: a match is listed for a look, not refused. The full name,
  the email, the login name, the home directory name and `.env` values
  are findings. An entry of yours whose pattern equals a derived value
  governs and the derived entry is dropped.
- **Entries carry `specifics`:** the thing an entry matches, in words
  ("an AWS access key id", "the owner's email address"), on every built-in
  and derived entry, for the privacy review to read beside the value. The
  17 built-in token, key and identifier formats say `ignore_case: false`
  explicitly, since their case is part of the format.
- Findings on derived patterns are labelled by category (`[name]`,
  `[email]`, `[username]`, `[env]`) like every other finding, and the
  `Patterns` check's count of environment patterns now counts the derived
  entries that are findings.
- Judgment entries are not applied to file, branch and tag names, so the
  name parts no longer block there; the git name, email and login name
  still do.
