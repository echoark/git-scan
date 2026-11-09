"""One acknowledgement model across the scan and the review.

The judgment matches acknowledged at commit time go with the commit to the
review, where the reviewer (a stub here, printing whatever result the test
sets) may honour them; the findings of a flagged review are saved per
commit and can be acknowledged with ``git-scan review ack`` when none of
them is block. The scan's acknowledgement path, the label note and the
push gate are the real ones."""

import json
import os
import sys
from pathlib import Path

import pytest

from git_scan.sdk import marks, review
from git_scan.sdk.user_patterns import export_patterns
from tests.unit.test_review import _commit, _git, _prompts, _run, gh, repo  # noqa: F401 (fixtures)

STUB = '''import json, os, re, sys
prompt = sys.stdin.read()
open(os.environ["STUB_PROMPT_OUT"], "a").write(prompt + "\\n=====END PROMPT=====\\n")
seen = re.findall(r"^## commit ([0-9a-f]{12})$", prompt, re.M)
result = json.loads(os.environ.get("STUB_RESULT") or '{"findings": [], "acknowledged": []}')
result.update(status="completed", commits_reviewed=seen, summary="stub")
text = json.dumps(result)
if seen:
    text = text.replace("HEAD12", seen[0])
print(text)
'''


@pytest.fixture
def ack_repo(repo, tmp_path, monkeypatch):
    (tmp_path / "stub.py").write_text(STUB)
    monkeypatch.delenv("STUB_RESULT", raising=False)
    return repo


def _result(monkeypatch, findings=(), acknowledged=()):
    """The stub reviewer's answer; ``HEAD12`` in a location becomes the
    first commit of the batch."""
    monkeypatch.setenv("STUB_RESULT", json.dumps({"findings": list(findings),
                                                  "acknowledged": list(acknowledged)}))


def _ack_record(repo):
    return json.loads((repo / ".git" / "git-scan-ack-tree").read_text())


def _commit_with_ack(repo, name="a.txt", text="we met in Springfield\n"):
    """Commit a judgment match through the hooks, acknowledging it as the
    refusal says."""
    (repo / name).write_text(text)
    _git(repo, "add", name)
    refused = _git(repo, "commit", "-q", "-m", f"add {name}", check=False)
    assert refused.returncode != 0 and "git-scan ack" in refused.stdout + refused.stderr
    r = _run("ack")
    assert r.exit_code == 0, r.output
    _git(repo, "commit", "-q", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


SPRINGFIELD = {"entry": "town", "path": "a.txt", "side": "added", "line": 1, "text": "Springfield"}


def test_the_scan_ack_records_its_matches_and_the_post_commit_hook_carries_them(ack_repo):
    (ack_repo / "a.txt").write_text("we met in Springfield\n")
    _git(ack_repo, "add", "a.txt")
    assert _run("ack").exit_code == 0
    record = _ack_record(ack_repo)
    assert record["matches"] == [SPRINGFIELD]
    assert record["tree"] == _git(ack_repo, "write-tree").stdout.strip()
    _git(ack_repo, "commit", "-q", "-m", "add a.txt")
    sha = _git(ack_repo, "rev-parse", "HEAD").stdout.strip()
    assert not (ack_repo / ".git" / "git-scan-ack-tree").exists()          # consumed
    assert marks.marks_on(sha) == {"scanned"}                              # the label note as before
    assert marks.review_note(sha) == {"scan_acks": [SPRINGFIELD]}
    labels = _git(ack_repo, "notes", f"--ref={marks.NOTES_REF}", "show", sha).stdout
    assert labels.strip() == "scanned"                                     # no JSON in the label note

    # a removed line is recorded as removed
    (ack_repo / "a.txt").write_text("we met\n")
    _git(ack_repo, "add", "a.txt")
    assert _run("ack").exit_code == 0
    assert _ack_record(ack_repo)["matches"] == [dict(SPRINGFIELD, side="removed")]


def test_an_ack_for_another_tree_is_not_carried(ack_repo):
    (ack_repo / "a.txt").write_text("we met in Springfield\n")
    _git(ack_repo, "add", "a.txt")
    assert _run("ack").exit_code == 0
    (ack_repo / "b.txt").write_text("clean\n")
    _git(ack_repo, "add", "b.txt")                                         # staged more: ack no longer applies
    assert _git(ack_repo, "commit", "-q", "-m", "x", check=False).returncode != 0
    _git(ack_repo, "commit", "-q", "--no-verify", "-m", "x")
    sha = _git(ack_repo, "rev-parse", "HEAD").stdout.strip()
    assert marks.marks_on(sha) == set() and marks.review_note(sha) == {}


def test_the_acks_reach_the_review_prompt_beside_their_commit(ack_repo, tmp_path):
    sha = _commit_with_ack(ack_repo)
    other = _commit(ack_repo, "b.txt")
    assert _run("review").exit_code == 0
    p = _prompts(tmp_path)[0]
    section = p[p.index(f"## commit {sha[:12]}"):]
    acks = section[section.index("### acknowledged at commit time"):]
    assert "looked at each of these matches and accepted it" in acks
    assert '"entry": "town"' in acks and '"text": "Springfield"' in acks and '"side": "added"' in acks
    newest = p[p.index(f"## commit {other[:12]}"):p.index(f"## commit {sha[:12]}")]   # newest first
    assert "+clean" in newest and "### acknowledged at commit time" not in newest
    rules = " ".join(p.split())
    assert "covers only the one match the user accepted" in rules
    assert "the acknowledgement is invalid: report it as a finding" in rules


def test_an_honoured_ack_is_listed_and_the_commit_recorded(ack_repo, monkeypatch):
    sha = _commit_with_ack(ack_repo)
    _result(monkeypatch, acknowledged=[{"entry": "town", "location": "HEAD12:a.txt:1",
                                        "value": "Springfield", "ack": "town a.txt:1 added"}])
    r = _run("review")
    assert r.exit_code == 0, r.output
    assert f"acknowledged: {sha[:12]}:a.txt:1 [town] Springfield (ack honoured: town a.txt:1 added)" \
        in r.output
    assert marks.marks_on(sha) == {"scanned", "reviewed"}


def test_an_ack_claim_on_a_block_entry_comes_back_as_a_finding(ack_repo, tmp_path, monkeypatch):
    """A claim the scan never made (here written into the note by hand) on a
    block entry reaches the reviewer, which reports it; the finding follows
    the entry and blocks, and review ack cannot clear it."""
    sha = _commit(ack_repo, "a.txt", "Jane Doe\n", verify=False)
    marks.update_review_note(sha, {"scan_acks": [{"entry": "me", "path": "a.txt", "side": "added",
                                                  "line": 1, "text": "Jane Doe"}]})
    # the value is the claim, not the entry's pattern: the entry still governs
    _result(monkeypatch, findings=[{"category": "name", "value": "ack of me at a.txt:1",
                                    "entry": "me",
                                    "location": "HEAD12:a.txt:1",
                                    "note": "entry me is block: the acknowledgement is not valid"}])
    r = _run("review")
    assert r.exit_code == 1 and "the acknowledgement is not valid" in r.output
    assert '"entry": "me"' in _prompts(tmp_path)[0].split("### acknowledged at commit time")[1]
    saved = marks.review_note(sha)["findings"]
    assert [(f["entry"], f["match"]) for f in saved] == [("me", "block")]
    assert "reviewed" not in marks.marks_on(sha)
    r = _run("review", "ack", sha)
    assert r.exit_code == 1 and "block" in r.output and "fix the content" in r.output
    assert "reviewed" not in marks.marks_on(sha)


def test_a_block_pattern_inside_a_longer_value_is_block_and_review_ack_refuses(ack_repo, monkeypatch):
    """A value with words around a block entry's pattern fits no entry
    whole, but it holds a block pattern, so it is saved as block."""
    sha = _commit(ack_repo, "a.txt", "ask Jane Doe about it\n", verify=False)
    _result(monkeypatch, findings=[{"category": "names", "value": "ask Jane Doe about it",
                                    "location": "HEAD12:a.txt:1", "note": "a name in prose"}])
    assert _run("review").exit_code == 1
    assert [f["match"] for f in marks.review_note(sha)["findings"]] == ["block"]
    r = _run("review", "ack", sha)
    assert r.exit_code == 1 and "fix the content" in r.output and "reviewed" not in marks.marks_on(sha)


def test_an_entry_less_finding_with_no_block_pattern_is_ackable(ack_repo, monkeypatch):
    sha = _commit(ack_repo, "a.txt")
    _result(monkeypatch, findings=[{"category": "names", "value": "ask Pat Example about it",
                                    "entry": None, "location": "HEAD12:a.txt:1", "note": "a name"}])
    assert _run("review").exit_code == 1
    assert [(f["entry"], f["match"]) for f in marks.review_note(sha)["findings"]] == [(None, "judgment")]
    assert _run("review", "ack", sha).exit_code == 0 and "reviewed" in marks.marks_on(sha)


def test_what_a_saved_finding_is():
    catalog = {"patterns": [{"id": "me", "pattern": "Jane Doe", "match": "block"},
                            {"id": "town", "pattern": "Springfield", "match": "judgment"},
                            {"id": "people", "specifics": "another person's name", "match": "judgment"},
                            {"id": "nick", "pattern": "jd[0-9]+", "match": "warn"}]}
    blocks = review.compile_patterns([catalog["patterns"][0]])
    m = lambda **f: review.finding_match(dict({"category": "x"}, **f), catalog, blocks)
    assert m(value="Springfield") == "judgment"                         # a judgment entry, whole
    assert m(value="Pat Example", entry="people") == "judgment"         # a specifics-only judgment entry
    assert m(value="says Jane Doe wrote it") == "block"                 # a block pattern inside
    assert m(value="ack of me at a.txt:1", entry="me") == "block"       # named block entry, no full match
    assert m(value="near Springfield Ave", entry="town") == "judgment"  # named, its pattern occurs in it
    assert m(value="near Shelbyville", entry="town") == "block"         # named, its pattern absent
    assert m(value="jd42 again", entry="nick") == "block"               # named warn entry, no full match
    assert m(value="Pat Example") == "judgment"                         # truly entry-less
    assert m(value="Pat Example", entry="no-such-entry") == "judgment"  # an id the catalog lacks
    assert m(value="Jane Doe's notes", entry="no-such-entry") == "block"


def test_an_env_value_inside_an_entry_less_finding_is_block(ack_repo, monkeypatch):
    """A .env value is a block entry the reviewer never sees; a finding that
    holds one is still block."""
    secret = "s3cr3t" + "Value" * 2
    (ack_repo / ".env").write_text(f"API_TOKEN={secret}\n")
    sha = _commit(ack_repo, "a.txt")
    _result(monkeypatch, findings=[{"category": "secret", "value": f"token {secret} in a log",
                                    "location": "HEAD12:a.txt:1", "note": "looks like a secret"}])
    assert _run("review").exit_code == 1
    assert [f["match"] for f in marks.review_note(sha)["findings"]] == ["block"]


@pytest.mark.parametrize("refspec", [f"{marks.NOTES_REF}:{marks.NOTES_REF}",
                                     f"{marks.REVIEW_REF}:{marks.REVIEW_REF}", "--mirror"])
def test_the_push_gate_refuses_a_push_of_git_scans_own_records(ack_repo, monkeypatch, refspec):
    assert _run("hook", "install", "--pre-push").exit_code == 0
    sha = _commit_with_ack(ack_repo)                             # writes both notes refs
    _result(monkeypatch, findings=[{"category": "people", "value": "Pat Example", "entry": "people",
                                    "location": "HEAD12:a.txt:1", "note": "a name"}])
    _commit(ack_repo, "b.txt")
    assert _run("review").exit_code == 1
    assert _git(ack_repo, "rev-parse", "--verify", marks.REVIEW_REF).returncode == 0
    args = ["push", "--mirror", "origin"] if refspec == "--mirror" else ["push", "origin", refspec]
    refused = _git(ack_repo, *args, check=False)
    assert refused.returncode != 0
    assert "git-scan's own records" in refused.stderr and "never pushed" in refused.stderr
    remote = ack_repo.parent / "remote.git"
    assert _git(remote, "for-each-ref", "refs/notes").stdout.strip() == ""
    assert _git(ack_repo, "push", "-q", "origin", "main", check=False).returncode == 0   # branches still push
    assert sha in _git(remote, "rev-list", "main").stdout


def test_private_refs_in():
    lines = [f"refs/heads/main {'a' * 40} refs/heads/main {'0' * 40}",
             f"{marks.REVIEW_REF} {'b' * 40} {marks.REVIEW_REF} {'0' * 40}",
             f"(delete) {'0' * 40} {marks.NOTES_REF} {'c' * 40}"]
    assert marks.private_refs_in(lines) == [marks.REVIEW_REF, marks.NOTES_REF]
    assert marks.private_refs_in(lines[:1]) == []


def test_a_new_concern_in_acked_content_is_still_reported(ack_repo, monkeypatch):
    sha = _commit_with_ack(ack_repo, text="we met Pat Example in Springfield\n")
    _result(monkeypatch,
            acknowledged=[{"entry": "town", "location": "HEAD12:a.txt:1", "value": "Springfield",
                           "ack": "town a.txt:1"}],
            findings=[{"category": "people", "value": "Pat Example", "entry": "people",
                       "location": "HEAD12:a.txt:1", "note": "people: another person's name"}])
    r = _run("review")
    assert r.exit_code == 1
    assert "acknowledged:" in r.output and "[people] Pat Example" in r.output
    assert f"git-scan review ack {sha[:12]}" in r.output
    assert "reviewed" not in marks.marks_on(sha)


def test_findings_are_saved_per_commit_in_their_own_notes_ref(ack_repo, monkeypatch):
    first = _commit(ack_repo, "a.txt")
    second = _commit(ack_repo, "b.txt")
    _result(monkeypatch, findings=[{"category": "people", "value": "Pat Example", "entry": "people",
                                    "location": f"{first[:12]}:a.txt:1", "note": "a name"},
                                   {"category": "names", "value": "Pat Example",
                                    "location": "publish:pr.md:1", "note": "no entry"}])
    assert _run("review").exit_code == 1
    raw = _git(ack_repo, "notes", f"--ref={marks.REVIEW_REF}", "show", first).stdout
    saved = json.loads(raw)
    assert saved["mode"] == "public"
    assert saved["findings"] == [{"entry": "people", "match": "judgment",
                                  "location": f"{first[:12]}:a.txt:1", "value": "Pat Example",
                                  "category": "people", "reason": "a name", "mode": "public"}]
    assert marks.review_note(second)["findings"] == []           # publish text is no commit's
    labels = _git(ack_repo, "notes", f"--ref={marks.NOTES_REF}", "show", first).stdout
    assert labels.split() == ["scanned"]                         # the label note: labels only

    # a later review replaces the saved findings: they are always the latest
    _result(monkeypatch, findings=[{"category": "people", "value": "Lee Example", "entry": "people",
                                    "location": f"{first[:12]}:a.txt:1", "note": "a name"}])
    assert _run("review").exit_code == 1
    assert [f["value"] for f in marks.review_note(first)["findings"]] == ["Lee Example"]


def test_review_ack_on_the_flagged_commit_is_enough_for_its_batch(ack_repo, tmp_path, monkeypatch):
    """The commits of a flagged batch that had no findings are recorded by
    that review, so accepting the flagged commit's findings lets the push
    through without a second review run."""
    assert _run("hook", "install", "--pre-push", "--require-review").exit_code == 0
    flagged = _commit(ack_repo, "a.txt")
    clean = _commit(ack_repo, "b.txt")
    _result(monkeypatch, findings=[{"category": "people", "value": "Pat Example", "entry": "people",
                                    "location": f"{flagged[:12]}:a.txt:1", "note": "a name"}])
    r = _run("review")
    assert r.exit_code == 1 and "Recorded as reviewed: 1 commit(s)" in r.output
    assert marks.marks_on(clean) == {"scanned", "reviewed"}
    assert marks.marks_on(flagged) == {"scanned"}
    assert _run("review", "ack", flagged).exit_code == 0
    assert _git(ack_repo, "push", "-q", "origin", "main", check=False).returncode == 0
    assert len(_prompts(tmp_path)) == 1                          # one review run in all


@pytest.mark.parametrize("location", ["publish:pr.md:1", "message", "0123456789ab:a.txt:1"],
                         ids=["publish text", "no commit", "unknown commit"])
def test_a_finding_that_names_no_commit_of_the_batch_records_nothing(ack_repo, tmp_path,
                                                                    monkeypatch, location):
    """Clean commits of a flagged batch are recorded only when every finding
    names a commit of the batch; one on the publish text, or one that names
    no commit or another one, leaves the whole batch unrecorded."""
    flagged = _commit(ack_repo, "a.txt")
    clean = _commit(ack_repo, "b.txt")
    (tmp_path / "pr.md").write_text("A pull-request body.\n")
    _result(monkeypatch, findings=[{"category": "people", "value": "Pat Example", "entry": "people",
                                    "location": f"{flagged[:12]}:a.txt:1", "note": "a name"},
                                   {"category": "names", "value": "Lee Example",
                                    "location": location, "note": "a name"}])
    r = _run("review", "--publish", str(tmp_path / "pr.md"))
    assert r.exit_code == 1 and "Recorded as reviewed" not in r.output
    assert marks.marks_on(clean) == {"scanned"} and marks.marks_on(flagged) == {"scanned"}


def test_names_a_commit():
    shas = ["a" * 40, "b" * 40]
    assert review.names_a_commit("aaaaaaaaaaaa:x.txt:1", shas)
    assert not review.names_a_commit("publish:pr.md:1", shas)
    assert not review.names_a_commit("message", shas)
    assert not review.names_a_commit("cccccccccccc:x.txt:1", shas)


def test_review_ack_records_the_commit_when_every_finding_is_judgment(ack_repo, monkeypatch):
    assert _run("hook", "install", "--pre-push", "--require-review").exit_code == 0
    sha = _commit(ack_repo, "a.txt")
    _result(monkeypatch, findings=[{"category": "people", "value": "Pat Example", "entry": "people",
                                    "location": "HEAD12:a.txt:1", "note": "a public author's name"},
                                   {"category": "names", "value": "Lee Example",
                                    "location": "HEAD12:message", "note": "fits no entry"}])
    assert _run("review").exit_code == 1
    assert _git(ack_repo, "push", "-q", "origin", "main", check=False).returncode != 0
    r = _run("review", "ack", sha[:12])
    assert r.exit_code == 0, r.output
    assert "[people] Pat Example (people: judgment, public review)" in r.output
    assert "a public author's name" in r.output
    assert "[names] Lee Example (no entry: judgment, public review)" in r.output
    assert "acknowledged 2 review finding(s)" in r.output
    assert marks.marks_on(sha) == {"scanned", "reviewed"}
    assert _git(ack_repo, "push", "-q", "origin", "main", check=False).returncode == 0
    assert "already recorded as reviewed" in _run("review", "ack", sha).output


def test_review_ack_is_refused_with_nothing_saved(ack_repo):
    sha = _commit(ack_repo, "a.txt")
    r = _run("review", "ack", sha)
    assert r.exit_code == 1 and "no review findings are saved" in r.output
    assert "reviewed" not in marks.marks_on(sha)
    assert _run("review", "ack", "nope").exit_code == 1


def test_people_is_a_judgment_entry_in_a_public_review_and_a_note_when_relaxed(ack_repo):
    by_id = lambda relaxed: {e["id"]: e for e in export_patterns(".", relaxed=relaxed)["patterns"]}
    assert by_id(False)["people"]["match"] == "judgment"
    assert by_id(True)["people"]["match"] == "warn"


def test_a_finding_is_saved_on_the_commit_its_location_names():
    shas = ["a" * 40, "b" * 40]
    assert review._commits_of("bbbbbbbbbbbb:x.txt:3", shas) == [shas[1]]
    assert review._commits_of("publish:pr.md:1", shas) == []
    assert review._commits_of("somewhere", shas) == shas            # no commit named: all of them
    assert review.finding_match({"value": "x"}, {"patterns": []}) == "judgment"
