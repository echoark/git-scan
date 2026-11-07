"""Pattern scanning of changed lines — one pass over every pattern.

Every matcher is a pattern entry of the merged configuration, compiled by
``compile_patterns`` with the entry's own flags: the built-in token, key
and identifier patterns, the entries derived from this machine (the git
author name and its parts, the git email, the login name, the home
directory name, ``.env`` values; see ``sdk/environment.py``), and the
user's own. No pattern reaches the text another way, so a user's flags on
an id (``ignore_case``, ``word_boundary``, exceptions, ``judgment``,
``disabled``) always apply.

Which entries are findings, which are judgment calls and which are allowed
is each entry's policy in the clone's exposure (``config.entry_policy``;
the scanner passes ``exposure``, public unless the clone was made eligible
for a relaxed review). Judgment entries feed the ``Judgment`` check rather
than ``Patterns``: their matches are listed for a look and cleared by an
acknowledgement, never findings in themselves. Allowed entries are not
applied at all in that exposure.
"""

import fnmatch
import re
from typing import List

from ..utils import CheckResult
from ..marks import ack_applies


class Compiled:
    """A compiled pattern entry: id, label, regex, the file globs it skips,
    and for a judgment entry the note that says when a match is a finding."""

    __slots__ = ("id", "label", "regex", "exclude_files", "note")

    def __init__(self, label, regex, exclude_files, note=None, id=None):
        self.label, self.regex, self.exclude_files, self.note = label, regex, exclude_files, note
        self.id = id or label

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
    """Compile pattern entries, applying word_boundary, not_followed_by,
    exclude_files and ignore_case (on unless the entry says
    ``ignore_case: false``). Callers decide which entries to pass:
    ``config.scan_patterns`` for findings, ``config.judgment_patterns`` for
    the judgment check."""
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
            flags = 0 if p.get("ignore_case") is False else re.IGNORECASE
            compiled = re.compile(regex_str, flags)
            label = p.get("category", p.get("id", pattern_str))
            result.append(Compiled(label, compiled, list(p.get("exclude_files") or []),
                                   p.get("note"), id=p.get("id")))
        except re.error:
            pass

    return result


def _is_known_false_positive(file_path: str, entry: Compiled, line_content: str) -> bool:
    """A copyright line in a license file names the author on purpose."""
    return ("license" in file_path.lower()
            and str(entry.id).startswith("git-name-")
            and "copyright" in line_content.lower())


def _judgment_check(repo_path: str, config, scan_input, acknowledged: bool,
                    exposure: str = "public") -> List[CheckResult]:
    """Matches on judgment entries: reported for a look, cleared by an
    acknowledgement, never a finding in themselves.

    For the staged change (and its commit message) the acknowledgement is
    the record ``git-scan ack`` wrote for exactly this staged tree. For
    commits already made it is the caller's say-so (``git-scan ack --commit``),
    which scans the commit with its judgment matches acknowledged.
    """
    entries = config.judgment_patterns_for(exposure) if config else []
    if not entries:
        return []
    compiled = compile_patterns(entries)
    matches = []
    for ln in (scan_input.content if scan_input else []):
        stripped = ln.text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for entry in compiled:
            if not entry.applies_to(ln.file):
                continue
            m = entry.search(ln.text)
            if m and not _is_known_false_positive(ln.file, entry, ln.text):
                matches.append((ln, entry, m.group(0)[:40]))
    info = f"{len(entries)} entries" + ("; relaxed clone" if exposure == "relaxed" else "")
    if not matches:
        return [CheckResult("Judgment", True, [], info=f"{info}; no matches")]
    scope = scan_input.scope if scan_input else "staged"
    acked = ack_applies(repo_path) if scope in ("staged", "message") else bool(acknowledged)
    if acked:
        return [CheckResult("Judgment", True, [],
                            info=f"{info}; {len(matches)} match(es) acknowledged")]
    findings = ["Each match below is a judgment call, not a finding. Look at every one; "
                "fix the content, or if all of them are fine, acknowledge them with:"]
    if scope in ("staged", "message"):
        findings.append("  git-scan ack")
    else:
        for sha in dict.fromkeys(ln.commit for ln, _, _ in matches if ln.commit):
            findings.append(f"  git-scan ack --commit {sha[:12]}")
    for ln, entry, value in matches:
        findings.append(f"{ln.where()} [{entry.label}] {value}")
        if entry.note:
            findings.append(f"    note: {entry.note}")
    return [CheckResult("Judgment", False, findings,
                        info=f"{info}; {len(matches)} match(es), not acknowledged")]


def run_checks(repo_path: str, config=None, scan_input=None, acknowledged: bool = False,
               exposure: str = "public", **kwargs) -> List[CheckResult]:
    compiled = compile_patterns(config.scan_patterns_for(exposure)) if config else []

    judgment = _judgment_check(repo_path, config, scan_input, acknowledged, exposure)
    if not compiled:
        return [CheckResult("Patterns", True, [], skipped=True)] + judgment

    findings = []
    ignored_count = 0
    lines = scan_input.content if scan_input else []

    for ln in lines:
        stripped = ln.text.strip()
        if not stripped or stripped.startswith("#"):
            continue

        for entry in compiled:
            if not entry.applies_to(ln.file):
                continue
            match = entry.search(ln.text)
            if not match:
                continue
            if _is_known_false_positive(ln.file, entry, ln.text):
                ignored_count += 1
            else:
                findings.append(f"{ln.where()} [{entry.label}] {match.group(0)[:40]}")

    counts = config.counts_for(exposure) if config else {}
    info = (f"{counts.get('personal', 0)} personal, {counts.get('built-in', 0)} built-in, "
            f"{counts.get('environment', 0)} environment; "
            f"{len(scan_input.files) if scan_input else 0} files")
    if exposure == "relaxed":
        info += f"; relaxed clone, {counts.get('allowed', 0)} allowed"
    if ignored_count:
        info += f", {ignored_count} ignored"
    return [CheckResult("Patterns", not findings, findings[:20], info=info)] + judgment
