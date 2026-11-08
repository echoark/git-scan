"""Layered YAML config: package defaults → environment → user → project.

Four layers are merged with higher layers winning:
  1. Package defaults (shipped with git-scan)
  2. The environment: pattern entries derived from this machine (the git
     author name and its parts, the git email, the login name, the home
     directory name, ``.env`` values; see ``environment``). Not a file.
  3. User config (``$XDG_CONFIG_HOME/git-scan/git-scan.yaml``,
     default ``~/.config/git-scan/git-scan.yaml``)
  4. Project config (``./git-scan.yaml`` in the repo root)

Uses the yaml-config-merge snippet for merging. Arrays of dicts are
merged by composite key (id, name). An inherited pattern is turned off with
``match: ignore``. Scalars and plain dicts: higher layer wins. Every
matcher the scan applies, built in, derived or the user's own, is a pattern
entry in this merge, so the user's flags on an id always apply, and every
entry says what a match does (``match``, and ``relaxed`` for a relaxed
review): one rulebook for the scan and the reviewer.

Every layer is validated when loaded. A broken file fails the scan with the
file, the spot, and the command that fixes it, rather than silently
scanning with a half-loaded config.

The ``patterns`` key of the user layer is the switch for personal patterns:
absent (or no user layer at all) means "not configured", and a full scan is
refused before any check runs, since nothing personal could be checked;
present, even as an empty list, means configured. The refusal ends with
setup instructions, or with the text in ``missing-config-help`` beside the
user layer when that file exists (written by ``hook install
--missing-config-help``). That text lives outside the user layer because the
user layer is what's missing when it's needed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .environment import derived_entries, is_derived_id
from .yaml_merge import _merge_dicts

PACKAGE_DEFAULTS = Path(__file__).parent.parent / "data" / "git-scan.yaml"
CONFIG_FILENAME = "git-scan.yaml"

LAYERS = ("built-in", "environment", "user", "project")
FILE_LAYERS = ("built-in", "user", "project")     # the environment layer is derived, not read
MERGE_KEYS = ["id", "name"]

TOP_LEVEL_KEYS = {"entropy", "thresholds", "patterns", "allowed_emails", "identity", "push",
                  "notes", "review"}
REVIEW_KEYS = {"runner", "model", "timeout", "batch_bytes", "public_prospective"}
PATTERN_FIELDS = {"id", "pattern", "category", "specifics", "word_boundary",
                  "not_followed_by", "exclude_files", "ignore_case", "match", "relaxed"}
# Fields earlier versions wrote; ``git-scan config migrate`` rewrites them.
MIGRATED_FIELDS = {"note": "specifics", "judgment": "match: judgment", "disabled": "match: ignore"}

# What a match on an entry does. ``block``: the scan fails, the review
# reports a finding. ``judgment``: the scan fails until the user acknowledges
# the match; the reviewer applies the entry's specifics and reports the match
# for a human look, never escalating it by inference. ``warn``: the scan
# prints the match and passes; the review shows it as a note that doesn't
# block. ``ignore``: the entry is not compiled and not sent to the reviewer.
MATCHES = ("block", "judgment", "warn", "ignore")


def entry_match(entry: dict, relaxed: bool = False) -> str:
    """What a match on ``entry`` does: its ``match`` (``block`` when absent),
    or in a relaxed review its ``relaxed`` value when it has one."""
    match = entry.get("match") or "block"
    if relaxed and entry.get("relaxed"):
        return entry["relaxed"]
    return match


THRESHOLD_KEYS = {"large_amount", "suspicious_nonround", "suspicious_any",
                  "cents_review"}
ENTROPY_KEYS = {"enabled", "threshold", "min_len", "exclusions"}
EXCLUSION_FIELDS = {"id", "file_glob", "content_pattern", "disabled"}


class ConfigError(ValueError):
    """A config layer is invalid. The message names the file and the fix."""


def user_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "git-scan" / CONFIG_FILENAME


def missing_config_help_path() -> Path:
    """Optional text shown, instead of setup instructions, when the user layer is missing."""
    return user_config_path().parent / "missing-config-help"


def read_missing_config_help() -> Optional[str]:
    path = missing_config_help_path()
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return text or None


def write_missing_config_help(text: str) -> Path:
    path = missing_config_help_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n")
    return path


def project_config_path(repo_path: str) -> Optional[Path]:
    """Find project-level config in repo root."""
    root = Path(repo_path)
    for name in (CONFIG_FILENAME, f".{CONFIG_FILENAME}", "sensitive-patterns.yaml"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def layer_paths(repo_path: str = ".") -> dict:
    """Path of each layer, whether or not the file exists."""
    return {
        "built-in": PACKAGE_DEFAULTS,
        "user": user_config_path(),
        "project": project_config_path(repo_path) or Path(repo_path) / CONFIG_FILENAME,
    }


def load_layer(path: Path) -> Optional[dict]:
    """Parse and validate one layer. ``None`` if the file doesn't exist."""
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: not valid YAML: {e}")
    validate_layer(data, path)
    return data


def validate_layer(data, path) -> None:
    """Raise ConfigError describing the first problems found."""
    errors = []
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: must be a mapping of sections, got {type(data).__name__}")

    for key in data:
        if key not in TOP_LEVEL_KEYS:
            errors.append(f"unknown section '{key}' (known: {', '.join(sorted(TOP_LEVEL_KEYS))})")

    patterns = data.get("patterns", [])
    if "patterns" in data and patterns is None:
        errors.append("patterns: must be a list; write 'patterns: []' for none "
                      "(or run: git-scan patterns clear)")
    elif not isinstance(patterns, list):
        errors.append(f"patterns: must be a list, got {type(patterns).__name__}")
    else:
        for i, p in enumerate(patterns):
            loc = f"patterns[{i}]"
            if not isinstance(p, dict):
                errors.append(f"{loc}: must be a mapping with id and pattern")
                continue
            loc = f"patterns[{i}] ({p.get('id', 'no id')})"
            for f in p:
                if f in MIGRATED_FIELDS:
                    errors.append(f"{loc}: '{f}' is no longer a field; it is now {MIGRATED_FIELDS[f]}. "
                                  "Run 'git-scan config migrate' to rewrite the file")
                elif f not in PATTERN_FIELDS:
                    errors.append(f"{loc}: unknown field '{f}' (known: {', '.join(sorted(PATTERN_FIELDS))})")
            if not isinstance(p.get("id"), str) or not p.get("id"):
                errors.append(f"{loc}: 'id' is required and must be text")
            if "pattern" in p:
                if not isinstance(p["pattern"], str) or not p["pattern"]:
                    errors.append(f"{loc}: 'pattern' must be text")
                else:
                    try:
                        re.compile(p["pattern"])
                    except re.error as e:
                        errors.append(f"{loc}: pattern is not a valid regex: {e}")
            for text_field in ("category", "specifics"):
                if text_field in p and (not isinstance(p[text_field], str) or not p[text_field]):
                    errors.append(f"{loc}: '{text_field}' must be text")
            for flag in ("word_boundary", "ignore_case"):
                if flag in p and not isinstance(p[flag], bool):
                    errors.append(f"{loc}: '{flag}' must be true or false")
            for key in ("match", "relaxed"):
                if key in p and p[key] not in MATCHES:
                    errors.append(f"{loc}: '{key}' must be one of {', '.join(MATCHES)} "
                                  f"(run: git-scan patterns edit {p.get('id', 'ID')} --{key} VALUE)")
            for field in ("not_followed_by", "exclude_files"):
                val = p.get(field)
                if val is not None and (not isinstance(val, list)
                                        or not all(isinstance(x, str) for x in val)):
                    errors.append(f"{loc}: '{field}' must be a list of text")

    notes = data.get("notes", [])
    if not isinstance(notes, list) or not all(isinstance(n, str) and n for n in notes):
        errors.append("notes: must be a list of text (run: git-scan notes add TEXT)")

    thresholds = data.get("thresholds", {})
    if not isinstance(thresholds, dict):
        errors.append("thresholds: must be a mapping")
    else:
        for k, v in thresholds.items():
            if k not in THRESHOLD_KEYS:
                errors.append(f"thresholds.{k}: unknown (known: {', '.join(sorted(THRESHOLD_KEYS))})")
            elif not isinstance(v, (int, float)) or isinstance(v, bool):
                errors.append(f"thresholds.{k}: must be a number")

    entropy = data.get("entropy", {})
    if not isinstance(entropy, dict):
        errors.append("entropy: must be a mapping")
    else:
        for k in entropy:
            if k not in ENTROPY_KEYS:
                errors.append(f"entropy.{k}: unknown (known: {', '.join(sorted(ENTROPY_KEYS))})")
        if "enabled" in entropy and not isinstance(entropy["enabled"], bool):
            errors.append("entropy.enabled: must be true or false")
        if "threshold" in entropy and not isinstance(entropy["threshold"], (int, float)):
            errors.append("entropy.threshold: must be a number")
        if "min_len" in entropy and (not isinstance(entropy["min_len"], int)
                                     or isinstance(entropy["min_len"], bool)):
            errors.append("entropy.min_len: must be a whole number")
        exclusions = entropy.get("exclusions", [])
        if not isinstance(exclusions, list):
            errors.append("entropy.exclusions: must be a list")
        else:
            for i, e in enumerate(exclusions):
                loc = f"entropy.exclusions[{i}]"
                if not isinstance(e, dict):
                    errors.append(f"{loc}: must be a mapping")
                    continue
                for f in e:
                    if f not in EXCLUSION_FIELDS:
                        errors.append(f"{loc}: unknown field '{f}'")
                if not isinstance(e.get("id"), str):
                    errors.append(f"{loc}: 'id' is required")

    allowed = data.get("allowed_emails", [])
    if not isinstance(allowed, list) or not all(isinstance(x, str) for x in allowed):
        errors.append("allowed_emails: must be a list of addresses")

    identity = data.get("identity", [])
    if not isinstance(identity, list):
        errors.append("identity: must be a list of rules")
    else:
        for i, r in enumerate(identity):
            loc = f"identity[{i}]"
            if not isinstance(r, dict):
                errors.append(f"{loc}: must be a mapping with remote and emails")
                continue
            for f in r:
                if f not in ("remote", "emails"):
                    errors.append(f"{loc}: unknown field '{f}' (known: emails, remote)")
            if not isinstance(r.get("remote"), str) or not r.get("remote"):
                errors.append(f"{loc}: 'remote' is required (host, optional owner and repo)")
            em = r.get("emails")
            if not isinstance(em, list) or not em or not all(isinstance(x, str) and x for x in em):
                errors.append(f"{loc}: 'emails' must be a non-empty list of addresses")

    push = data.get("push", {})
    if not isinstance(push, dict):
        errors.append("push: must be a mapping (require_review: true)")
    else:
        for f in push:
            if f == "require_marks":
                errors.append("push.require_marks is no longer supported: set "
                              "push.require_review: true (git-scan config require-review on) "
                              "and record each review with 'git-scan review record'")
            elif f != "require_review":
                errors.append(f"push: unknown field '{f}' (known: require_review)")
        if "require_review" in push and not isinstance(push["require_review"], bool):
            errors.append("push.require_review: must be true or false")

    review = data.get("review", {})
    if not isinstance(review, dict):
        errors.append("review: must be a mapping (runner, model, timeout, batch_bytes, "
                      "public_prospective)")
    else:
        for k, v in review.items():
            if k == "relaxed":
                errors.append("review.relaxed is no longer supported: a relaxed review is "
                              "asked for per review (git-scan review --relaxed) and only for "
                              "a repository made eligible with 'git-scan relax'. Delete the "
                              "review.relaxed key from this file")
            elif k not in REVIEW_KEYS:
                errors.append(f"review.{k}: unknown (known: {', '.join(sorted(REVIEW_KEYS))})")
            elif k in ("runner", "model") and (not isinstance(v, str) or not v):
                errors.append(f"review.{k}: must be text")
            elif k in ("timeout", "batch_bytes") and (not isinstance(v, int) or isinstance(v, bool)
                                                       or v <= 0):
                errors.append(f"review.{k}: must be a positive whole number")
            elif k == "public_prospective" and not isinstance(v, bool):
                errors.append("review.public_prospective: must be true or false")

    if errors:
        lines = [f"Invalid config: {path}"] + [f"  {e}" for e in errors] + [
            "Fix the file, or remove the entry with 'git-scan patterns remove <id>' "
            "and add it again with 'git-scan patterns add'."]
        raise ConfigError("\n".join(lines))


@dataclass
class MergedConfig:
    """Resolved config after three-layer merge."""
    raw: dict = field(default_factory=dict)
    layers: dict = field(default_factory=dict)   # layer name -> parsed dict or None

    @property
    def patterns(self) -> list[dict]:
        """Active entries: every entry whose ``match`` is not ``ignore``."""
        return [p for p in self.raw.get("patterns", []) if entry_match(p) != "ignore"]

    def with_match(self, value: str) -> list[dict]:
        """Active entries with a pattern whose ``match`` is ``value``: what the
        scan compiles for that treatment. Entries with specifics only are the
        reviewer's and never compile."""
        return [p for p in self.patterns if p.get("pattern") and entry_match(p) == value]

    @property
    def scan_patterns(self) -> list[dict]:
        """Entries whose match fails the scan (``match: block``)."""
        return self.with_match("block")

    @property
    def judgment_patterns(self) -> list[dict]:
        """Entries whose match is a judgment call to acknowledge (``match: judgment``)."""
        return self.with_match("judgment")

    @property
    def warn_patterns(self) -> list[dict]:
        """Entries whose match is reported and passed (``match: warn``)."""
        return self.with_match("warn")

    @property
    def all_patterns(self) -> list[dict]:
        """All patterns including ignored ones."""
        return self.raw.get("patterns", [])

    @property
    def notes(self) -> list[str]:
        """Free-text guidance for a reviewer, from every layer combined.

        Read per layer, like ``review_required``: merging replaces a list, so a
        project layer could otherwise drop the user's notes.
        """
        seen: dict = {}
        for layer in self.layers.values():
            for n in (layer or {}).get("notes") or []:
                seen.setdefault(n, None)
        return list(seen)

    @property
    def user_patterns_defined(self) -> bool:
        """The user layer has a ``patterns`` key (even an empty list)."""
        user = self.layers.get("user")
        return bool(user) and "patterns" in user

    @property
    def pattern_sources(self) -> dict:
        """Pattern id -> layer that first defines it (``environment`` for an
        entry derived from this machine)."""
        sources: dict = {}
        for name in LAYERS:
            layer = self.layers.get(name) or {}
            for p in layer.get("patterns") or []:
                if isinstance(p, dict) and p.get("id"):
                    sources.setdefault(p["id"], name)
        return sources

    @property
    def pattern_counts(self) -> dict:
        """Blocking pattern entries by origin (built-in, environment, personal),
        plus the judgment and warn entries, counted apart since their matches
        are not findings."""
        sources = self.pattern_sources
        counts = {"built-in": 0, "environment": 0, "personal": 0, "judgment": 0, "warn": 0}
        for p in self.patterns:
            if not p.get("pattern"):
                continue
            match = entry_match(p)
            if match in ("judgment", "warn"):
                counts[match] += 1
            else:
                origin = sources.get(p.get("id"), "user")
                counts[origin if origin in ("built-in", "environment") else "personal"] += 1
        return counts

    @property
    def thresholds(self) -> dict:
        defaults = {
            "large_amount": 300000,
            "suspicious_nonround": 10000,
            "suspicious_any": 100000,
            "cents_review": 500,
        }
        defaults.update(self.raw.get("thresholds", {}))
        return defaults

    @property
    def _entropy(self) -> dict:
        return self.raw.get("entropy", {})

    @property
    def entropy_exclusions(self) -> list[dict]:
        """Active entropy exclusion rules."""
        return [e for e in self._entropy.get("exclusions", [])
                if not e.get("disabled")]

    @property
    def entropy_enabled(self) -> bool:
        return self._entropy.get("enabled", True)

    @property
    def entropy_threshold(self) -> float:
        return self._entropy.get("threshold", 4.2)

    @property
    def entropy_min_len(self) -> int:
        return self._entropy.get("min_len", 20)

    @property
    def review(self) -> dict:
        """The merged ``review`` section: runner, model, timeout, batch_bytes."""
        return dict(self.raw.get("review") or {})

    @property
    def review_required(self) -> bool:
        """Whether a push needs a recorded review, from every layer combined.

        Read per layer rather than from the merged view: a layer can require
        the review, never lift a requirement another layer set.
        """
        return any(bool(((layer or {}).get("push") or {}).get("require_review"))
                   for layer in self.layers.values())


def _catalog_texts(layers: dict) -> list:
    """The pattern texts of the user and project layers: a derived value one
    of them equals is governed by that entry, not by the environment layer."""
    texts = []
    for name in ("user", "project"):
        for p in (layers.get(name) or {}).get("patterns") or []:
            if isinstance(p, dict) and isinstance(p.get("pattern"), str):
                texts.append(p["pattern"])
    return texts


def load_config(repo_path: str = ".") -> MergedConfig:
    """Load, validate, and merge config from all layers: the three files and
    the environment layer derived from this machine.

    Raises ConfigError if any layer is invalid.
    """
    paths = layer_paths(repo_path)
    layers = {name: load_layer(path) for name, path in paths.items()}
    layers["environment"] = {"patterns": derived_entries(repo_path, _catalog_texts(layers))}

    merged: dict = {}
    for name in LAYERS:
        if layers[name] is not None:
            merged = _merge_dicts(merged, layers[name], MERGE_KEYS)

    config = MergedConfig(raw=merged, layers=layers)
    missing = [p.get("id") for p in config.all_patterns
               if not p.get("pattern") and not p.get("specifics")]
    # An override of an environment entry this machine doesn't derive (a
    # one-word git name has no parts; another machine's .env key) has nothing
    # to apply to; it is left in place for the machine it was written on.
    orphans = {pid for pid in missing if is_derived_id(pid)}
    if orphans:
        merged["patterns"] = [p for p in merged.get("patterns", []) if p.get("id") not in orphans]
        missing = [pid for pid in missing if pid not in orphans]
    if missing:
        where = {pid: paths[config.pattern_sources.get(pid, "user")] for pid in missing}
        raise ConfigError("\n".join(
            ["Invalid config: an entry has neither 'pattern' nor 'specifics' in any layer:"]
            + [f"  {pid} (first defined in {path})" for pid, path in where.items()]
            + ["Add one with 'git-scan patterns edit <id> --pattern <regex>' or "
               "'--specifics <text>', or remove the entry with 'git-scan patterns remove <id>'."]))
    return config
