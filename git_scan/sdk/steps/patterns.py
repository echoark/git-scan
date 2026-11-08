"""Pattern scanning of changed lines — one pass over every pattern.

Every matcher is a pattern entry of the merged configuration, compiled by
``compile_patterns`` with the entry's own flags: the built-in token, key
and identifier patterns, the entries derived from this machine (the git
author name and its parts, the git email, the login name, the home
directory name, ``.env`` values; see ``sdk/environment.py``), and the
user's own. No pattern reaches the text another way, so a user's flags on
an id (``ignore_case``, ``word_boundary``, exceptions, ``match``) always
apply.

What a match does is the entry's ``match`` (``by_match`` below): ``block``
fails the ``Patterns`` check; ``judgment`` feeds the ``Judgment`` check,
which lists the match for a look and is cleared by an acknowledgement;
``warn`` feeds the ``Warnings`` check, which lists the match and passes;
``ignore`` is not compiled. An entry with specifics and no pattern is the
reviewer's alone and never compiles.
"""

import fnmatch
import re
from typing import List

from ..utils import CheckResult
from ..marks import ack_applies


class Compiled:
    """A compiled pattern entry: id, label, regex, the file globs it skips,
    and the specifics that say what it matches."""

    __slots__ = ("id", "label", "regex", "exclude_files", "specifics")

    def __init__(self, label, regex, exclude_files, specifics=None, id=None):
        self.label, self.regex, self.exclude_files = label, regex, exclude_files
        self.specifics = specifics
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


def by_match(config, value: str) -> List[dict]:
    """The entries the scan compiles for one treatment: those with a pattern
    whose ``match`` is ``value`` (``config.with_match``).

    **The judgment flag is how whoever defines a pattern admits that its
    match cannot be trusted as a final, unbypassable call.** For built-in
    patterns that author is git-scan; for the user's own entries it is the
    user. When a match cannot safely be decided by the scan or the review
    alone, the pattern's author sets ``match: judgment`` as its default, and
    the decision on each match belongs to the user, never to an agent: the
    agent surfaces the match, the user acknowledges or fixes. ``warn`` is
    likewise the author's choice, made in advance through the CLI: the match
    is reported and the work proceeds.

    What governs a protected repository is the merged configuration stack
    loaded for it: the built-in layer, the user's layer, and the repository's
    project layer. Any layer may tighten or loosen what the layers beneath it
    define, because the user owns that decision: a machine may be configured
    to tolerate what git-scan normally refuses, and so may a repository, for
    example a private dotfiles repository where certain identifiers or keys
    are deliberately kept. Overrides exist for two reasons: an unforeseen
    consequence of a pattern as shipped in git-scan, or an exception that
    applies to that user or that repository and that the vast majority of
    users and repositories would not want. Entries are edited or removed
    only through the CLI, and only by the user.

    The defence against misuse is therefore not a layer that cannot loosen;
    it is that entries change only through the CLI, by the user, and that
    every decision traces to the merged configuration that was loaded.
    """
    return config.with_match(value) if config else []


def compile_patterns(patterns: List[dict]) -> List[Compiled]:
    """Compile pattern entries, applying word_boundary, not_followed_by,
    exclude_files and ignore_case (on unless the entry says
    ``ignore_case: false``). Entries without a pattern are skipped. Callers
    decide which entries to pass (``by_match``)."""
    result = []
    for p in patterns:
        pattern_str = p.get("pattern")
        if not pattern_str:
            continue
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
                                   p.get("specifics"), id=p.get("id")))
        except re.error:
            pass

    return result


def _is_known_false_positive(file_path: str, entry: Compiled, line_content: str) -> bool:
    """A copyright line in a license file names the author on purpose."""
    return ("license" in file_path.lower()
            and str(entry.id).startswith("git-name-")
            and "copyright" in line_content.lower())


def _matches(compiled: List[Compiled], scan_input) -> list:
    """``(line, entry, value)`` for every match of ``compiled`` in the content."""
    found = []
    for ln in (scan_input.content if scan_input else []):
        stripped = ln.text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for entry in compiled:
            if not entry.applies_to(ln.file):
                continue
            m = entry.search(ln.text)
            if m and not _is_known_false_positive(ln.file, entry, ln.text):
                found.append((ln, entry, m.group(0)[:40]))
    return found


def _judgment_check(repo_path: str, config, scan_input, acknowledged: bool) -> List[CheckResult]:
    """Matches on judgment entries: reported for a look, cleared by an
    acknowledgement, never a finding in themselves.

    For the staged change (and its commit message) the acknowledgement is
    the record ``git-scan ack`` wrote for exactly this staged tree. For
    commits already made it is the caller's say-so (``git-scan ack --commit``),
    which scans the commit with its judgment matches acknowledged.
    """
    entries = by_match(config, "judgment")
    if not entries:
        return []
    matches = _matches(compile_patterns(entries), scan_input)
    info = f"{len(entries)} entries"
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
        if entry.specifics:
            findings.append(f"    {entry.id}: {entry.specifics}")
    return [CheckResult("Judgment", False, findings,
                        info=f"{info}; {len(matches)} match(es), not acknowledged")]


def _warn_check(config, scan_input) -> List[CheckResult]:
    """Matches on warn entries: listed, and the check passes. The author of
    the entry decided in advance that the match is reported and the work
    proceeds; an agent reports it to the user."""
    entries = by_match(config, "warn")
    if not entries:
        return []
    matches = _matches(compile_patterns(entries), scan_input)
    info = f"{len(entries)} entries"
    if not matches:
        return [CheckResult("Warnings", True, [], info=f"{info}; no matches")]
    findings = [f"{ln.where()} [{entry.label}] {value}" for ln, entry, value in matches]
    return [CheckResult("Warnings", True, findings,
                        info=f"{info}; {len(matches)} match(es), reported and passed")]


def run_checks(repo_path: str, config=None, scan_input=None, acknowledged: bool = False,
               **kwargs) -> List[CheckResult]:
    compiled = compile_patterns(by_match(config, "block"))

    judgment = _judgment_check(repo_path, config, scan_input, acknowledged)
    warnings = _warn_check(config, scan_input)
    if not compiled:
        return [CheckResult("Patterns", True, [], skipped=True)] + judgment + warnings

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

    counts = config.pattern_counts if config else {}
    info = (f"{counts.get('personal', 0)} personal, {counts.get('built-in', 0)} built-in, "
            f"{counts.get('environment', 0)} environment; "
            f"{len(scan_input.files) if scan_input else 0} files")
    if ignored_count:
        info += f", {ignored_count} ignored"
    return [CheckResult("Patterns", not findings, findings[:20], info=info)] + judgment + warnings
