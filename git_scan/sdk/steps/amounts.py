"""Dollar amount checks for sensitive financial data in changed lines."""

import re
from typing import List

from ..utils import CheckResult

DOLLAR_PATTERN = re.compile(r"\$[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{2})?")

ACCEPTABLE_IRS_AMOUNTS = {
    "176,100", "168,600", "160,200",
    "23,200", "23,850", "94,300", "96,950", "201,050", "206,700",
    "383,900", "394,600", "487,450", "501,050", "731,200", "751,600",
    "29,200", "30,000",
    "250,000",
}


def parse_amount(s: str) -> float:
    try:
        return float(s.replace("$", "").replace(",", ""))
    except ValueError:
        return 0.0


def is_acceptable(s: str) -> bool:
    return s.replace("$", "").lstrip("0") in ACCEPTABLE_IRS_AMOUNTS


def is_round(amount: float) -> bool:
    if amount >= 10000:
        return amount % 1000 == 0
    return amount % 100 == 0


def run_checks(repo_path: str, config=None, scan_input=None, **kwargs) -> List[CheckResult]:
    thresholds = config.thresholds if config else {}
    large_threshold = thresholds.get("large_amount", 300000)
    nonround_threshold = thresholds.get("suspicious_nonround", 10000)
    cents_threshold = thresholds.get("cents_review", 500)

    large_findings, nonround_findings, cents_findings = [], [], []

    for ln in (scan_input.content if scan_input else []):
        for m in DOLLAR_PATTERN.finditer(ln.text):
            amount_str = m.group(0)
            amount = parse_amount(amount_str)
            if is_acceptable(amount_str):
                continue
            if amount >= large_threshold:
                large_findings.append(f"{ln.where()} {amount_str}")
            if amount >= nonround_threshold and not is_round(amount):
                nonround_findings.append(f"{ln.where()} {amount_str}")
            cents = round((amount % 1) * 100)
            if cents != 0 and amount >= cents_threshold:
                cents_findings.append(f"{ln.where()} {amount_str}")

    return [
        CheckResult(f"Large amounts (>= ${large_threshold:,})",
                    not large_findings, large_findings[:10]),
        CheckResult("Non-round suspicious amounts",
                    not nonround_findings, nonround_findings[:10]),
        CheckResult("Amounts with cents",
                    not cents_findings, cents_findings[:10]),
    ]
