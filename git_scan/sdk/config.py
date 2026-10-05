"""Layered YAML config: package defaults → user → project.

Three layers are merged with higher layers winning:
  1. Package defaults (shipped with git-scan)
  2. User config (~/.config/git-scan/git-scan.yaml)
  3. Project config (./git-scan.yaml in repo root)

Uses the yaml-config-merge snippet for merging. Arrays of dicts are
merged by composite key (id, name). Setting ``disabled: true`` on an
inherited pattern suppresses it. Scalars and plain dicts: higher layer wins.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .yaml_merge import merge_yaml_configs

PACKAGE_DEFAULTS = Path(__file__).parent.parent / "data" / "git-scan.yaml"
CONFIG_FILENAME = "git-scan.yaml"


def _user_config_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "git-scan" / CONFIG_FILENAME


def _project_config_path(repo_path: str) -> Optional[Path]:
    """Find project-level config in repo root."""
    root = Path(repo_path)
    for name in (CONFIG_FILENAME, f".{CONFIG_FILENAME}", "sensitive-patterns.yaml"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


@dataclass
class MergedConfig:
    """Resolved config after three-layer merge."""
    raw: dict = field(default_factory=dict)

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
    """Load and merge config from all three layers."""
    paths = [
        PACKAGE_DEFAULTS,
        _user_config_path(),
    ]
    project_path = _project_config_path(repo_path)
    if project_path:
        paths.append(project_path)

    try:
        merged = merge_yaml_configs(paths, key_fields=["id", "name"])
    except FileNotFoundError:
        # No config files at all — use empty defaults
        merged = {}

    return MergedConfig(raw=merged)
