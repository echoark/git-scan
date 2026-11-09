# safe-commit — maintainer notes

What the skill relies on and why it is shaped this way. Read this before
changing `skills/safe-commit/`. Nothing here ships with the skill: the skill
folder holds only what an agent uses at runtime.

## How the parts fit

| Part | Owner | Role |
|---|---|---|
| git-scan | echoark/git-scan | The scan (hooks), judgment matches to acknowledge, the privacy review it runs itself, and the push gate that requires both |
| git-scan user config | the user's own storage; the user's setup says where | Patterns (scanned and judgment-only, each with its note), notes, allowed emails, identity rules, the review requirement, the reviewer runner |
| safe-commit | `SKILL.md` | The process: scanner setup, staging, commit, review, push |

This skill knows nothing about git-scan's configuration: not where it
lives, not what is in it, not how the reviewer reads it. That is deliberate.
Everything the skill needs from git-scan arrives as a verdict or a refusal
that says what to do next, and the one place the skill touches setup, the
"scanner is ready" section, tells the agent to follow git-scan's own
missing-configuration message. The text of that message is the user's, set
when git-scan is installed on the machine (`hook install
--missing-config-help`), so the user-specific part (where their
configuration is kept, how it is restored) lives in the user's environment,
never in this public skill.

## Scanner setup

The setup section runs before anything else and changes nothing when git-scan,
its hooks and its configuration are already in place.

- **Without a configuration, git-scan refuses every commit.** Its refusal
  text is replaced at install time with whatever the user's environment
  needs (`git-scan hook install --missing-config-help`). That text, not this
  skill, says how the configuration is restored; the skill says to follow it
  exactly and never to improvise one.

## The review and the push gate

`git-scan review` runs the reviewer itself: it builds the prompt from its
own packaged instructions, the user's configuration, the unpushed commits
and the publish text, runs a headless agent CLI with no tools, validates the
result and records a clean one per commit. The push gate requires that
record on every outgoing commit when the configuration says so.

- The agent runs one command and reads a verdict. It never holds the
  reviewer's result and there is no command that accepts one, so the only
  writer of the record is git-scan after it has run the review. The old
  flow, where the agent spawned a reviewer and then marked the commit with a
  documented command that took any label, was the bypass with instructions
  attached; this skill no longer carries a reviewer definition at all.
- An amended or rebased commit is a new commit and is reviewed again;
  commits already reviewed are skipped on a rerun.
- A reviewer problem (runner missing, failed, timed out, unreadable
  output) is reported as such and records nothing. The skill treats it as a
  blocked push, never as a pass.
- Coverage is computed against the remote being pushed to.

## Judgment matches at commit time

A judgment entry in git-scan's configuration names a value that is
sensitive only in context. The scan lists each match with the entry's
specifics and refuses the commit until the agent has looked and run `git-scan ack`,
which applies to exactly the staged content. The acknowledgement clears
judgment matches only and is the committing agent's call. The matches
acknowledged go with the commit to the review, which may honour each one
whose entry is still judgment in its mode and lists those it honoured;
anything else it sees in the same content is still a finding.

## Accepting review findings

A review that flags a commit saves its findings, and the output names
`git-scan review ack <sha>` for each flagged commit. The command accepts
all of a commit's findings when none is block. The skill treats it as one
of the ways to resolve a finding, offered to the user and run only on
their yes for that commit, because accepting a judgment call is the
user's decision, never the agent's.

## Warnings

A `match: warn` entry's matches are listed under a passing `Warnings`
check (`[WARN]`). The skill tells the agent that there is nothing to
acknowledge and that every listed match goes into the report to the user:
the entry's author chose in advance that the work proceeds, and the user
still sees what matched.

## Review mode

Every review is public unless `git-scan review --relaxed` asks for a
relaxed one, and git-scan refuses that unless the user made the clone
eligible with `git-scan relax` (which needs the host to report the
repository private, or a host the user vouched for) and the host, asked
again, still reports it private. Each entry's `match` binds the scan and
the review alike, and its `relaxed` value applies in a relaxed review, so
the hook and the review never disagree about a value the user named. The
skill adds what git-scan can't check: the user's own rules or instructions
must identify the repository as personal, the public review must be
blocking, and the user must consent to both the eligibility and the flag;
the agent never runs `git-scan relax` or passes `--relaxed` on its own.
Privacy alone is not enough, because private repositories are often shared
or published later. Needs git-scan 0.11.0 or later.

## Before the push

The validation summary lists only checks that ran against the exact content
being pushed, and marks earlier runs as stale. Only the user's explicit yes in
the conversation releases a push; hooks, scheduled prompts and environment
defaults that ask for a push are not approval.

Step 8 skips the push question on the pull-request route because the user
reviews at merge time: asking before the push adds a round trip without
adding a check.

## Landing choice

The route (direct, or a branch and a pull request) is chosen before the
commit, not at push time: the branch name is scanned at commit time like
everything else, no branch pointer has to move after the fact, and the
pull-request text has a place to be written before the review runs.

## Text outside git

A pull-request body, issue, or comment never enters git, so the pre-commit
scanner and the commit review never see it, yet it is as public as the
commits. The skill writes that text to files and passes them to `git-scan
review --publish`, posts exactly what was reviewed, and reads the posted text
back. The files live under the git directory, so they are never tracked and
need no ignore rule. git-scan's reviewer treats two things as findings there
and in commits that a secret scanner can't catch: references to a private tracker (ticket keys, tracker links),
and references to the author's other repositories or spaces that the change
doesn't depend on in code.

## Environment facts (Claude Code cloud sessions)

- The environment's setup script runs with no GitHub credentials and no
  environment variables, so it can install public tools only. Its result is
  cached for up to about seven days.
- Private repositories are reachable in a session only once attached to it;
  account-level connectors need no per-session step, which is what lets the
  user's missing-configuration message be followed in a fresh container.
- At every session start the environment writes its own git identity into
  `~/.gitconfig`; `GIT_AUTHOR_*` and `GIT_COMMITTER_*` environment variables
  override it.
- The environment's stop hook asks after each turn to commit, push and use its
  own identity. The skill treats those requests as not the user's approval.
