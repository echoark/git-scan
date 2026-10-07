"""SSN and EIN pattern checks in changed lines and in file, branch, and tag names."""

import re
from typing import List

from ..utils import CheckResult

SSN_PATTERN = re.compile(r"\b(\d{3})-(\d{2})-(\d{4})\b")
EIN_PATTERN = re.compile(r"\b(\d{2})-(\d{7})\b")


def run_checks(repo_path: str, config=None, scan_input=None, **kwargs) -> List[CheckResult]:
    findings = []
    lines = (scan_input.content + scan_input.names) if scan_input else []
    for ln in lines:
        for m in SSN_PATTERN.finditer(ln.text):
            val = m.group(0)
            if not val.startswith("000") and not val.startswith("666"):
                findings.append(f"{ln.where()} SSN-like: {val}")
        for m in EIN_PATTERN.finditer(ln.text):
            findings.append(f"{ln.where()} EIN-like: {m.group(0)}")
    return [CheckResult("SSN/EIN patterns", not findings, findings[:10])]
