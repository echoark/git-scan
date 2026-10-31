"""The privacy review: git-scan runs the reviewer and records the result.

``review`` computes the commits a push would send (or takes a range),
builds one prompt per batch from the packaged reviewer prompt, the owner's
catalog (the personal entries and notes of the configuration), the commits'
patches and messages, and any publish text, and hands it on standard input
to the configured runner: a headless agent CLI started with no tools, so
the reviewer's whole world is the prompt. The JSON it returns is validated
here, and each commit of a clean batch is marked ``reviewed``, which the
push gate requires when the configuration says so.

The calling agent runs one command and reads a summary. It never holds the
reviewer's result and there is no command that accepts one, so the only
writer of the ``reviewed`` mark is this code path. Reviewer failures fail
closed: a missing runner, a non-zero exit, a timeout, or output that is not
a valid result records nothing and says the problem is the reviewer, not
the content.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from . import marks
from .config import load_config, MergedConfig
from .sources import LOG_ARGS
from .steps.git_identity import normalize_remote
from .user_patterns import export_patterns

DATA = Path(__file__).parent.parent / "data"
PROMPT_FILE = DATA / "review-prompt.md"
SCHEMA_FILE = DATA / "review-schema.json"

DEFAULT_TIMEOUT = 600          # seconds per batch
DEFAULT_BATCH_BYTES = 200_000  # patch and message text per prompt

_FENCE = re.compile(r"```(?:json|privacy-guard-result)?\s*\n(.*?)\n\s*```", re.S)


class ReviewError(ValueError):
    """The review can't run or didn't complete; nothing was recorded for the
    affected commits. The message says whether the problem is the reviewer
    or the content and what to do."""


@dataclass
class ReviewResult:
    mode: str
    runner: str
    commits: List[str] = field(default_factory=list)      # reviewed in this run
    skipped: List[str] = field(default_factory=list)      # already marked reviewed
    findings: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    batches: int = 0
    reports: List[str] = field(default_factory=list)      # one line per batch: what the reviewer said, by whom, how long

    @property
    def clean(self) -> bool:
        return not self.findings


# --- runner ----------------------------------------------------------------

def preset_argv(name: str, model: Optional[str] = None) -> Optional[List[str]]:
    """The command line for a known headless agent CLI, prompt on stdin,
    no tools, no user context, JSON out; ``model`` pins the model."""
    if name == "claude":
        argv = ["claude", "-p", "--output-format", "json", "--tools", "", "--max-turns", "1",
                "--setting-sources", "", "--disable-slash-commands", "--no-session-persistence",
                "--json-schema", SCHEMA_FILE.read_text()]
    elif name == "agy":
        argv = ["agy", "-p", "--output-format", "json"]
    else:
        return None
    if model:
        argv += ["--model", model]
    return argv


def resolve_runner(config: MergedConfig, override: Optional[str] = None) -> tuple:
    """``(name, argv)`` for the runner to use: the override, else the
    configured ``review.runner``, else the first known CLI on PATH."""
    chosen = override or config.review.get("runner")
    model = config.review.get("model")
    if chosen:
        preset = preset_argv(chosen, model)
        if preset is not None:
            if not shutil.which(preset[0]):
                raise ReviewError(f"the configured reviewer runner '{chosen}' is not installed "
                                  "(not on PATH). Install it, or set another with: "
                                  "git-scan config review-runner NAME")
            return chosen, preset
        argv = shlex.split(chosen)
        if not argv or not shutil.which(argv[0]):
            raise ReviewError(f"the configured reviewer runner command '{chosen}' was not found. "
                              "Set another with: git-scan config review-runner CMD")
        return chosen, argv
    for name in ("claude", "agy"):
        if shutil.which(name):
            return name, preset_argv(name, model)
    raise ReviewError("no reviewer runner: install the claude or agy CLI, or name a command "
                      "that reads a prompt on stdin and prints the result JSON: "
                      "git-scan config review-runner CMD")


# --- what gets reviewed ---------------------------------------------------------

def _out(repo_path: str, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo_path, capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else ""


def unpushed(repo_path: str = ".") -> List[str]:
    lines, remote = marks.current_branch_push(repo_path=repo_path)
    return marks.outgoing_commits(lines, remote, repo_path)


def range_commits(repo_path: str, rev_range: str) -> List[str]:
    res = subprocess.run(["git", "rev-list", rev_range], cwd=repo_path, capture_output=True,
                         text=True)
    if res.returncode != 0:
        raise ReviewError(res.stderr.strip() or f"git rev-list {rev_range} failed")
    return [s for s in res.stdout.split() if s]


def commit_records(repo_path: str, commits: Sequence[str]) -> List[dict]:
    """Each commit's id, author, committer, message and patch, in one git call."""
    if not commits:
        return []
    args = LOG_ARGS + ["--no-walk=unsorted", "--stdin"]
    res = subprocess.run(["git", *args], cwd=repo_path, capture_output=True, text=True,
                         errors="replace", input="\n".join(commits) + "\n")
    if res.returncode != 0:
        raise ReviewError(res.stderr.strip() or "git log failed")
    fields = res.stdout.split("\0")
    records = []
    for i in range(1, len(fields) - 4, 5):
        sha, author, committer, body, patch = fields[i:i + 5]
        records.append({"sha": sha, "author": author, "committer": committer,
                        "message": body.strip("\n"), "patch": patch.strip("\n")})
    return records


def _prefix_matches(prefix: str, remote: str) -> bool:
    """``host/owner/name-prefix`` against ``host/owner/repo``: every segment
    but the last must match exactly (host case-insensitively); the last is a
    prefix of the repository name, so ``github.com/octo/my-`` covers every
    repository of octo's whose name starts with ``my-``."""
    rule = [s for s in prefix.strip("/").split("/") if s]
    parts = remote.split("/")
    if not rule or len(rule) > len(parts):
        return False
    if rule[0].lower() != parts[0].lower():
        return False
    if rule[1:-1] != parts[1:len(rule) - 1]:
        return False
    return len(rule) == 1 or parts[len(rule) - 1].startswith(rule[-1])


def exposure_mode(repo_path: str, config: MergedConfig, relaxed: bool) -> str:
    """``relaxed`` only when asked for and the remote matches a configured
    prefix; the caller can tighten to public, never loosen."""
    if not relaxed:
        return "public"
    url = _out(repo_path, "remote", "get-url", "origin")
    remote = normalize_remote(url) if url else None
    prefixes = config.review.get("relaxed") or []
    if remote and any(_prefix_matches(p, remote) for p in prefixes):
        return "relaxed"
    raise ReviewError("relaxed mode is not configured for this remote"
                      + (f" ({remote})" if remote else "")
                      + ". Add it with: git-scan review relaxed add HOST/OWNER/PREFIX")


def build_prompt(mode: str, catalog: dict, records: List[dict],
                 publish: Dict[str, str]) -> str:
    parts = [PROMPT_FILE.read_text(), "", f"# Exposure mode\n\n{mode}", "",
             "# Catalog\n", "```json", json.dumps(catalog, indent=1, ensure_ascii=False), "```", ""]
    parts.append(f"# Commits ({len(records)})\n")
    for r in records:
        parts += [f"## commit {r['sha'][:12]}", f"author: {r['author']}",
                  f"committer: {r['committer']}", "", "### message", "```", r["message"], "```",
                  "", "### patch", "```diff", r["patch"], "```", ""]
    if publish:
        parts.append(f"# Publish text ({len(publish)})\n")
        for name, text in publish.items():
            parts += [f"## {name}", "```", text, "```", ""]
    return "\n".join(parts)


def _batches(records: List[dict], limit: int) -> List[List[dict]]:
    out: List[List[dict]] = [[]]
    size = 0
    for r in records:
        n = len(r["patch"]) + len(r["message"])
        if out[-1] and size + n > limit:
            out.append([])
            size = 0
        out[-1].append(r)
        size += n
    return [b for b in out if b]


# --- running the reviewer -----------------------------------------------------------

def parse_result(stdout: str) -> dict:
    """The reviewer's result from the runner's output: the result object
    itself, or a CLI envelope carrying it in ``structured_output`` or as JSON
    text in ``result`` (bare or fenced)."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ReviewError(f"the reviewer's output is not JSON ({e}); nothing recorded")
    meta: Dict[str, object] = {}
    if isinstance(data, dict) and "status" not in data:
        models = data.get("modelUsage")
        meta = {"model": next(iter(models), None) if isinstance(models, dict) and models else data.get("model"),
                "duration_ms": data.get("duration_ms")}
        if data.get("is_error"):
            raise ReviewError(f"the reviewer failed: {str(data.get('result'))[:200]}; "
                              "nothing recorded")
        if isinstance(data.get("structured_output"), dict):
            data = data["structured_output"]
        elif isinstance(data.get("result"), str):
            text = data["result"]
            m = _FENCE.search(text)
            try:
                data = json.loads(m.group(1) if m else text)
            except json.JSONDecodeError as e:
                raise ReviewError(f"the reviewer's result is not JSON ({e}); nothing recorded")
    if not isinstance(data, dict) or "status" not in data or not isinstance(data.get("findings"), list):
        raise ReviewError("the reviewer's result lacks 'status' or a 'findings' list; nothing recorded")
    data["runner_meta"] = meta
    for f in data["findings"]:
        if not isinstance(f, dict) or not all(k in f for k in ("category", "value", "location")):
            raise ReviewError("a finding in the reviewer's result lacks category, value or location; "
                              "nothing recorded")
    return data


def covers(reported: Sequence[str], shas: Sequence[str]) -> List[str]:
    """The commits in ``shas`` the reviewer did not list in ``commits_reviewed``
    (ids may be abbreviated either way, 7 characters or more)."""
    ids = [str(r).strip() for r in reported if len(str(r).strip()) >= 7]
    return [s for s in shas if not any(s.startswith(r) or r.startswith(s) for r in ids)]


def run_batch(argv: List[str], prompt: str, timeout: int) -> dict:
    started = time.monotonic()
    try:
        res = subprocess.run(argv, input=prompt, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ReviewError(f"the reviewer did not answer within {timeout}s; nothing recorded. "
                          "Run again (reviewed commits are skipped), or raise the limit: "
                          "git-scan config review-timeout SECONDS")
    except OSError as e:
        raise ReviewError(f"could not start the reviewer runner: {e}")
    if res.returncode != 0:
        tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
        raise ReviewError("the reviewer runner failed (exit " + str(res.returncode) + "): "
                          + " | ".join(tail) + "; nothing recorded")
    data = parse_result(res.stdout)
    data["runner_meta"]["seconds"] = round(time.monotonic() - started, 1)
    if data["status"] != "completed":
        raise ReviewError("the reviewer did not complete the review"
                          + (f" ({data.get('failure_reason')})" if data.get("failure_reason") else "")
                          + "; nothing recorded. Run it again")
    return data


def review(repo_path: str = ".", commits: Optional[List[str]] = None,
           rev_range: Optional[str] = None, relaxed: bool = False,
           publish: Sequence[str] = (), runner: Optional[str] = None,
           timeout: Optional[int] = None, batch_bytes: Optional[int] = None,
           progress: Optional[Callable[[str], None]] = None) -> ReviewResult:
    """Review the unpushed commits (or ``commits`` / ``rev_range``) and record
    each commit of a clean batch as reviewed."""
    say = progress or (lambda _msg: None)
    config = load_config(repo_path)
    if not config.user_patterns_defined:
        raise ReviewError("no personal patterns are configured; set them up (or declare none "
                          "with: git-scan patterns clear) before reviewing")
    name, argv = resolve_runner(config, runner)
    mode = exposure_mode(repo_path, config, relaxed)
    timeout = timeout or int(config.review.get("timeout") or DEFAULT_TIMEOUT)
    batch_bytes = batch_bytes or int(config.review.get("batch_bytes") or DEFAULT_BATCH_BYTES)

    if rev_range:
        wanted = range_commits(repo_path, rev_range)
    elif commits is not None:
        wanted = list(commits)
    else:
        wanted = unpushed(repo_path)
    if not wanted:
        raise ReviewError("nothing to review: no unpushed commits on this branch")
    marked = marks.marks_of(wanted, repo_path)
    result = ReviewResult(mode=mode, runner=name)
    result.skipped = [s for s in wanted if marks.REVIEWED in marked[s]]
    todo = [s for s in wanted if marks.REVIEWED not in marked[s]]
    if not todo:
        say(f"all {len(wanted)} commit(s) already reviewed")
        return result

    texts: Dict[str, str] = {}
    for p in publish:
        try:
            texts[Path(p).name] = Path(p).read_text(errors="replace")
        except OSError as e:
            raise ReviewError(f"publish text {p} can't be read: {e}")

    catalog = export_patterns(repo_path)
    catalog.pop("configured", None)
    records = commit_records(repo_path, todo)
    batches = _batches(records, batch_bytes)
    result.batches = len(batches)
    say(f"reviewing {len(todo)} commit(s) in {len(batches)} batch(es) with {name}"
        + (f"; {len(result.skipped)} already reviewed" if result.skipped else ""))
    for i, batch in enumerate(batches, 1):
        say(f"batch {i}/{len(batches)}: {len(batch)} commit(s)")
        prompt = build_prompt(mode, catalog, batch, texts if i == 1 else {})
        data = run_batch(argv, prompt, timeout)
        shas = [r["sha"] for r in batch]
        missing = covers(data.get("commits_reviewed") or [], shas)
        if missing:
            raise ReviewError("the reviewer did not report reviewing commit(s) "
                              + ", ".join(s[:12] for s in missing)
                              + "; nothing recorded for this batch. Run it again")
        result.warnings += [str(w) for w in data.get("warnings") or []]
        result.commits += shas
        meta = data.get("runner_meta") or {}
        by = " by " + str(meta["model"]) if meta.get("model") else ""
        secs = f" in {meta['seconds']}s" if meta.get("seconds") is not None else ""
        report = f"batch {i}/{len(batches)}{by}{secs}: {str(data.get('summary') or '').strip() or '(no summary)'}"
        result.reports.append(report)
        say(report)
        if data["findings"]:
            result.findings += data["findings"]
            say(f"batch {i}/{len(batches)}: {len(data['findings'])} finding(s); not recorded")
        else:
            marks.add_marks(marks.REVIEWED, shas, repo_path)
            say(f"batch {i}/{len(batches)}: clean; recorded")
    return result


# --- relaxed remotes ----------------------------------------------------------------

def relaxed_list(repo_path: str = ".") -> List[str]:
    return list(load_config(repo_path).review.get("relaxed") or [])
