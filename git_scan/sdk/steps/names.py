"""Personal patterns in file, branch, and tag names.

Names are checked across the whole repo on every scan, not only the
staged change: a branch or tag name is published with every push, and a
tracked file's name is visible to anyone who can see the repo.

Only literal-string patterns apply to names: the user's plain personal
patterns (no ``word_boundary`` / ``not_followed_by``, which are tuned for
prose and misfire on names like ``vendor-tasks/``), plus the user's own
names from the environment (username, home directory, git name parts).
Built-in secret patterns (categories token, key, identifier) target content
and would misfire on hex-like file names; the email and SSN/EIN checks
cover names themselves.
"""

import re
from typing import List

from ..utils import CheckResult
from .patterns import compile_patterns, identity_patterns

CONTENT_ONLY_CATEGORIES = {"token", "key", "identifier"}
MAX_FINDINGS = 10


def _check(title: str, lines, compiled) -> CheckResult:
    findings = []
    for ln in lines:
        for entry in compiled:
            if entry.applies_to(ln.text) and entry.search(ln.text):
                findings.append(f"{ln.where()} [{entry.label}]")
                break
    return CheckResult(title, not findings, findings[:MAX_FINDINGS],
                       info=f"{len(lines)} names")


def run_checks(repo_path: str, config=None, scan_input=None, **kwargs) -> List[CheckResult]:
    personal = [p for p in (config.patterns if config else [])
                if p.get("category") not in CONTENT_ONLY_CATEGORIES
                and not p.get("word_boundary") and not p.get("not_followed_by")]
    compiled = compile_patterns(personal)
    from .patterns import Compiled
    compiled += [Compiled(label, re.compile(rx), []) for label, rx in identity_patterns(repo_path).items()]
    if not compiled:
        return [CheckResult("Patterns in names", True, [], skipped=True,
                            info="No personal patterns configured")]
    names = scan_input.names if scan_input else []
    return [
        _check("Patterns in file names", [n for n in names if n.source == "file"], compiled),
        _check("Patterns in branch names", [n for n in names if n.source == "branch"], compiled),
        _check("Patterns in tag names", [n for n in names if n.source == "tag"], compiled),
    ]
