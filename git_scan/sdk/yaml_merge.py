# --- Reusable Snippet: yaml-config-merge v1 ---
# Managed by skill: yaml-config-merge
# Keep consistent with the latest version of this snippet.
# Do not modify without updating the skill definition.
# ------------------------------------------------
from __future__ import annotations

from pathlib import Path
import yaml


def merge_yaml_configs(
    paths: list[Path | str], key_fields: list[str] | None = None
) -> dict:
    """Load YAML files in order and deep-merge them (later layers win).

    Arrays of dicts are merged by composite identity built from key_fields
    (default: ['id', 'name']). Missing files are silently skipped.
    At least one file must exist.
    """
    key_fields = key_fields or ["id", "name"]
    merged: dict = {}
    found = False
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        found = True
        with open(p) as f:
            layer = yaml.safe_load(f) or {}
        merged = _merge_dicts(merged, layer, key_fields)
    if not found:
        raise FileNotFoundError(f"No config files found in: {paths}")
    return merged


def _merge_dicts(base: dict, overlay: dict, key_fields: list[str]) -> dict:
    key = lambda item: "|".join(str(item.get(f, "")) for f in key_fields)
    result = dict(base)
    for field, value in overlay.items():
        existing = result.get(field)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[field] = _merge_dicts(existing, value, key_fields)
        elif (
            isinstance(existing, list)
            and isinstance(value, list)
            and existing
            and isinstance(existing[0], dict)
        ):
            merged = {key(obj): dict(obj) for obj in existing}
            for obj in value:
                k = key(obj)
                if k in merged:
                    merged[k].update(obj)
                else:
                    merged[k] = dict(obj)
            result[field] = list(merged.values())
        else:
            result[field] = value
    return result
