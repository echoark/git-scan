"""Manage the user's (or a project's) personal patterns and settings.

These functions are the one supported way to change a config layer; the
CLI ``patterns`` and ``config`` commands are thin wrappers over them.

Removing applies the intent "stop applying this pattern" whatever layer it
lives in: an entry in the target layer is deleted; a built-in is turned off
by writing ``disabled: true`` for its id into the target layer (built-ins
can't be deleted). ``restore`` turns a built-in back on.

Writing rewrites the layer file from its parsed form, so hand-written
comments in it are not preserved.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import List, Optional

import yaml

from .config import (
    load_layer, layer_paths, load_config, user_config_path, project_config_path,
    CONFIG_FILENAME, THRESHOLD_KEYS, ConfigError,
)


class PatternError(ValueError):
    """A pattern operation can't be done; the message says why and what to do."""


def target_path(repo_path: str = ".", project: bool = False) -> Path:
    if project:
        return project_config_path(repo_path) or Path(repo_path) / CONFIG_FILENAME
    return user_config_path()


def _read(path: Path) -> dict:
    return load_layer(path) or {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _entries(data: dict) -> List[dict]:
    entries = data.get("patterns")
    return entries if isinstance(entries, list) else []


def add_pattern(id: str, pattern: str, category: Optional[str] = None,
                word_boundary: bool = False, not_followed_by: Optional[List[str]] = None,
                exclude_files: Optional[List[str]] = None,
                repo_path: str = ".", project: bool = False) -> dict:
    """Add a pattern to the user (or project) layer. Fails on a duplicate id."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", id):
        raise PatternError(f"'{id}' is not a valid id: use letters, digits, '.', '_' or '-'")
    try:
        re.compile(pattern)
    except re.error as e:
        raise PatternError(f"'{pattern}' is not a valid regex: {e}")

    path = target_path(repo_path, project)
    data = _read(path)
    entries = _entries(data)
    if any(e.get("id") == id for e in entries):
        raise PatternError(f"'{id}' already exists in {path}; remove it first: "
                           f"git-scan patterns remove {id}")
    entry: dict = {"id": id, "pattern": pattern}
    if category:
        entry["category"] = category
    if word_boundary:
        entry["word_boundary"] = True
    if not_followed_by:
        entry["not_followed_by"] = list(not_followed_by)
    if exclude_files:
        entry["exclude_files"] = list(exclude_files)
    entries.append(entry)
    data["patterns"] = entries
    _write(path, data)
    return entry


EDITABLE = ("pattern", "category", "word_boundary", "not_followed_by", "exclude_files")


def edit_pattern(id: str, repo_path: str = ".", project: bool = False, **changes) -> dict:
    """Change fields of a pattern. For an entry in another layer (e.g. a
    built-in), writes an override entry holding only the changed fields;
    the merge applies them on top. Lists replace, they don't append."""
    changes = {k: v for k, v in changes.items() if k in EDITABLE and v is not None}
    if not changes:
        raise PatternError("nothing to change")
    if "pattern" in changes:
        try:
            re.compile(changes["pattern"])
        except re.error as e:
            raise PatternError(f"'{changes['pattern']}' is not a valid regex: {e}")
    config = load_config(repo_path)
    if id not in config.pattern_sources:
        raise PatternError(f"no pattern '{id}'")
    path = target_path(repo_path, project)
    data = _read(path)
    entries = _entries(data)
    mine = next((e for e in entries if e.get("id") == id), None)
    if mine is None:
        mine = {"id": id}
        entries.append(mine)
    for k, v in changes.items():
        if v in ([], False) and k != "pattern":
            mine.pop(k, None)
        else:
            mine[k] = list(v) if isinstance(v, (list, tuple)) else v
    data["patterns"] = entries
    _write(path, data)
    return mine


# --- allowed emails ---------------------------------------------------------

def allow_email(address: str, repo_path: str = ".", project: bool = False) -> None:
    if "@" not in address or " " in address:
        raise PatternError(f"'{address}' is not an email address")
    path = target_path(repo_path, project)
    data = _read(path)
    allowed = [a for a in (data.get("allowed_emails") or [])]
    if address.lower() in (a.lower() for a in allowed):
        raise PatternError(f"'{address}' is already allowed in {path}")
    allowed.append(address)
    data["allowed_emails"] = allowed
    _write(path, data)


def disallow_email(address: str, repo_path: str = ".", project: bool = False) -> None:
    path = target_path(repo_path, project)
    data = _read(path)
    allowed = data.get("allowed_emails") or []
    kept = [a for a in allowed if a.lower() != address.lower()]
    if len(kept) == len(allowed):
        raise PatternError(f"'{address}' is not in the allowed list in {path}")
    data["allowed_emails"] = kept
    _write(path, data)


def list_allowed_emails(repo_path: str = ".") -> List[str]:
    return list(load_config(repo_path).raw.get("allowed_emails") or [])


def remove_pattern(id: str, repo_path: str = ".", project: bool = False) -> str:
    """Stop applying a pattern. Returns a sentence saying what was done."""
    path = target_path(repo_path, project)
    data = _read(path)
    entries = _entries(data)
    mine = [e for e in entries if e.get("id") == id]
    config = load_config(repo_path)
    origin = config.pattern_sources.get(id)

    if not mine and origin is None:
        close = difflib.get_close_matches(id, list(config.pattern_sources), n=3)
        hint = f"; did you mean {', '.join(close)}?" if close else ""
        raise PatternError(f"no pattern '{id}'{hint}")

    remaining = [e for e in entries if e.get("id") != id]
    if origin == "built-in":
        remaining.append({"id": id, "disabled": True})
        data["patterns"] = remaining
        _write(path, data)
        if mine:
            return f"removed your version of '{id}' and turned off the built-in"
        return f"'{id}' is built in; it is now off in {path}"
    if not mine:
        raise PatternError(f"'{id}' is defined in the {origin} layer, not in {path}; "
                           f"remove it there{' (use --project)' if origin == 'project' else ''}")
    data["patterns"] = remaining
    _write(path, data)
    return f"removed '{id}' from {path}"


def restore_pattern(id: str, repo_path: str = ".", project: bool = False) -> str:
    """Turn a built-in pattern back on by dropping its override."""
    path = target_path(repo_path, project)
    data = _read(path)
    entries = _entries(data)
    override = [e for e in entries if e.get("id") == id and e.get("disabled")]
    if not override:
        raise PatternError(f"'{id}' is not turned off in {path}")
    data["patterns"] = [e for e in entries if not (e.get("id") == id and e.get("disabled"))]
    _write(path, data)
    return f"'{id}' is on again"


def clear_patterns(repo_path: str = ".", project: bool = False) -> str:
    """Declare that this layer has no personal patterns (``patterns: []``)."""
    path = target_path(repo_path, project)
    data = _read(path)
    data["patterns"] = []
    _write(path, data)
    return f"no personal patterns in {path}"


def list_patterns(repo_path: str = ".", include_disabled: bool = False) -> List[dict]:
    """Merged patterns with their source layer and state."""
    config = load_config(repo_path)
    sources = config.pattern_sources
    rows = []
    for p in config.all_patterns:
        if p.get("disabled") and not include_disabled:
            continue
        rows.append({
            "id": p.get("id"),
            "pattern": p.get("pattern", ""),
            "category": p.get("category", ""),
            "layer": sources.get(p.get("id"), "user"),
            "disabled": bool(p.get("disabled")),
        })
    return rows


# --- single settings -------------------------------------------------------

SETTINGS = {
    # name: (section, key, type, description)
    "large-amount": ("thresholds", "large_amount", int, "Flag dollar amounts at or above this"),
    "suspicious-nonround": ("thresholds", "suspicious_nonround", int,
                            "Flag non-round amounts at or above this"),
    "cents-review": ("thresholds", "cents_review", int,
                     "Flag amounts with cents at or above this"),
    "entropy-enabled": ("entropy", "enabled", bool, "Run the entropy scan"),
    "entropy-threshold": ("entropy", "threshold", float, "Shannon entropy that flags a token"),
    "entropy-min-len": ("entropy", "min_len", int, "Shortest token the entropy scan considers"),
}


def get_setting(name: str, repo_path: str = "."):
    section, key, _, _ = SETTINGS[name]
    config = load_config(repo_path)
    return config.raw.get(section, {}).get(key)


def set_setting(name: str, value, repo_path: str = ".", project: bool = False) -> None:
    section, key, typ, _ = SETTINGS[name]
    if typ is bool:
        if isinstance(value, str):
            if value.lower() in ("on", "true", "yes"):
                value = True
            elif value.lower() in ("off", "false", "no"):
                value = False
            else:
                raise PatternError(f"{name}: expected on or off")
    else:
        try:
            value = typ(value)
        except (TypeError, ValueError):
            raise PatternError(f"{name}: expected a {'whole ' if typ is int else ''}number")
        if value < 0:
            raise PatternError(f"{name}: must not be negative")
    path = target_path(repo_path, project)
    data = _read(path)
    data.setdefault(section, {})[key] = value
    _write(path, data)


def layer_status(repo_path: str = ".") -> List[dict]:
    """Each layer's path and whether its file exists."""
    return [{"layer": name, "path": str(path), "exists": path.is_file()}
            for name, path in layer_paths(repo_path).items()]
