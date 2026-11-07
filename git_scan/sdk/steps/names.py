"""Personal patterns in file, branch, and tag names.

Names are checked across the whole repo on every scan, not only the
staged change: a branch or tag name is published with every push, and a
tracked file's name is visible to anyone who can see the repo.

Only literal-string patterns apply to names: the plain entries of the
configuration (no ``word_boundary`` / ``not_followed_by``, which are tuned
for prose and misfire on names like ``vendor-tasks/``), including the
entries derived from this machine whose match is a finding (the git author
name and email). Built-in secret patterns (categories token, key,
identifier) and ``.env`` values target content and would misfire on
hex-like file names; the email and SSN/EIN checks cover names themselves.
Judgment entries are not applied to names: a name has no acknowledgement
flow, and the parts of the author's name, the login name and the home
directory name are judgment entries by default.
"""

from typing import List

from ..utils import CheckResult
from .patterns import compile_patterns

CONTENT_ONLY_CATEGORIES = {"token", "key", "identifier", "env"}
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


def run_checks(repo_path: str, config=None, scan_input=None, exposure: str = "public",
               **kwargs) -> List[CheckResult]:
    if scan_input is not None and scan_input.scope == "message":
        return []      # a message scan reads no names; the commit's own scan does
    personal = [p for p in (config.scan_patterns_for(exposure) if config else [])
                if p.get("category") not in CONTENT_ONLY_CATEGORIES
                and not p.get("word_boundary") and not p.get("not_followed_by")]
    compiled = compile_patterns(personal)
    if not compiled:
        return [CheckResult("Patterns in names", True, [], skipped=True,
                            info="No personal patterns configured")]
    names = scan_input.names if scan_input else []
    return [
        _check("Patterns in file names", [n for n in names if n.source == "file"], compiled),
        _check("Patterns in branch names", [n for n in names if n.source == "branch"], compiled),
        _check("Patterns in tag names", [n for n in names if n.source == "tag"], compiled),
    ]
