"""Recording a privacy review so the push gate can require it.

The review itself is a judgment pass over the unpushed commits by a reviewer
that reads the configuration (``git-scan patterns list --json``). It ends
with a structured result. ``record`` accepts that result, checks that it is
a clean pass covering the commits about to be pushed, and marks the tip
``reviewed``. Nothing else writes that mark.

The result is JSON, either bare or inside a fenced block tagged
``privacy-guard-result``. Required: ``status`` equal to ``completed`` and an
empty ``findings`` list. Coverage is checked from ``commits`` (a list of
commit ids or prefixes) when present, else from ``scan_scope.unpushed_commits``
(a count), against the commits the current branch would push.
"""

from __future__ import annotations

import json
import re
from typing import List

from . import marks

_FENCE = re.compile(r"```privacy-guard-result\s*\n(.*?)\n\s*```", re.S)


class ReviewError(ValueError):
    """The result can't be recorded; the message says why."""


def load_result(text: str) -> dict:
    """Parse a review result from bare JSON or a report containing the fenced block."""
    m = _FENCE.search(text)
    raw = m.group(1) if m else text
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ReviewError(f"not a review result: no JSON found ({e})")
    if not isinstance(data, dict):
        raise ReviewError("not a review result: expected a JSON object")
    return data


def unpushed(repo_path: str = ".") -> List[str]:
    lines, remote = marks.current_branch_push(repo_path=repo_path)
    return marks.outgoing_commits(lines, remote, repo_path)


def record(result: dict, repo_path: str = ".") -> dict:
    """Record a clean review on HEAD. Returns ``{"sha", "covered"}``."""
    status = result.get("status")
    if status != "completed":
        raise ReviewError(f"the review did not complete (status: {status!r}); nothing recorded")
    findings = result.get("findings")
    if not isinstance(findings, list):
        raise ReviewError("the result has no 'findings' list; nothing recorded")
    if findings:
        raise ReviewError(f"the review reported {len(findings)} finding(s); fix them and "
                          "review again. Nothing recorded")
    outgoing = unpushed(repo_path)
    if not outgoing:
        raise ReviewError("nothing to record: the current branch has no unpushed commits")
    listed = result.get("commits")
    if isinstance(listed, list) and listed:
        ids = [str(c).strip() for c in listed if str(c).strip()]
        uncovered = [s for s in outgoing if not any(s.startswith(i) for i in ids)]
        unknown = [i for i in ids if not any(s.startswith(i) for s in outgoing)]
        if uncovered or unknown:
            parts = []
            if uncovered:
                parts.append("unpushed commits the review did not list: "
                             + ", ".join(s[:12] for s in uncovered))
            if unknown:
                parts.append("listed commits that are not unpushed here: " + ", ".join(unknown))
            raise ReviewError("the review does not cover this push (" + "; ".join(parts)
                              + "). Review the unpushed commits and record that result")
    else:
        scope = result.get("scan_scope") or {}
        count = scope.get("unpushed_commits")
        if count != len(outgoing):
            raise ReviewError(f"the review covered {count!r} unpushed commit(s); this branch "
                              f"has {len(outgoing)}. Review the unpushed commits and record "
                              "that result")
    sha = marks.add_mark(marks.REVIEWED, "HEAD", repo_path)
    return {"sha": sha, "covered": len(outgoing)}
