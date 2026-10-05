"""Entropy-based secret detection.

Scans staged file content for high-entropy strings that look like secrets.
Supports entropy exclusion patterns to suppress known false positives
(e.g., sha512 hashes in lockfiles).
"""

from typing import List

from ..entropy import (
    check_secret, extract_tokens,
    build_excluded_content_strings, filter_entropy_findings,
)
from ..utils import CheckResult, get_staged_files, get_staged_content


def run_checks(repo_path: str, config=None, deep: bool = False,
               include_untracked: bool = False, **kwargs) -> List[CheckResult]:
    """Run entropy scan on staged files."""
    if config and not config.entropy_enabled:
        return [CheckResult("Entropy scan", True, [], skipped=True,
                            info="Disabled in config")]

    threshold = config.entropy_threshold if config else 4.2
    min_len = config.entropy_min_len if config else 20
    exclusions = config.entropy_exclusions if config else []

    files = get_staged_files(repo_path)
    if not files:
        return [CheckResult("Entropy scan", True, info="No staged files")]

    all_findings = []
    all_suppressed = []

    for file_path in files:
        content = get_staged_content(repo_path, file_path)
        if content is None:
            continue

        # Build ECS for this file
        ecs = build_excluded_content_strings(file_path, content, exclusions)

        file_findings = []
        regex_matched = set()  # avoid double-flagging with regex step

        for line_num, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            for token in extract_tokens(line):
                if token in regex_matched:
                    continue

                result = check_secret(
                    token, min_len=min_len,
                    entropy_threshold=threshold,
                )
                if result["flagged"]:
                    file_findings.append({
                        "file": file_path,
                        "line_num": line_num,
                        "matched_text": token,
                        "entropy": result["entropy"],
                    })

        # Apply exclusions
        kept, suppressed = filter_entropy_findings(file_findings, ecs)
        all_findings.extend(kept)
        all_suppressed.extend(suppressed)

    findings_text = []
    for f in all_findings[:20]:
        findings_text.append(
            f"{f['file']}:{f['line_num']} entropy={f['entropy']:.2f} "
            f"{f['matched_text'][:40]}"
        )

    passed = len(all_findings) == 0
    info = f"{len(files)} files"
    if all_suppressed:
        info += f", {len(all_suppressed)} excluded"
    return [CheckResult("Entropy scan", passed, findings_text, info=info)]
