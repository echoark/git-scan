"""Email address checks on staged content."""

import re
from typing import List

from ..utils import CheckResult, get_staged_files, get_staged_content

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


def run_checks(repo_path: str, config=None, deep: bool = False, **kwargs) -> List[CheckResult]:
    """Run email address checks on staged files."""
    # Allowed emails from config
    allowed = set()
    if config:
        for e in config.raw.get("allowed_emails", []):
            allowed.add(e.lower())

    files = get_staged_files(repo_path)
    if not files:
        return [CheckResult("Email addresses", True, info="No staged files")]

    findings = []
    seen = set()

    for file_path in files:
        content = get_staged_content(repo_path, file_path)
        if content is None:
            continue
        for line_num, line in enumerate(content.splitlines(), 1):
            for m in EMAIL_PATTERN.finditer(line):
                email = m.group(0).lower().lstrip("+-")
                if email in seen or email in allowed:
                    continue
                seen.add(email)

                if any(d in email for d in DEFAULT_ACCEPTABLE_DOMAINS):
                    continue

                at_pos = email.find("@")
                if at_pos >= 0:
                    domain = email[at_pos + 1:]
                    if any(domain.startswith(mod) for mod in DECORATOR_MODULES):
                        continue

                findings.append(f"{file_path}:{line_num} {email}")

    passed = len(findings) == 0
    return [CheckResult("Email addresses", passed, findings[:10])]
