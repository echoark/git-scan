"""Personal patterns in file, branch, and tag names.

Names are checked across the whole repo on every scan, not only the
staged change: a branch or tag name is published with every push, and a
tracked file's name is visible to anyone who can see the repo.

Only plain patterns apply to names. Built-in secret patterns (categories
token, key, identifier) target content and would misfire on hex-like file
names. Patterns with word_boundary or not_followed_by are tuned for prose
(e.g. a company name that is fine inside product names) and would misfire
on names like ``vendor-tasks/``.
"""

from typing import List

from ..utils import CheckResult, run_cmd
from .patterns import compile_patterns

CONTENT_ONLY_CATEGORIES = {"token", "key", "identifier"}
MAX_FINDINGS = 10


def _names(repo_path: str, cmd: str) -> List[str]:
    rc, stdout, _ = run_cmd(cmd, repo_path)
    return [n for n in stdout.splitlines() if n.strip()] if rc == 0 else []


def _check(title: str, names: List[str], compiled) -> CheckResult:
    findings = []
    for name in names:
        for label, regex in compiled:
            if regex.search(name):
                findings.append(f"{name} [{label}]")
                break
    info = f"{len(names)} names"
    return CheckResult(title, not findings, findings[:MAX_FINDINGS], info=info)


def run_checks(repo_path: str, config=None, deep: bool = False, **kwargs) -> List[CheckResult]:
    patterns = [p for p in (config.patterns if config else [])
                if p.get("category") not in CONTENT_ONLY_CATEGORIES
                and not p.get("word_boundary") and not p.get("not_followed_by")]
    if not patterns:
        return [CheckResult("Patterns in names", True, [], skipped=True,
                            info="No personal patterns configured")]
    compiled = compile_patterns(patterns)
    return [
        _check("Patterns in file names", _names(repo_path, "git ls-files"), compiled),
        _check("Patterns in branch names", _names(
            repo_path, "git for-each-ref --format='%(refname:short)' refs/heads refs/remotes"), compiled),
        _check("Patterns in tag names", _names(
            repo_path, "git for-each-ref --format='%(refname:short)' refs/tags"), compiled),
    ]
