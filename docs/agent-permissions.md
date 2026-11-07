# Agent permissions: the human gate

git-scan's own gates are honest about their limits: a command that loosens
one prints a notice, asks for confirmation (`--yes` non-interactively) and
writes an audit record, and a locked catalog fails scans closed when it
changes behind the commands' back. None of that stops an agent that owns
the shell. The gate that does is the harness the agent runs in: its
permission rules can make every loosening command wait for the human.

Set rules so these always prompt, whatever the session's permission mode:

| Command | Why it needs a human |
|---|---|
| `git-scan ack`, `git-scan ack --commit`, `git-scan ack --range` | acknowledges judgment matches: someone has to have looked |
| `git-scan relax` | makes the clone's exposure relaxed for the scan and the review |
| `git-scan patterns ...` | edits, removes, clears or excepts entries of the catalog |
| `git-scan emails allow` | makes the email check ignore an address |
| `git-scan config ...` | turns a requirement off, or points the review at another runner or model |
| `git-scan hook ...` | removes or replaces the hooks |
| `git-scan lock` | re-locks a changed catalog (works only at a terminal anyway) |
| `git commit --no-verify`, `git push --no-verify`, `git -c core.hooksPath=...` | skips the hooks |

## Claude Code

Add to `~/.claude/settings.json` (every project) or a project's
`.claude/settings.json`. `ask` rules win over `allow` rules, so a broad
`Bash(git-scan *)` or `Bash(git *)` allow elsewhere does not bypass them:

```json
{
  "permissions": {
    "ask": [
      "Bash(git-scan ack*)",
      "Bash(git-scan relax*)",
      "Bash(git-scan patterns *)",
      "Bash(git-scan emails allow*)",
      "Bash(git-scan config *)",
      "Bash(git-scan hook *)",
      "Bash(git-scan lock*)",
      "Bash(git commit *--no-verify*)",
      "Bash(git push *--no-verify*)",
      "Bash(git -c core.hooksPath*)"
    ]
  }
}
```

`git-scan run`, `git-scan review`, `git-scan audit`, `git-scan patterns
list` and `git-scan hook status` change nothing and can stay allowed. A
session in `bypassPermissions` mode asks nothing; keep agent sessions out
of that mode in repositories git-scan protects.

## Other agents

The same list applies. Gemini CLI and Antigravity read `excludeTools` and
tool allow-lists from their settings; put the commands above outside any
blanket shell allow-list so each one prompts. For a harness with no
per-command rules, the server-side gate is the human gate: see the README,
"Server-side gate".

## What this does not cover

A harness prompt is a decision point, not proof that the content was
looked at. The audit log (`git-scan audit`) says who confirmed what and
when, so a loosening approved in a hurry can still be found. The
server-side gate re-scans every push from the catalog in a secret,
independent of local acknowledgements, so a loosening on one machine does
not reach the shared branch unseen.
