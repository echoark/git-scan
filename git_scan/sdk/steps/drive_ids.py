"""Google Drive/Doc/Sheet ID checks on staged content."""

import re
from typing import List

from ..utils import CheckResult, get_staged_files, get_staged_content

DRIVE_PATTERNS = [
    re.compile(r"/d/[A-Za-z0-9_-]{20,50}"),
    re.compile(r"id=[A-Za-z0-9_-]{20,50}"),
    re.compile(r"folders/[A-Za-z0-9_-]{20,50}"),
]


def run_checks(repo_path: str, config=None, deep: bool = False, **kwargs) -> List[CheckResult]:
    """Run Google Drive ID checks on staged files."""
    files = get_staged_files(repo_path)
    if not files:
        return [CheckResult("Google Drive IDs", True, info="No staged files")]

    findings = []
    for file_path in files:
        content = get_staged_content(repo_path, file_path)
        if content is None:
            continue
        for line_num, line in enumerate(content.splitlines(), 1):
            for pattern in DRIVE_PATTERNS:
                for m in pattern.finditer(line):
                    findings.append(f"{file_path}:{line_num} {m.group(0)[:50]}")

    passed = len(findings) == 0
    return [CheckResult("Google Drive IDs", passed, findings[:10])]
