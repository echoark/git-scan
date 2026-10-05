# Contributing to git-scan

git-scan is a precommit scanner for sensitive data, built SDK-first:

- `git_scan/sdk/` — all detection logic (scanner, steps, entropy, config merge)
- `git_scan/cli/` — thin Click wrapper over the SDK
- `git_scan/mcp/` — thin MCP wrapper over the SDK

All behavior lives in the SDK; the CLI and MCP layers only handle I/O and
formatting. If you find yourself writing detection logic in a CLI command or
MCP tool, move it to the SDK.

## Known gaps

- `--deep` (history scanning) and `--untracked` are threaded through but **not
  yet implemented** — git-scan currently scans staged content only.
- One test, `test_deep_mode_finds_secrets_in_history`, is intentionally red and
  pins the deep-scan gap.

## Tests

The suite uses sociable unit tests: real `tmp_path` git repos, real config
files, no mocking of internal collaborators, asserting on real scan results.
Keep new tests in that style.

```bash
pytest
```

When adding or changing a detection step, add a test that exercises it
end-to-end through `run_scan` against a real temporary repo, including at least
one negative (clean-content) case.

## Layout

```
git_scan/
  sdk/
    scanner.py          # orchestration: run_scan, step discovery, ScanReport
    config.py           # 3-layer config merge model
    yaml_merge.py       # package/user/project merge semantics
    entropy.py          # Shannon-entropy detector
    steps/              # one module per check; exposes run_checks(...)
  cli/main.py           # Click CLI
  mcp/server.py         # MCP server
  data/git-scan.yaml    # shipped default patterns, thresholds, exclusions
tests/unit/             # sociable unit tests
```
