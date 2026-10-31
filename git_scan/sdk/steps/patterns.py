"""Pattern scanning of changed lines — one pass over every pattern.

Pattern sources:
  1. Config patterns (package defaults + user + project, merged by id):
     the built-in token/key/identifier patterns and the user's own.
  2. Dynamic patterns from the environment: OS username, home directory
     name (both skipped for shared system accounts such as ``root``), the
     name (and its parts) and email git will commit with, ``.env`` values.
"""

import fnmatch
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from ..utils import CheckResult
from ..marks import ack_applies

# Account names that machines, containers, and CI runners give everyone.
# They identify no person, and most are ordinary words ("root", "node"),
# so treating them as personal would flag prose like "the repo root".
SYSTEM_ACCOUNT_NAMES = frozenset({
    "root", "admin", "administrator", "runner", "ubuntu", "ec2-user",
    "vscode", "codespace", "node", "user", "nobody",
})

_IDENT = re.compile(r"^(?P<name>.*?)\s*<(?P<email>[^>]*)>")


def _personal_account_name(name):
    """The account name, or None when it is a shared system account."""
    if name and name.lower() not in SYSTEM_ACCOUNT_NAMES:
        return name
    return None


def commit_identity(repo_path: str = "."):
    """(name, email) git will author the next commit with.

    ``git var GIT_AUTHOR_IDENT`` applies git's own precedence: the
    ``GIT_AUTHOR_NAME`` / ``GIT_AUTHOR_EMAIL`` environment variables over
    repository config over global config. Reading ``user.name`` alone would
    take the global config's name even when the environment overrides it,
    which is how a hosted environment's placeholder identity ends up treated
    as the user's name. Falls back to config when ``git var`` fails (no
    identity configured at all).
    """
    try:
        result = subprocess.run(
            ["git", "var", "GIT_AUTHOR_IDENT"],
            capture_output=True, text=True, timeout=5, cwd=repo_path,
        )
        m = _IDENT.match(result.stdout.strip()) if result.returncode == 0 else None
        if m:
            return m.group("name").strip(), m.group("email").strip()
    except Exception:
        pass

    def _config(key):
        try:
            r = subprocess.run(["git", "config", key], capture_output=True,
                               text=True, timeout=5, cwd=repo_path)
            return r.stdout.strip()
        except Exception:
            return ""
    return _config("user.name"), _config("user.email")


def gather_dynamic_patterns(repo_path: str = ".") -> Dict[str, str]:
    """Sensitive strings from the environment and git config, as regexes."""
    patterns = {}

    username = _personal_account_name(
        os.environ.get("USER") or os.environ.get("USERNAME"))
    if username:
        patterns[f"Local username ('{username}')"] = re.escape(username)

    try:
        home_name = _personal_account_name(Path.home().name)
        if home_name and home_name != username:
            patterns[f"Home dir name ('{home_name}')"] = re.escape(home_name)
    except Exception:
        pass

    git_name, git_email = commit_identity(repo_path)
    if git_name:
        patterns[f"Git Full Name ('{git_name}')"] = re.escape(git_name)
        for part in git_name.split():
            if len(part) > 2:
                patterns[f"Git Name Part ('{part}')"] = re.escape(part)
    if git_email:
        patterns[f"Git Email ('{git_email}')"] = re.escape(git_email)

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
    """A compiled pattern entry: label, regex, the file globs it skips, and
    for a judgment entry the note that says when a match is a finding."""

    __slots__ = ("label", "regex", "exclude_files", "note")

    def __init__(self, label, regex, exclude_files, note=None):
        self.label, self.regex, self.exclude_files, self.note = label, regex, exclude_files, note

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
    exclude_files and ignore_case. Callers decide which entries to pass:
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
            compiled = re.compile(regex_str, re.IGNORECASE if p.get("ignore_case") else 0)
            label = p.get("category", p.get("id", pattern_str))
            result.append(Compiled(label, compiled, list(p.get("exclude_files") or []),
                                   p.get("note")))
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


def _judgment_check(repo_path: str, config, scan_input, acknowledged: bool) -> List[CheckResult]:
    """Matches on judgment entries: reported for a look, cleared by an
    acknowledgement, never a finding in themselves.

    For the staged change (and its commit message) the acknowledgement is
    the record ``git-scan ack`` wrote for exactly this staged tree. For
    commits already made it is the caller's say-so (``git-scan ack --commit``),
    which scans the commit with its judgment matches acknowledged.
    """
    entries = config.judgment_patterns if config else []
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
            if m:
                matches.append((ln, entry, m.group(0)[:40]))
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
        if entry.note:
            findings.append(f"    note: {entry.note}")
    return [CheckResult("Judgment", False, findings,
                        info=f"{info}; {len(matches)} match(es), not acknowledged")]


def run_checks(repo_path: str, config=None, scan_input=None, acknowledged: bool = False,
               **kwargs) -> List[CheckResult]:
    dynamic: Dict[str, str] = gather_dynamic_patterns(repo_path)
    config_compiled = compile_patterns(config.scan_patterns) if config else []

    judgment = _judgment_check(repo_path, config, scan_input, acknowledged)
    total = len(dynamic) + len(config_compiled)
    if total == 0:
        return [CheckResult("Patterns", True, [], skipped=True)] + judgment

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
    return [CheckResult("Patterns", not findings, findings[:20], info=info)] + judgment
