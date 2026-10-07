"""Pattern scanning of changed lines — one pass over every pattern.

Pattern sources:
  1. Config patterns (package defaults + user + project, merged by id):
     the built-in token/key/identifier patterns and the user's own.
  2. Dynamic patterns from the environment: OS username, home directory
     name, git user name (and its parts), git email, ``.env`` values.
"""

import fnmatch
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from ..utils import CheckResult


def gather_dynamic_patterns(repo_path: str = ".") -> Dict[str, str]:
    """Sensitive strings from the environment and git config, as regexes."""
    patterns = {}

    username = os.environ.get("USER") or os.environ.get("USERNAME")
    if username:
        patterns[f"Local username ('{username}')"] = re.escape(username)

    try:
        home_name = Path.home().name
        if home_name and home_name != username:
            patterns[f"Home dir name ('{home_name}')"] = re.escape(home_name)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=5, cwd=repo_path,
        )
        git_name = result.stdout.strip()
        if git_name:
            patterns[f"Git Full Name ('{git_name}')"] = re.escape(git_name)
            for part in git_name.split():
                if len(part) > 2:
                    patterns[f"Git Name Part ('{part}')"] = re.escape(part)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "config", "user.email"],
            capture_output=True, text=True, timeout=5, cwd=repo_path,
        )
        git_email = result.stdout.strip()
        if git_email:
            patterns[f"Git Email ('{git_email}')"] = re.escape(git_email)
    except Exception:
        pass

    env_path = Path(repo_path) / ".env"
    if env_path.is_file():
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        value = value.strip().strip('"').strip("'")
                        if value and len(value) > 8:
                            patterns[f".env ({key.strip()})"] = re.escape(value)
        except Exception:
            pass

    return patterns


def identity_patterns(repo_path: str = ".") -> Dict[str, str]:
    """The dynamic patterns that are names of the user: username, home
    directory, git name and its parts. (Not the email or ``.env`` values.)"""
    return {k: v for k, v in gather_dynamic_patterns(repo_path).items()
            if k.startswith(("Local username", "Home dir name", "Git Full Name",
                             "Git Name Part"))}


class Compiled:
    """A compiled pattern entry: label, regex, and the file globs it skips."""

    __slots__ = ("label", "regex", "exclude_files")

    def __init__(self, label, regex, exclude_files):
        self.label, self.regex, self.exclude_files = label, regex, exclude_files

    def __iter__(self):            # (label, regex) unpacking
        yield self.label
        yield self.regex

    def applies_to(self, file_path: str) -> bool:
        """Globs match the whole repo-relative path, never just the base
        name, so an exclusion for one file can't silently cover every file
        of the same name elsewhere."""
        return not any(fnmatch.fnmatch(file_path, g) for g in self.exclude_files)

    def search(self, text: str):
        return self.regex.search(text)


def compile_patterns(patterns: List[dict]) -> List[Compiled]:
    """Compile pattern entries, applying word_boundary, not_followed_by, exclude_files."""
    result = []
    for p in patterns:
        pattern_str = p.get("pattern", "")
        regex_str = pattern_str

        if p.get("word_boundary"):
            regex_str = rf"\b{regex_str}\b"

        not_followed = p.get("not_followed_by", [])
        if not_followed:
            escaped_opts = [re.escape(opt) for opt in not_followed]
            lookahead = f"(?!({'|'.join(escaped_opts)}))"
            regex_str = f"{regex_str}{lookahead}"

        try:
            compiled = re.compile(regex_str)
            label = p.get("category", p.get("id", pattern_str))
            result.append(Compiled(label, compiled, list(p.get("exclude_files") or [])))
        except re.error:
            pass

    return result


def _is_known_false_positive(file_path: str, match_type: str, line_content: str) -> bool:
    file_lower = file_path.lower()
    line_lower = line_content.lower()

    if ("license" in file_lower
            and match_type.startswith(("Git Full Name", "Git Name Part"))
            and "copyright" in line_lower):
        return True

    return False


def run_checks(repo_path: str, config=None, scan_input=None, **kwargs) -> List[CheckResult]:
    dynamic: Dict[str, str] = gather_dynamic_patterns(repo_path)
    config_compiled = compile_patterns(config.patterns) if config else []

    total = len(dynamic) + len(config_compiled)
    if total == 0:
        return [CheckResult("Patterns", True, [], skipped=True)]

    findings = []
    ignored_count = 0
    lines = scan_input.content if scan_input else []

    for ln in lines:
        stripped = ln.text.strip()
        if not stripped or stripped.startswith("#"):
            continue

        for name, pattern in dynamic.items():
            try:
                if re.search(pattern, ln.text):
                    if _is_known_false_positive(ln.file, name, stripped):
                        ignored_count += 1
                    else:
                        findings.append(f"{ln.where()} [{name}] {stripped[:60]}")
            except re.error:
                pass

        for entry in config_compiled:
            if not entry.applies_to(ln.file):
                continue
            match = entry.search(ln.text)
            if match:
                findings.append(f"{ln.where()} [{entry.label}] {match.group(0)[:40]}")

    counts = config.pattern_counts if config else {}
    info = (f"{counts.get('personal', 0)} personal, {counts.get('built-in', 0)} built-in, "
            f"{len(dynamic)} environment; {len(scan_input.files) if scan_input else 0} files")
    if ignored_count:
        info += f", {ignored_count} ignored"
    return [CheckResult("Patterns", not findings, findings[:20], info=info)]
