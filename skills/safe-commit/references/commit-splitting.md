# Honest commit splitting

The default is one commit per unit of work. Split into multiple commits only
when the user asks, or when the working tree holds genuinely independent
pieces of work — different features, different subjects, unrelated fixes.

When you split, these four rules keep history truthful: every commit should
represent a state that actually existed on disk and that someone could check
out, build, and reason about.

## Rule 1 — Split only at file boundaries

Each candidate commit must involve an **entirely separate set of files** from
the others. No file appears in more than one commit. Whole-file granularity
only.

This is the only split method that preserves the property that every commit's
content was, at some point, the real state of the files on disk.

## Rule 2 — Never split via hunk surgery, stash, or temp edits

Do **not** use `git add -p` (interactive hunk staging), `git stash` and
re-apply, or temporary file edits to separate one file's changes across
multiple commits. Those techniques produce commits whose content was never in
the working tree as a coherent unit — nobody tested that state, nobody wrote
it, it exists only because of the surgery.

If a single file contains changes that conceptually belong to two different
commits, the honest options are:

- Commit them together as one commit (simplest, and safe).
- Revert the file, redo one change, commit, redo the other, commit — real
  states, real typing, but slow and error-prone.

Pick the first unless there is a strong reason not to.

## Rule 3 — Don't silently un-test a tested state

If tests have already run on the combined working-tree state (files A, B, C, D
all changed together and tested as a unit), then splitting into "commit A+B"
followed by "commit C+D" creates two commits that were **never tested in that
form**. The tested unit was all four files together. Each subset might pass its
own tests, or might not — you don't know.

Safe options when testing has already happened on the combined state:

- **Split only if the subset is obviously independent** of the rest — no
  shared import, no shared function, no shared config, so a reviewer can
  confirm independence at a glance.
- **Re-test just the subset after splitting** — only when tests are fast,
  pure, and deterministic (seconds, local, unit-level). Stash the unrelated
  files, run tests, commit the tested subset, restore, repeat. Don't do this
  for slow or external-dependency tests — the retest cost usually exceeds the
  split benefit.
- **Give up and commit together.** Almost always the right answer when in
  doubt. One coherent combined commit beats two split commits whose individual
  correctness isn't verified.

When the user asked for splits and you are unsure whether a split honors this
rule, **say so**: explain the concern and ask whether to split-and-retest,
combine, or split-and-trust. Don't silently pick.

## Rule 4 — Each commit must stand on its own

A reader who checks out any single commit in isolation should see a coherent
state. Broken-in-the-middle commits make `git bisect` unusable and confuse
future archaeology. "Stand on its own" doesn't mean every commit is
independently shippable — it means the repository at that commit is
syntactically valid, imports resolve, and the commit represents a real state
someone could have had.

## The review is per-push, not per-commit

When committing a batch of N commits, do all N commits first (each through the
skill's Steps 1–7), then run `git-scan review` once at the end. It reads the
content of every unpushed commit in a single run — one invocation covers the
whole batch. Running it per commit is wasted effort and gives the review a
partial view of what will actually be pushed.
