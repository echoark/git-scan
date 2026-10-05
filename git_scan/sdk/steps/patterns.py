"""Unified pattern scanning — all regex-based checks in one pass.

Pattern sources:
  1. Config patterns (package defaults + user + project, merged by id)
     Includes built-in token/key/identifier patterns shipped in git-scan.yaml
     and any user-added patterns at user or project scope.
  2. Dynamic patterns from environment (username, git identity, .env values)
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from ..utils import CheckResult, get_staged_files, get_staged_content


def _gather_dynamic_patterns(repo_path: str = ".") -> Dict[str, str]:
    """Gather sensitive patterns from environment and git config."""
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
            capture_output=True, text=True, timeout=5,
            cwd=repo_path,
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
            capture_output=True, text=True, timeout=5,
            cwd=repo_path,
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


def _compile_config_patterns(config) -> List[Tuple[str, re.Pattern]]:
    """Build compiled regex patterns from merged config."""
    if config is None:
        return []

    result = []
    for p in config.patterns:
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
            result.append((label, compiled))
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


def run_checks(repo_path: str, config=None, deep: bool = False,
               include_untracked: bool = False, **kwargs) -> List[CheckResult]:
    """Run all pattern-based scanning in one pass over staged files."""

    # Dynamic patterns from environment
    dynamic: Dict[str, str] = _gather_dynamic_patterns(repo_path)

    # Config patterns (built-in defaults + user + project, already merged)
    config_compiled = _compile_config_patterns(config)

    total = len(dynamic) + len(config_compiled)
    if total == 0:
        return [CheckResult("Patterns", True, [], skipped=True)]

    files = get_staged_files(repo_path)
    if not files:
        return [CheckResult("Patterns", True, info="No staged files")]

    findings = []
    ignored_count = 0

    for file_path in files:
        content = get_staged_content(repo_path, file_path)
        if content is None:
            continue

        for line_num, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # Dynamic patterns (plain regex strings)
            for name, pattern in dynamic.items():
                try:
                    if re.search(pattern, line):
                        if _is_known_false_positive(file_path, name, stripped):
                            ignored_count += 1
                        else:
                            findings.append(
                                f"{file_path}:{line_num} [{name}] "
                                f"{stripped[:60]}"
                            )
                except re.error:
                    pass

            # Config patterns (pre-compiled)
            for label, compiled in config_compiled:
                match = compiled.search(line)
                if match:
                    findings.append(
                        f"{file_path}:{line_num} [{label}] "
                        f"{match.group(0)[:40]}"
                    )

    passed = len(findings) == 0
    info = f"{total} patterns, {len(files)} files"
    if ignored_count:
        info += f", {ignored_count} ignored"
    return [CheckResult("Patterns", passed, findings[:20], info=info)]
