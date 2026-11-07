"""The privacy review that git-scan runs itself: a stub runner stands in for
the headless agent CLI (it reads the prompt on stdin, saves it, and prints
whatever result the test wants), so the whole path runs with no model and
no network: prompt assembly, batching, result validation, fail-closed
behavior, marks, and the push gate that depends on them."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from git_scan.cli.main import cli
from git_scan.sdk import marks, relaxed, review
from git_scan.sdk.hooks import install_hook

STUB = '''import json, os, sys
prompt = sys.stdin.read()
out = os.environ.get("STUB_PROMPT_OUT")
if out:
    open(out, "a").write(prompt + "\\n=====END PROMPT=====\\n")
mode = os.environ.get("STUB_MODE", "clean")
import re
seen = re.findall(r"^## commit ([0-9a-f]{12})$", prompt, re.M)
if mode == "partial":
    seen = seen[1:]
if mode in ("clean", "partial"):
    print(json.dumps({"status": "completed", "findings": [], "commits_reviewed": seen, "summary": "nothing personal in these commits"}))
elif mode == "finding":
    print(json.dumps({"status": "completed", "commits_reviewed": seen, "summary": "one",
                      "findings": [{"category": "names", "value": "Jane Doe",
                                    "location": "abc:notes.txt:1"}]}))
elif mode == "envelope":          # a CLI envelope carrying the result as text
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "duration_ms": 4321, "modelUsage": {"stub-model-1": {}},
                      "result": "```json\\n" + json.dumps({"status": "completed", "findings": [],
                                                           "commits_reviewed": seen, "summary": "ok"}) + "\\n```"}))
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
                "--match", "judgment", "--specifics", "only beside an address").exit_code == 0
    assert _run("notes", "add", "a city is a finding only beside an address").exit_code == 0
    return r


def _commit(repo, name, text="clean\n", verify=True):
    """Commit one file; the fixture's hooks scan it unless ``verify`` is off
    (for content that deliberately matches a judgment entry)."""
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"add {name}", *([] if verify else ["--no-verify"]))
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
    assert "# Review mode\n\npublic" in p
    assert '"pattern": "Jane Doe"' in p and '"match": "judgment"' in p   # entries, with what a match does
    assert "only beside an address" in p                       # entry specifics and free-text note
    assert first[:12] in p and second[:12] in p
    assert "+alpha line" in p and "+beta line" in p and "add a.txt" in p   # patches and messages
    assert "# Publish text (1)" in p and "Because." in p
    assert '"id": "github-pat"' in p and '"id": "people"' in p   # the merged entries, built-ins included
    assert "variants in spelling, case, spacing and form" in " ".join(p.split())   # the standing instruction


def test_findings_are_printed_and_nothing_is_recorded(repo, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "finding")
    sha = _commit(repo, "a.txt")
    r = _run("review")
    assert r.exit_code == 1
    assert "abc:notes.txt:1 [names] Jane Doe" in r.output
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


def test_the_reviewers_summary_is_reported_with_the_model_and_the_time(repo, monkeypatch):
    sha = _commit(repo, "a.txt")
    r = _run("review", "--quiet")
    assert r.exit_code == 0, r.output
    assert "reviewer: batch 1/1 in " in r.output and "s: nothing personal in these commits" in r.output
    monkeypatch.setenv("STUB_MODE", "envelope")             # a CLI envelope names the model and its timing
    _commit(repo, "b.txt")
    r = _run("review", "--quiet")
    assert r.exit_code == 0, r.output
    assert "reviewer: batch 1/1 by stub-model-1 in " in r.output and "s: ok" in r.output


def test_a_result_that_skips_a_commit_fails_closed_and_records_nothing(repo, monkeypatch):
    monkeypatch.setenv("STUB_MODE", "partial")                # the reviewer lists all but the first commit
    first = _commit(repo, "a.txt")
    second = _commit(repo, "b.txt")
    r = _run("review", "--quiet")
    assert r.exit_code == 1
    assert "did not report reviewing commit(s)" in r.output and "nothing recorded" in r.output
    assert first[:12] in r.output or second[:12] in r.output
    assert marks.marks_on(first) == {"scanned"} and marks.marks_on(second) == {"scanned"}
    assert review.covers(["abcdef1234567"], ["abcdef1234567890" + "0" * 24]) == []
    assert review.covers(["abc"], ["abcdef1234567890" + "0" * 24]) != []       # too short to count


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


def test_publish_text_is_reviewed_alone_when_every_commit_is_already_reviewed(repo, tmp_path):
    sha = _commit(repo, "a.txt")
    assert _run("review", "--quiet").exit_code == 0          # the commit is reviewed and recorded
    before = marks.marks_on(sha)
    (tmp_path / "pr.md").write_text("## Why\nEdited after the push.\n")
    r = _run("review", "--quiet", "--publish", str(tmp_path / "pr.md"))
    assert r.exit_code == 0, r.output
    assert "clean for publish text only (pr.md)" in r.output and "nothing was recorded" in r.output
    assert "reviewer: publish text in " in r.output
    p = _prompts(tmp_path)[-1]
    assert "# Commits (0)" in p and "Edited after the push." in p
    assert marks.marks_on(sha) == before                       # nothing new recorded


def test_publish_text_with_no_unpushed_commit_is_reviewed_and_a_finding_fails(repo, tmp_path, monkeypatch):
    sha = _commit(repo, "a.txt")
    _git(repo, "push", "-q", "origin", "main")
    (tmp_path / "comment.md").write_text("A comment for an issue.\n")
    r = _run("review", "--quiet", "--publish", str(tmp_path / "comment.md"))
    assert r.exit_code == 0 and "publish text only (comment.md)" in r.output
    monkeypatch.setenv("STUB_MODE", "finding")
    r = _run("review", "--quiet", "--publish", str(tmp_path / "comment.md"))
    assert r.exit_code == 1
    assert "[names] Jane Doe" in r.output and "fix the publish text" in r.output
    assert "reviewed" not in marks.marks_on(sha)


def test_no_runner_is_a_clear_error_and_nothing_to_review_says_so(repo, monkeypatch):
    _commit(repo, "a.txt")
    assert _run("review", "--runner", "/nonexistent/agent").exit_code == 1
    assert "not found" in _run("review", "--runner", "/nonexistent/agent").output
    _git(repo, "push", "-q", "origin", "main")
    r = _run("review")
    assert r.exit_code == 1 and "nothing to review" in r.output


# --- relaxed review: eligibility per clone, asked for per review ------------------

GH_STUB = '''import os, sys
answer = os.environ.get("STUB_GH", "private")
log = os.environ.get("STUB_GH_LOG")
if log:
    open(log, "a").write(" ".join(sys.argv[1:]) + "\\n")
if answer == "fail":
    print("gh: Could not resolve to a Repository", file=sys.stderr); sys.exit(1)
print(answer)
'''

GITHUB_URL = "https://github.com/octo/notes.git"


@pytest.fixture
def gh(tmp_path, monkeypatch):
    """A stand-in for the GitHub CLI on PATH: answers the visibility query
    with $STUB_GH (or fails with 'fail') and logs its arguments."""
    bin_dir = tmp_path / "ghbin"
    bin_dir.mkdir()
    script = bin_dir / "gh"
    script.write_text(f"#!{sys.executable}\n" + GH_STUB)
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_GH_LOG", str(tmp_path / "gh.log"))
    monkeypatch.setenv("STUB_GH", "private")
    return tmp_path / "gh.log"


def _eligible(repo):
    return _git(repo, "config", "--local", "--get", "git-scan.relaxed-remote", check=False).stdout.strip()


def _relaxed_stub(monkeypatch, category, value="Pat Example", entry=None):
    """The stub reviewer reports one finding of this category on this value
    (by default one no catalog entry names), naming ``entry`` if given."""
    monkeypatch.setenv("STUB_MODE", "finding")
    stub = Path(os.environ["STUB_PROMPT_OUT"]).parent / "stub.py"
    finding = f'"category": "{category}", "value": "{value}"'
    if entry:
        finding += f', "entry": "{entry}"'
    stub.write_text(STUB.replace('"category": "names", "value": "Jane Doe"', finding))


def test_every_review_is_public_unless_relaxed_is_asked_for(repo, tmp_path, gh):
    """Eligibility by itself changes nothing: a review is public until
    --relaxed asks, and the host is asked again for each relaxed review."""
    _commit(repo, "a.txt")
    _git(repo, "remote", "set-url", "origin", GITHUB_URL)
    r = _run("review")
    assert r.exit_code == 0, r.output
    assert "# Review mode\n\npublic" in _prompts(tmp_path)[0]
    assert "(public, " in r.output
    assert _run("relax").exit_code == 0
    _commit(repo, "b.txt")
    r = _run("review")                        # eligible, not asked: still public
    assert r.exit_code == 0, r.output
    assert "# Review mode\n\npublic" in _prompts(tmp_path)[1]
    assert gh.read_text().count("--jq .visibility") == 1        # only relax asked the host
    _commit(repo, "c.txt")
    r = _run("review", "--relaxed")
    assert r.exit_code == 0, r.output
    assert "# Review mode\n\nrelaxed" in _prompts(tmp_path)[2] and "(relaxed, " in r.output
    assert gh.read_text().count("--jq .visibility") == 2        # asked again for the review
    assert _run("review", "--public").exit_code == 2             # no such flag


def test_an_eligible_clone_no_longer_private_refuses_a_relaxed_review(repo, tmp_path, gh, monkeypatch):
    _commit(repo, "a.txt")
    _git(repo, "remote", "set-url", "origin", GITHUB_URL)
    assert _run("relax").exit_code == 0
    monkeypatch.setenv("STUB_GH", "public")
    r = _run("review", "--relaxed")
    assert r.exit_code == 1 and "no relaxed review" in r.output
    assert _prompts(tmp_path) == []                              # never run in another mode
    assert _run("review").exit_code == 0                         # public needs no host


def test_relax_marks_a_github_repository_reported_private_in_git_config(repo, tmp_path, gh):
    _commit(repo, "a.txt")
    _git(repo, "remote", "set-url", "origin", GITHUB_URL)
    user_layer = (tmp_path / "xdg_config" / "git-scan" / "git-scan.yaml").read_text()
    r = _run("relax")
    assert r.exit_code == 0, r.output
    assert "eligible" in r.output and "verified private by github.com" in r.output
    assert "api repos/octo/notes --jq .visibility" in gh.read_text()
    assert _eligible(repo) == GITHUB_URL
    assert (tmp_path / "xdg_config" / "git-scan" / "git-scan.yaml").read_text() == user_layer
    assert not (repo / "git-scan.yaml").exists()
    r = _run("relax")
    assert r.exit_code == 0 and "already eligible" in r.output
    assert gh.read_text().count("--jq .visibility") == 1        # nothing asked the second time
    r = _run("review", "--relaxed")
    assert r.exit_code == 0, r.output
    assert "# Review mode\n\nrelaxed" in _prompts(tmp_path)[0]
    assert gh.read_text().count("--jq .visibility") == 2        # asked again for the review


@pytest.mark.parametrize("answer", ["public", "internal", "fail", ""])
def test_relax_refuses_unless_the_host_answers_private(repo, gh, monkeypatch, answer):
    _git(repo, "remote", "set-url", "origin", GITHUB_URL)
    monkeypatch.setenv("STUB_GH", answer)
    assert _run("relax").exit_code == 1
    assert _eligible(repo) == ""


def test_relax_refuses_when_gh_is_missing(repo, monkeypatch):
    _git(repo, "remote", "set-url", "origin", GITHUB_URL)
    dirs = {str(Path(sys.executable).parent), str(Path(shutil.which("git")).parent)}
    if any((Path(d) / "gh").exists() for d in dirs):
        pytest.skip("gh is installed beside git or python")
    monkeypatch.setenv("PATH", os.pathsep.join(sorted(dirs)))
    r = _run("relax")
    assert r.exit_code == 1 and "not installed" in r.output


def test_a_relaxed_review_is_refused_when_not_eligible_or_no_longer_private(repo, tmp_path, gh,
                                                                           monkeypatch):
    _commit(repo, "a.txt")
    _git(repo, "remote", "set-url", "origin", GITHUB_URL)
    r = _run("review", "--relaxed")
    assert r.exit_code == 1 and "not marked eligible" in r.output
    assert _run("relax").exit_code == 0
    monkeypatch.setenv("STUB_GH", "public")
    r = _run("review", "--relaxed")
    assert r.exit_code == 1 and "'public', not private" in r.output
    assert _prompts(tmp_path) == []                              # never run in another mode


def test_eligibility_covers_one_exact_url(repo, tmp_path, gh):
    _commit(repo, "a.txt")
    _git(repo, "remote", "set-url", "origin", GITHUB_URL)
    assert _run("relax").exit_code == 0
    _git(repo, "remote", "set-url", "origin", "https://github.com/octo/notes-public.git")
    r = _run("review", "--relaxed")
    assert r.exit_code == 1 and "not marked eligible" in r.output


def test_no_remote_or_a_path_remote_is_never_eligible(repo, gh):
    assert _run("relax").exit_code == 1                          # a filesystem path
    _git(repo, "remote", "remove", "origin")
    r = _run("relax")
    assert r.exit_code == 1 and "no remote" in r.output


def test_hosts_github_enterprise_is_asked_with_its_hostname(repo, tmp_path, gh):
    _git(repo, "remote", "set-url", "origin", "git@git.corp.example:team/notes.git")
    r = _run("relax")
    assert r.exit_code == 1 and "not a registered host" in r.output
    assert _run("hosts", "add", "github", "git.corp.example").exit_code == 0
    assert "git.corp.example  github" in _run("hosts", "list").output
    assert _run("relax").exit_code == 0
    assert "--hostname git.corp.example repos/team/notes" in gh.read_text()


def test_hosts_other_needs_allow_relaxed_and_is_never_asked(repo, tmp_path, gh):
    _commit(repo, "a.txt")
    _git(repo, "remote", "set-url", "origin", "git@git.internal.example:me/notes.git")
    assert _run("hosts", "add", "other", "git.internal.example").exit_code == 0
    r = _run("relax")
    assert r.exit_code == 1 and "--allow-relaxed" in r.output
    assert _run("hosts", "add", "other", "git.internal.example", "--allow-relaxed").exit_code == 0
    r = _run("relax")
    assert r.exit_code == 0 and "vouched for by the user" in r.output
    assert _run("review", "--relaxed").exit_code == 0
    assert "# Review mode\n\nrelaxed" in _prompts(tmp_path)[0]
    assert not gh.exists()                                       # never asked about this host
    assert _run("hosts", "remove", "git.internal.example").exit_code == 0
    r = _run("review", "--relaxed")
    assert r.exit_code == 1 and "not a registered host" in r.output


def test_hosts_rules(repo):
    r = _run("hosts", "add", "bitbucket", "bitbucket.corp.example")
    assert r.exit_code == 1 and "not supported yet" in r.output
    assert _run("hosts", "add", "gitea", "x.example").exit_code == 1          # unknown type
    r = _run("hosts", "add", "github", "git.corp.example", "--allow-relaxed")
    assert r.exit_code == 1 and "only for hosts git-scan can't ask" in r.output
    assert _run("hosts", "add", "github", "github.com").exit_code == 1        # built in
    assert _run("hosts", "remove", "github.com").exit_code == 1
    assert _run("hosts", "remove", "nowhere.example").exit_code == 1
    assert "github.com  github (built in)" in _run("hosts", "list").output


def test_a_plugin_provider_is_used_like_a_built_in(repo, tmp_path, monkeypatch):
    """A provider installed as an entry point (git_scan.host_providers) works
    with no change to git-scan: a real distribution on sys.path, not a mock."""
    site = tmp_path / "site"
    (site / "corp_hosts-1.0.dist-info").mkdir(parents=True)
    (site / "corp_hosts.py").write_text(
        "from git_scan.sdk.hosts import HostProvider\n"
        "class Corp(HostProvider):\n"
        "    name = 'corp'\n"
        "    verifies = True\n"
        "    def visibility(self, host, owner, repo):\n"
        "        return 'private'\n")
    (site / "corp_hosts-1.0.dist-info" / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: corp-hosts\nVersion: 1.0\n")
    (site / "corp_hosts-1.0.dist-info" / "entry_points.txt").write_text(
        "[git_scan.host_providers]\ncorp = corp_hosts:Corp\n")
    monkeypatch.syspath_prepend(str(site))
    _git(repo, "remote", "set-url", "origin", "https://src.corp.example/team/notes.git")
    assert _run("hosts", "add", "corp", "src.corp.example").exit_code == 0
    r = _run("relax")
    assert r.exit_code == 0 and "verified private by src.corp.example" in r.output


def _relaxed(repo, gh):
    _git(repo, "remote", "set-url", "origin", GITHUB_URL)
    assert _run("relax").exit_code == 0


def test_a_finding_that_matches_no_entry_blocks_in_both_modes(repo, gh, monkeypatch):
    sha = _commit(repo, "a.txt")
    _relaxed(repo, gh)
    _relaxed_stub(monkeypatch, "names")                          # a name no entry lists, unnamed
    r = _run("review")
    assert r.exit_code == 1 and "[FAIL]" in r.output and "Pat Example" in r.output
    r = _run("review", "--relaxed")
    assert r.exit_code == 1 and "[FAIL]" in r.output and "reviewed" not in marks.marks_on(sha)


@pytest.mark.parametrize("entry", ["people", "trackers", "user-home-path", "git-email"])
def test_a_warn_entry_is_a_note_in_a_relaxed_review_and_blocks_in_public(repo, gh, monkeypatch, entry):
    """The built-in entries for other people, private trackers and home paths,
    and the derived identity entries, block in public and are notes in a
    relaxed review (relaxed: warn); the reviewer names the entry."""
    value = {"people": "Pat Example", "trackers": "ABC-123", "user-home-path": "/home/pat/",
             "git-email": "quill@example.com"}[entry]
    sha = _commit(repo, "a.txt")
    _relaxed(repo, gh)
    _relaxed_stub(monkeypatch, "names", value=value, entry=entry)
    assert _run("review").exit_code == 1
    r = _run("review", "--relaxed")
    assert r.exit_code == 0, r.output
    assert f"note:" in r.output and value in r.output and f"entry {entry}: warn" in r.output
    assert "reviewed" in marks.marks_on(sha)


@pytest.mark.parametrize("entry", ["personal-framing", "history"])
def test_a_relaxed_ignore_entry_is_not_sent_to_the_reviewer(repo, tmp_path, gh, monkeypatch, entry):
    """Personal framing and history narration are specifics-only built-ins:
    reported (framing as a judgment call) in public, not even sent when
    relaxed. A reviewer can only match what it was sent, so a finding it
    puts under an id it was not given fits no entry and blocks like any
    other, as a stranger's name would."""
    sha = _commit(repo, "a.txt")
    _relaxed(repo, gh)
    _relaxed_stub(monkeypatch, "framing", value="my laptop again", entry=entry)
    r = _run("review")                                           # public: the entry blocks
    assert r.exit_code == 1 and f'"id": "{entry}"' in _prompts(tmp_path)[0]
    r = _run("review", "--relaxed")
    assert f'"id": "{entry}"' not in _prompts(tmp_path)[1]
    assert r.exit_code == 1 and "reviewed" not in marks.marks_on(sha)


@pytest.mark.parametrize("entry", ["credentials", "financial", "account-ids", "github-pat"])
def test_block_entries_block_in_a_relaxed_review_too(repo, gh, monkeypatch, entry):
    sha = _commit(repo, "a.txt")
    _relaxed(repo, gh)
    value = "ghp_" + "b" * 36 if entry == "github-pat" else "the balance is 1,234.56"
    _relaxed_stub(monkeypatch, "secrets", value=value, entry=entry)
    r = _run("review", "--relaxed")
    assert r.exit_code == 1 and "[FAIL]" in r.output and "reviewed" not in marks.marks_on(sha)


# --- the reviewer is bound to the entry: its match decides, never the label ----------

def _tenant(repo):
    """A judgment entry for a tenant name, described the way that once led a
    reviewer to re-classify it as an account identifier; a note when relaxed."""
    assert _run("patterns", "add", "tenant", "--pattern", "acme360", "--category", "domain",
                "--match", "judgment", "--relaxed", "warn",
                "--specifics", "the tenant name; a match when it identifies the owner's "
                "account, not the product").exit_code == 0


def test_the_prompt_carries_each_entrys_match_for_the_mode_and_what_the_scanner_found(repo, tmp_path, gh):
    _tenant(repo)
    _commit(repo, "a.txt", "the acme360 tenant\n", verify=False)
    _relaxed(repo, gh)
    assert _run("review").exit_code == 0
    p = _prompts(tmp_path)[0]
    tenant = p.index('"id": "tenant"')
    assert '"match": "judgment"' in p[tenant:tenant + 200] and '"found": true' in p[tenant:tenant + 300]
    me = p.index('"id": "me"')
    assert '"match": "block"' in p[me:me + 200] and '"found"' not in p[me:me + 200]
    assert '"relaxed"' not in p                                  # resolved: one match per mode
    _commit(repo, "b.txt")
    assert _run("review", "--relaxed").exit_code == 0
    p = _prompts(tmp_path)[1]
    tenant = p.index('"id": "tenant"')
    assert '"match": "warn"' in p[tenant:tenant + 200]
    assert '"id": "history"' not in p                            # relaxed: ignore, not sent


def test_regression_a_judgment_entry_is_never_escalated_past_its_match(repo, gh, monkeypatch):
    """The reviewer reports the tenant name as an account identifier, a
    label that would block in every mode. The entry's match governs: in
    public it is a judgment call, reported for a look; in a relaxed review
    the entry says warn, so it is a note and the review passes."""
    _tenant(repo)
    sha = _commit(repo, "a.txt", "the acme360 tenant\n", verify=False)
    _relaxed(repo, gh)
    _relaxed_stub(monkeypatch, "account_ids", value="acme360")   # the entry is not even named
    r = _run("review")                                           # public: judgment, for a look
    assert r.exit_code == 1 and "[account_ids] acme360" in r.output
    r = _run("review", "--relaxed")                              # relaxed: warn by the entry
    assert r.exit_code == 0, r.output
    assert "note:" in r.output and "entry tenant: warn in a relaxed review" in r.output
    assert "reviewed" in marks.marks_on(sha)


def test_a_relaxed_ignore_on_the_owners_pattern_entry_is_honoured(repo, tmp_path, gh, monkeypatch):
    """A pattern entry the owner set to ignore when relaxed is not sent, and
    a value it matches is dropped even when the reviewer reports it anyway:
    the owner decided, and git-scan can check a pattern itself."""
    assert _run("patterns", "add", "handle", "--pattern", "jsmith42", "--category", "custom",
                "--match", "judgment", "--relaxed", "ignore").exit_code == 0
    sha = _commit(repo, "a.txt", "jsmith42\n", verify=False)
    _relaxed(repo, gh)
    _relaxed_stub(monkeypatch, "names", value="jsmith42")        # not even named
    assert _run("review").exit_code == 1                         # public: judgment blocks
    r = _run("review", "--relaxed")
    assert r.exit_code == 0 and "note:" not in r.output, r.output    # ignored: not even a note
    assert '"id": "handle"' not in _prompts(tmp_path)[1] and "reviewed" in marks.marks_on(sha)


def test_a_named_entry_whose_pattern_does_not_match_the_value_is_ignored(repo, gh, monkeypatch):
    """A reviewer that labels a secret with a relaxed-ignore entry's id cannot
    make the secret disappear: a pattern entry applies only when its pattern
    matches the value."""
    assert _run("patterns", "edit", "me", "--relaxed", "ignore").exit_code == 0
    fake = "ghp_" + "c" * 36
    sha = _commit(repo, "a.txt")
    _relaxed(repo, gh)
    _relaxed_stub(monkeypatch, "secrets", value=fake, entry="me")
    r = _run("review", "--relaxed")
    assert r.exit_code == 1 and "reviewed" not in marks.marks_on(sha)


def test_a_partial_match_does_not_borrow_an_entrys_match(repo, gh, monkeypatch):
    assert _run("patterns", "edit", "me", "--relaxed", "ignore").exit_code == 0
    sha = _commit(repo, "a.txt")
    _relaxed(repo, gh)
    _relaxed_stub(monkeypatch, "secrets", value="password=Jane Doe2026!")
    r = _run("review", "--relaxed")
    assert r.exit_code == 1 and "reviewed" not in marks.marks_on(sha)


def test_relaxed_publish_text_alone_follows_the_entries(repo, tmp_path, gh, monkeypatch):
    _commit(repo, "a.txt")
    _relaxed(repo, gh)
    _git(repo, "push", "-q", "origin", "main", check=False)
    (tmp_path / "body.md").write_text("A pull-request body.\n")
    _relaxed_stub(monkeypatch, "names", entry="people")
    r = _run("review", "--relaxed", "--publish", str(tmp_path / "body.md"))
    assert r.exit_code == 0 and "note:" in r.output, r.output


def test_without_an_entry_every_finding_blocks_in_both_modes():
    found = [{"category": c, "value": "v"} for c in ("secrets", "names", "personal_framing", "employer")]
    assert review.sort_findings(found, "public") == (found, [])
    assert review.sort_findings(found, "relaxed") == (found, [])
    assert review.sort_findings(found, "relaxed", {"patterns": []}) == (found, [])


def test_the_entrys_match_in_the_verdict():
    catalog = {"patterns": [
        {"id": "me", "pattern": "Jane Doe", "category": "name", "match": "warn"},
        {"id": "card", "pattern": "1234", "category": "last4", "word_boundary": True, "match": "judgment"},
        {"id": "people", "category": "people", "specifics": "another person's name", "match": "warn"},
    ], "ignored": [{"id": "old-handle", "pattern": "jd[0-9]+", "match": "ignore"}]}
    found = [{"category": "account_ids", "value": "Jane Doe"},            # whole match on a warn entry: a note
             {"category": "names", "value": "'jane doe'", "entry": "me"},    # named, matches: a note
             {"category": "financial", "value": "1234", "entry": "card"},    # judgment: blocks, for a look
             {"category": "secrets", "value": "x1234y", "entry": "me"},      # named entry doesn't match: blocks
             {"category": "secrets", "value": "Jane Doe2026"},               # partial: not the entry: blocks
             {"category": "names", "value": "Pat Example", "entry": "people"},   # specifics only, named: a note
             {"category": "names", "value": "Pat Example"},                  # unnamed, no pattern: blocks
             {"category": "handles", "value": "jd42"},                        # an ignored pattern: dropped
             {"category": "history", "value": "the agent did it", "entry": "history"}]   # unknown id: blocks
    blocking, notes = review.sort_findings(found, "relaxed", catalog)
    assert [f["value"] for f in notes] == ["Jane Doe", "'jane doe'", "Pat Example"]
    assert [f["note"] for f in notes] == ["entry me: warn in a relaxed review"] * 2 + \
        ["entry people: warn in a relaxed review"]
    assert [f["value"] for f in blocking] == ["1234", "x1234y", "Jane Doe2026", "Pat Example", "the agent did it"]
    assert blocking[0]["entry"] == "card"


def test_no_severity_is_asked_for_or_shown(repo, tmp_path):
    schema = json.loads((Path(review.__file__).parent.parent / "data" / "review-schema.json").read_text())
    assert "severity" not in json.dumps(schema)
    assert "severity" not in (Path(review.__file__).parent.parent / "data" / "review-prompt.md").read_text()


def test_every_finding_must_say_which_rule_it_breaks():
    data = Path(review.__file__).parent.parent / "data"
    schema = json.loads((data / "review-schema.json").read_text())
    assert "note" in schema["properties"]["findings"]["items"]["required"]
    prompt = " ".join((data / "review-prompt.md").read_text().split())
    assert "names the entry it matched" in prompt
    assert "report it as a finding" in prompt                 # unsure means a finding, not a warning
    assert "Never use them to explain a call" in prompt


def test_the_prompt_is_the_entries_plus_one_standing_instruction():
    """The exposure table and the prose about what to look for beyond the
    catalog are gone: the merged entries carry the rules, and the prompt
    adds one standing instruction about variants."""
    prompt = " ".join((Path(review.__file__).parent.parent / "data" / "review-prompt.md").read_text().split())
    assert "Exposure mode" not in prompt and "beyond the catalog" not in prompt
    assert "| public | relaxed |" not in prompt
    assert "covers the value's variants in spelling, case, spacing and form" in prompt
    assert "Put the entry's `id` in every finding's `entry` field" in prompt


def test_public_prospective_refuses_eligibility_and_relaxed_reviews(repo, tmp_path, gh):
    _commit(repo, "a.txt")
    _git(repo, "remote", "set-url", "origin", GITHUB_URL)
    assert _run("relax").exit_code == 0
    assert _run("config", "public-prospective", "on", "--project").exit_code == 0
    r = _run("review", "--relaxed")
    assert r.exit_code == 1 and "public-prospective" in r.output
    assert _run("relax", "--remove").exit_code == 0
    r = _run("relax")
    assert r.exit_code == 1 and "public-prospective" in r.output
    # declared in the user layer, a project layer saying off does not lift it
    assert _run("config", "public-prospective", "off", "--project").exit_code == 0
    assert _run("config", "public-prospective", "on").exit_code == 0
    assert _run("relax").exit_code == 1


def test_a_config_with_review_relaxed_fails_with_the_migration(repo):
    (repo / "git-scan.yaml").write_text("review:\n  relaxed:\n    - github.com/octo/my-\n")
    r = _run("run")
    assert r.exit_code == 1
    assert "review.relaxed is no longer supported" in r.output
    assert "git-scan relax" in r.output


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
