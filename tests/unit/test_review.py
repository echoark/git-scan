"""The privacy review that git-scan runs itself: a stub runner stands in for
the headless agent CLI (it reads the prompt on stdin, saves it, and prints
whatever result the test wants), so the whole path runs with no model and
no network: prompt assembly, batching, result validation, fail-closed
behavior, marks, and the push gate that depends on them."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from git_scan.cli.main import cli
from git_scan.sdk import marks, review
from git_scan.sdk.hooks import install_hook

STUB = '''import json, os, sys
prompt = sys.stdin.read()
out = os.environ.get("STUB_PROMPT_OUT")
if out:
    open(out, "a").write(prompt + "\\n=====END PROMPT=====\\n")
mode = os.environ.get("STUB_MODE", "clean")
if mode == "clean":
    print(json.dumps({"status": "completed", "findings": [], "commits_reviewed": [], "summary": "clean"}))
elif mode == "finding":
    print(json.dumps({"status": "completed", "commits_reviewed": [], "summary": "one",
                      "findings": [{"category": "names", "value": "Jane Doe",
                                    "location": "abc:notes.txt:1", "severity": "high"}]}))
elif mode == "envelope":          # a CLI envelope carrying the result as text
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "result": "```json\\n" + json.dumps({"status": "completed", "findings": [],
                                                           "commits_reviewed": [], "summary": "ok"}) + "\\n```"}))
elif mode == "failed":
    print(json.dumps({"status": "failed", "failure_reason": "truncated", "findings": [],
                      "commits_reviewed": [], "summary": ""}))
elif mode == "garbage":
    print("I am not JSON")
elif mode == "crash":
    print("boom", file=sys.stderr); sys.exit(3)
elif mode == "hang":
    import time; time.sleep(30)
'''


def _git(repo, *args, check=True, input=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True, input=input)


def _run(*args):
    return CliRunner().invoke(cli, list(args))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository with a bare remote, the push gate installed, and the stub
    runner configured as the reviewer."""
    stub = tmp_path / "stub.py"
    stub.write_text(STUB)
    monkeypatch.setenv("STUB_PROMPT_OUT", str(tmp_path / "prompts.txt"))
    monkeypatch.setenv("STUB_MODE", "clean")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    install_hook(tmp_path / "hooks", pre_push=True)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "remote", "add", "origin", str(remote))
    monkeypatch.chdir(r)
    assert _run("config", "review-runner", f"{sys.executable} {stub}").exit_code == 0
    assert _run("patterns", "add", "me", "--pattern", "Jane Doe", "--category", "name").exit_code == 0
    assert _run("patterns", "add", "town", "--pattern", "Springfield", "--category", "city",
                "--judgment", "--note", "only beside an address").exit_code == 0
    assert _run("notes", "add", "a city is a finding only beside an address").exit_code == 0
    return r


def _commit(repo, name, text="clean\n"):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _prompts(tmp_path):
    text = (tmp_path / "prompts.txt").read_text() if (tmp_path / "prompts.txt").exists() else ""
    return [p for p in text.split("=====END PROMPT=====\n") if p.strip()]


def test_a_clean_review_marks_every_commit_and_the_prompt_holds_what_the_reviewer_needs(repo, tmp_path):
    first = _commit(repo, "a.txt", "alpha line\n")
    second = _commit(repo, "b.txt", "beta line\n")
    (tmp_path / "pr.md").write_text("## Why\nBecause.\n")
    r = _run("review", "--publish", str(tmp_path / "pr.md"))
    assert r.exit_code == 0, r.output
    assert "clean for 2 commit(s)" in r.output and "recorded" in r.output
    assert marks.marks_on(first) == {"scanned", "reviewed"}
    assert marks.marks_on(second) == {"scanned", "reviewed"}
    prompts = _prompts(tmp_path)
    assert len(prompts) == 1
    p = prompts[0]
    assert "# Privacy review of commits" in p                 # the packaged instructions
    assert "# Exposure mode\n\npublic" in p
    assert '"pattern": "Jane Doe"' in p and '"judgment": true' in p   # catalog, with judgment entries
    assert "only beside an address" in p                       # entry note and free-text note
    assert first[:12] in p and second[:12] in p
    assert "+alpha line" in p and "+beta line" in p and "add a.txt" in p   # patches and messages
    assert "# Publish text (1)" in p and "Because." in p
    assert "github-pat" not in p                               # built-in token patterns stay out


def test_findings_are_printed_and_nothing_is_recorded(repo, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "finding")
    sha = _commit(repo, "a.txt")
    r = _run("review")
    assert r.exit_code == 1
    assert "abc:notes.txt:1 [names/high] Jane Doe" in r.output
    assert "nothing was recorded" in r.output
    assert marks.marks_on(sha) == {"scanned"}


def test_the_push_gate_requires_the_review_and_the_review_satisfies_it(repo):
    assert _run("hook", "install", "--pre-push", "--require-review").exit_code == 0
    sha = _commit(repo, "a.txt")
    refused = _git(repo, "push", "-q", "origin", "main", check=False)
    assert refused.returncode != 0
    assert "1 commit(s) not yet reviewed" in refused.stderr and "git-scan review" in refused.stderr
    assert sha[:12] in refused.stderr
    assert _run("review").exit_code == 0
    pushed = _git(repo, "push", "-q", "origin", "main", check=False)
    assert pushed.returncode == 0, pushed.stderr
    assert "review recorded" in pushed.stdout + pushed.stderr


def test_reviewed_commits_are_skipped_on_a_rerun_and_a_new_commit_is_reviewed(repo, tmp_path):
    _commit(repo, "a.txt")
    assert _run("review").exit_code == 0
    r = _run("review")
    assert r.exit_code == 0 and "nothing new to review, 1 already reviewed" in r.output
    assert len(_prompts(tmp_path)) == 1
    second = _commit(repo, "b.txt")
    r = _run("review")
    assert "clean for 1 commit(s), 1 already reviewed" in r.output
    assert marks.marks_on(second) == {"scanned", "reviewed"}
    assert len(_prompts(tmp_path)) == 2


def test_batches_split_by_size_and_a_finding_in_one_batch_leaves_the_others_recorded(repo, tmp_path, monkeypatch):
    shas = [_commit(repo, f"{n}.txt", "x" * 300 + "\n") for n in ("a", "b", "c")]
    import yaml
    cfg = tmp_path / "xdg_config" / "git-scan" / "git-scan.yaml"
    data = yaml.safe_load(cfg.read_text())
    data["review"]["batch_bytes"] = 400
    cfg.write_text(yaml.safe_dump(data))
    r = _run("review")
    assert r.exit_code == 0, r.output
    assert len(_prompts(tmp_path)) == 3            # one commit per batch at that limit
    for s in shas:
        assert "reviewed" in marks.marks_on(s)


def test_a_cli_envelope_with_the_result_as_text_is_accepted(repo, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "envelope")
    sha = _commit(repo, "a.txt")
    assert _run("review").exit_code == 0
    assert "reviewed" in marks.marks_on(sha)


@pytest.mark.parametrize("mode,message", [
    ("failed", "did not complete the review (truncated)"),
    ("garbage", "not JSON"),
    ("crash", "runner failed (exit 3): boom"),
])
def test_reviewer_problems_fail_closed_and_say_so(repo, monkeypatch, mode, message):
    monkeypatch.setenv("STUB_MODE", mode)
    sha = _commit(repo, "a.txt")
    r = _run("review")
    assert r.exit_code == 1 and message in r.output and "nothing recorded" in r.output
    assert marks.marks_on(sha) == {"scanned"}


def test_a_timeout_fails_closed(repo, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "hang")
    sha = _commit(repo, "a.txt")
    r = _run("review", "--timeout", "1")
    assert r.exit_code == 1 and "did not answer within 1s" in r.output
    assert marks.marks_on(sha) == {"scanned"}


def test_no_runner_is_a_clear_error_and_nothing_to_review_says_so(repo, monkeypatch):
    _commit(repo, "a.txt")
    assert _run("review", "--runner", "/nonexistent/agent").exit_code == 1
    assert "not found" in _run("review", "--runner", "/nonexistent/agent").output
    _git(repo, "push", "-q", "origin", "main")
    r = _run("review")
    assert r.exit_code == 1 and "nothing to review" in r.output


def test_relaxed_mode_needs_a_configured_remote_prefix(repo, tmp_path):
    _commit(repo, "a.txt")
    _git(repo, "remote", "set-url", "origin", "https://github.com/octo/my-notes.git")
    r = _run("review", "--relaxed")
    assert r.exit_code == 1 and "relaxed mode is not configured" in r.output
    assert _run("review", "relaxed", "add", "github.com/octo/my-").exit_code == 0
    assert "github.com/octo/my-" in _run("review", "relaxed", "list").output
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "remote.git"))   # push target back
    # the remote URL is read for the mode only; a local remote never matches a prefix
    r = _run("review", "--relaxed")
    assert r.exit_code == 1 and "relaxed mode is not configured" in r.output


def test_relaxed_reaches_the_prompt_when_the_remote_matches(repo, tmp_path):
    _commit(repo, "a.txt")
    _run("review", "relaxed", "add", "github.com/octo/my-")
    _git(repo, "remote", "set-url", "origin", "https://github.com/octo/my-notes.git")
    r = _run("review", "--relaxed")            # the mode reads the remote URL; nothing is pushed
    assert r.exit_code == 0, r.output
    assert "# Exposure mode\n\nrelaxed" in _prompts(tmp_path)[0]


def test_review_record_is_gone_and_there_is_no_json_export(repo):
    assert _run("review", "record", "x").exit_code == 2
    assert _run("patterns", "list", "--json").exit_code == 2


def test_parse_result_shapes():
    ok = {"status": "completed", "findings": []}
    assert review.parse_result(json.dumps(ok))["status"] == "completed"
    assert review.parse_result(json.dumps({"structured_output": ok}))["findings"] == []
    with pytest.raises(review.ReviewError, match="lacks category"):
        review.parse_result(json.dumps({"status": "completed", "findings": [{"category": "x"}]}))
    with pytest.raises(review.ReviewError, match="reviewer failed"):
        review.parse_result(json.dumps({"is_error": True, "result": "rate limited"}))
