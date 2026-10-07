"""Entropy-based secret detection in changed lines.

Exclusion patterns (``entropy.exclusions`` in config) suppress known false
positives such as integrity hashes in lockfiles.
"""

from typing import List

from ..entropy import (
    check_secret, extract_tokens,
    build_excluded_content_strings, filter_entropy_findings,
)
from ..utils import CheckResult


def run_checks(repo_path: str, config=None, scan_input=None, **kwargs) -> List[CheckResult]:
    if config and not config.entropy_enabled:
        return [CheckResult("Entropy scan", True, [], skipped=True,
                            info="Disabled in config")]

    threshold = config.entropy_threshold if config else 4.2
    min_len = config.entropy_min_len if config else 20
    exclusions = config.entropy_exclusions if config else []

    grouped = scan_input.by_file() if scan_input else {}
    all_findings, all_suppressed = [], []

    for file_path, lines in grouped.items():
        content = "\n".join(ln.text for ln in lines)
        ecs = build_excluded_content_strings(file_path, content, exclusions)

        file_findings = []
        for ln in lines:
            stripped = ln.text.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for token in extract_tokens(ln.text):
                result = check_secret(token, min_len=min_len, entropy_threshold=threshold)
                if result["flagged"]:
                    file_findings.append({
                        "file": file_path,
                        "where": ln.where(),
                        "matched_text": token,
                        "entropy": result["entropy"],
                    })

        kept, suppressed = filter_entropy_findings(file_findings, ecs)
        all_findings.extend(kept)
        all_suppressed.extend(suppressed)

    findings_text = [
        f"{f['where']} entropy={f['entropy']:.2f} {f['matched_text'][:40]}"
        for f in all_findings[:20]
    ]
    info = f"{len(grouped)} files"
    if all_suppressed:
        info += f", {len(all_suppressed)} excluded"
    return [CheckResult("Entropy scan", not all_findings, findings_text, info=info)]
