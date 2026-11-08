# Changelog

Notable changes to git-scan, newest first. Versions follow
[CONTRIBUTING.md](CONTRIBUTING.md#version-management).

## 0.11.0 (unreleased)

### Changed

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
