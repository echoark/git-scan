# Reviewing content that was already pushed

Sometimes commits reach a remote without a review — a push slipped through
in an environment with no review wired up, a push happened outside this
workflow, or a newer configuration should re-check content that was pushed
earlier. The review normally reads the unpushed commits; for commits
already on the remote, name the range:

```bash
git-scan review --range <baseline>..<tip>
```

`<baseline>` is the last commit that was on the remote before the
unreviewed push, `<tip>` the branch as pushed. Keep the exposure mode
matched to the remote the content went to: public for a public or shared
remote, which is the default. Commits already reviewed are skipped.

**If the review passes**, nothing more is needed.

**If the review finds something**, the content is already on the remote, so
fixing it locally is not enough on its own:

1. Fix the findings in a new commit on your real branch and push it, so the
   current tip is clean.
2. Recognize that the sensitive content still exists in the remote's
   **history**. Depending on severity, that may call for rotating any exposed
   credential immediately (treat a pushed secret as compromised), and for
   history rewriting or repository remediation that is beyond this skill's
   scope — surface it to the user rather than attempting it silently.
