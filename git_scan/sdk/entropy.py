"""Shannon entropy detection with structural exceptions.

Entropy exclusion patterns (file_glob + content_pattern) suppress false
positives like sha512 hashes in lockfiles.
"""

from __future__ import annotations

import fnmatch
import math
import re
from collections import Counter
from typing import Optional

# Structural characters that separate "logical words" in identifiers/paths
STRUCTURAL_CHARS = "_./+-="

# Token extraction: word chars + dash + dot + slash + equals + plus
TOKEN_PATTERN = re.compile(r"[\w\-\.\/\=\+]{8,}")


def shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of a string."""
    if not s:
        return 0.0
    counts = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def _has_digit_before_letter(s: str) -> bool:
    """Check if string has any digit immediately followed by a letter.

    Real secrets mix digits throughout (e.g., MdP4AO1Ft9b8x).
    Code identifiers have digits only at END of segments (e.g., v2, config123).
    """
    return bool(re.search(r"[0-9][a-zA-Z]", s))


def get_pattern_exception(s: str, allow_path_exception: bool = True) -> Optional[str]:
    """Return exception reason if string matches an exclusion pattern."""
    if s.isalpha():
        return "all_alpha"

    if not allow_path_exception:
        return None

    if all(c.isalpha() or c in STRUCTURAL_CHARS for c in s) and any(
        c in STRUCTURAL_CHARS for c in s
    ):
        return "code_identifier"

    segments = re.split(r"[" + re.escape(STRUCTURAL_CHARS) + r"]+", s)
    segments = [seg for seg in segments if seg]

    if segments:
        has_digit_issue = any(_has_digit_before_letter(seg) for seg in segments)
        if not has_digit_issue:
            return "versioned_identifier"

    return None


def check_secret(
    s: str,
    min_len: int = 20,
    entropy_threshold: float = 4.2,
    allow_path_exception: bool = True,
) -> dict:
    """Check if a string should be flagged as a potential secret."""
    entropy = shannon_entropy(s)
    exception = get_pattern_exception(s, allow_path_exception=allow_path_exception)

    if len(s) < min_len:
        return {
            "flagged": False,
            "entropy": entropy,
            "exception": exception,
            "reason": f"len={len(s)}<{min_len}",
        }

    if exception:
        return {
            "flagged": False,
            "entropy": entropy,
            "exception": exception,
            "reason": exception,
        }

    if entropy > entropy_threshold:
        return {
            "flagged": True,
            "entropy": entropy,
            "exception": None,
            "reason": f"entropy={entropy:.2f}>{entropy_threshold}",
        }

    return {
        "flagged": False,
        "entropy": entropy,
        "exception": None,
        "reason": f"entropy={entropy:.2f}<={entropy_threshold}",
    }


def extract_tokens(line: str) -> list[str]:
    """Extract potential secret tokens from a line."""
    return TOKEN_PATTERN.findall(line)


def build_excluded_content_strings(
    file_path: str, file_content: str, exclusions: list[dict]
) -> list[str]:
    """Build Excluded Content Strings (ECS) for a file.

    For each exclusion rule whose file_glob matches the file, run the
    content_pattern regex against the file content. Each match produces
    a string in the ECS array.
    """
    ecs = []
    for rule in exclusions:
        glob = rule.get("file_glob", "")
        pattern = rule.get("content_pattern", "")
        if not glob or not pattern:
            continue
        # Match against basename and full relative path
        basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
        if not (fnmatch.fnmatch(basename, glob) or fnmatch.fnmatch(file_path, glob)):
            continue
        try:
            for m in re.finditer(pattern, file_content):
                ecs.append(m.group(0))
        except re.error:
            continue
    return ecs


def filter_entropy_findings(
    findings: list[dict], ecs: list[str]
) -> tuple[list[dict], list[dict]]:
    """Filter entropy findings against excluded content strings.

    Returns (kept, suppressed) tuples.
    """
    if not ecs:
        return findings, []

    kept = []
    suppressed = []
    for f in findings:
        matched_text = f.get("matched_text", "")
        if any(matched_text in ecs_val for ecs_val in ecs):
            suppressed.append(f)
        else:
            kept.append(f)
    return kept, suppressed
