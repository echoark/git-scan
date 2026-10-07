"""Layered YAML config: package defaults → user → project.

Three layers are merged with higher layers winning:
  1. Package defaults (shipped with git-scan)
  2. User config (``$XDG_CONFIG_HOME/git-scan/git-scan.yaml``,
     default ``~/.config/git-scan/git-scan.yaml``)
  3. Project config (``./git-scan.yaml`` in the repo root)

Uses the yaml-config-merge snippet for merging. Arrays of dicts are
merged by composite key (id, name). Setting ``disabled: true`` on an
inherited pattern suppresses it. Scalars and plain dicts: higher layer wins.

Every layer is validated when loaded. A broken file fails the scan with the
file, the spot, and the command that fixes it, rather than silently
scanning with a half-loaded config.

The ``patterns`` key of the user layer is the switch for personal patterns:
absent means "not configured" (the scan fails and says how to add one or
opt out); present, even as an empty list, means configured.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .yaml_merge import merge_yaml_configs

PACKAGE_DEFAULTS = Path(__file__).parent.parent / "data" / "git-scan.yaml"
CONFIG_FILENAME = "git-scan.yaml"

LAYERS = ("built-in", "user", "project")

TOP_LEVEL_KEYS = {"entropy", "thresholds", "patterns", "allowed_emails", "identity"}
PATTERN_FIELDS = {"id", "pattern", "category", "word_boundary",
                  "not_followed_by", "exclude_files", "disabled"}
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
                if f not in PATTERN_FIELDS:
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
            if "category" in p and not isinstance(p["category"], str):
                errors.append(f"{loc}: 'category' must be text")
            for flag in ("word_boundary", "disabled"):
                if flag in p and not isinstance(p[flag], bool):
                    errors.append(f"{loc}: '{flag}' must be true or false")
            for field in ("not_followed_by", "exclude_files"):
                val = p.get(field)
                if val is not None and (not isinstance(val, list)
                                        or not all(isinstance(x, str) for x in val)):
                    errors.append(f"{loc}: '{field}' must be a list of text")

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
        """Active patterns (disabled ones filtered out)."""
        return [p for p in self.raw.get("patterns", [])
                if not p.get("disabled")]

    @property
    def all_patterns(self) -> list[dict]:
        """All patterns including disabled."""
        return self.raw.get("patterns", [])

    @property
    def user_patterns_defined(self) -> bool:
        """The user layer has a ``patterns`` key (even an empty list)."""
        user = self.layers.get("user")
        return bool(user) and "patterns" in user

    @property
    def pattern_sources(self) -> dict:
        """Pattern id -> layer that first defines it."""
        sources: dict = {}
        for name in LAYERS:
            layer = self.layers.get(name) or {}
            for p in layer.get("patterns") or []:
                if isinstance(p, dict) and p.get("id"):
                    sources.setdefault(p["id"], name)
        return sources

    @property
    def pattern_counts(self) -> dict:
        """Active patterns by origin: built-in vs personal (user or project)."""
        sources = self.pattern_sources
        counts = {"built-in": 0, "personal": 0}
        for p in self.patterns:
            origin = sources.get(p.get("id"), "user")
            counts["built-in" if origin == "built-in" else "personal"] += 1
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


def load_config(repo_path: str = ".") -> MergedConfig:
    """Load, validate, and merge config from all three layers.

    Raises ConfigError if any layer is invalid.
    """
    paths = layer_paths(repo_path)
    layers = {name: load_layer(path) for name, path in paths.items()}

    present = [paths[name] for name in LAYERS if layers[name] is not None]
    try:
        merged = merge_yaml_configs(present, key_fields=["id", "name"])
    except FileNotFoundError:
        merged = {}

    config = MergedConfig(raw=merged, layers=layers)
    missing = [p.get("id") for p in config.patterns if not p.get("pattern")]
    if missing:
        where = {pid: paths[config.pattern_sources.get(pid, "user")] for pid in missing}
        raise ConfigError("\n".join(
            ["Invalid config: a pattern has no 'pattern' text in any layer:"]
            + [f"  {pid} (first defined in {path})" for pid, path in where.items()]
            + ["Add the text with 'git-scan patterns add <id> --pattern <text>' "
               "or remove the entry with 'git-scan patterns remove <id>'."]))
    return config
