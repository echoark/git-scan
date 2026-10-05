"""SSN and EIN pattern checks on staged content."""

import re
from typing import List

from ..utils import CheckResult, get_staged_files, get_staged_content

SSN_PATTERN = re.compile(r"\b(\d{3})-(\d{2})-(\d{4})\b")
EIN_PATTERN = re.compile(r"\b(\d{2})-(\d{7})\b")


def run_checks(repo_path: str, config=None, deep: bool = False, **kwargs) -> List[CheckResult]:
    """Run SSN/EIN checks on staged files."""
    files = get_staged_files(repo_path)
    if not files:
        return [CheckResult("SSN/EIN patterns", True, info="No staged files")]

    findings = []
    for file_path in files:
        content = get_staged_content(repo_path, file_path)
        if content is None:
            continue
        for line_num, line in enumerate(content.splitlines(), 1):
            for m in SSN_PATTERN.finditer(line):
                val = m.group(0)
                if not val.startswith("000") and not val.startswith("666"):
                    findings.append(f"{file_path}:{line_num} SSN-like: {val}")
            for m in EIN_PATTERN.finditer(line):
                findings.append(f"{file_path}:{line_num} EIN-like: {m.group(0)}")

    passed = len(findings) == 0
    return [CheckResult("SSN/EIN patterns", passed, findings[:10])]
