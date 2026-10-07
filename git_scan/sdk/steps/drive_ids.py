"""Google Drive/Doc/Sheet ID checks in changed lines."""

import re
from typing import List

from ..utils import CheckResult

DRIVE_PATTERNS = [
    re.compile(r"/d/[A-Za-z0-9_-]{20,50}"),
    re.compile(r"id=[A-Za-z0-9_-]{20,50}"),
    re.compile(r"folders/[A-Za-z0-9_-]{20,50}"),
]


def run_checks(repo_path: str, config=None, scan_input=None, **kwargs) -> List[CheckResult]:
    findings = []
    for ln in (scan_input.content if scan_input else []):
        for pattern in DRIVE_PATTERNS:
            for m in pattern.finditer(ln.text):
                findings.append(f"{ln.where()} {m.group(0)[:50]}")
    return [CheckResult("Google Drive IDs", not findings, findings[:10])]
