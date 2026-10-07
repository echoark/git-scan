"""Email address checks in changed lines and in file, branch, and tag names."""

import re
from typing import List

from ..utils import CheckResult

EMAIL_PATTERN = re.compile(r"[a-z0-9._%+-]{2,}@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)

DEFAULT_ACCEPTABLE_DOMAINS = [
    "example.com", "example.org", "example.net", "test.com",
    "users.noreply.github.com",
    "acme.com",
]

DECORATOR_MODULES = [
    "click.", "cli.", "pytest.", "mcp.", "app.", "flask.",
    "config.", "profile.", "settings.", "router.", "api.", "auth.",
    "staticmethod", "classmethod", "property", "dataclass",
    "fixture", "mark.", "tool", "command", "group", "option",
]


def _is_ignored(email: str, allowed: set) -> bool:
    if email in allowed:
        return True
    if any(d in email for d in DEFAULT_ACCEPTABLE_DOMAINS):
        return True
    domain = email.split("@", 1)[-1]
    return any(domain.startswith(mod) for mod in DECORATOR_MODULES)


def run_checks(repo_path: str, config=None, scan_input=None, **kwargs) -> List[CheckResult]:
    allowed = {e.lower() for e in (config.raw.get("allowed_emails", []) if config else [])}
    findings, seen = [], set()
    lines = (scan_input.content + scan_input.names) if scan_input else []
    for ln in lines:
        for m in EMAIL_PATTERN.finditer(ln.text):
            email = m.group(0).lower().lstrip("+-")
            if email in seen or _is_ignored(email, allowed):
                continue
            seen.add(email)
            findings.append(f"{ln.where()} {email}")
    return [CheckResult("Email addresses", not findings, findings[:10])]
