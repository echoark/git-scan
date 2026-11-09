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
    _result(monkeypatch, findings=[{"category": "name", "value": "Jane Doe", "entry": "me",
                                    "location": "HEAD12:a.txt:1",
                                    "note": "entry me is block: the acknowledgement is not valid"}])
    r = _run("review")
    assert r.exit_code == 1 and "the acknowledgement is not valid" in r.output
    assert '"entry": "me"' in _prompts(tmp_path)[0].split("### acknowledged at commit time")[1]
    assert "reviewed" not in marks.marks_on(sha)
    r = _run("review", "ack", sha)
    assert r.exit_code == 1 and "block" in r.output and "fix the content" in r.output
    assert "reviewed" not in marks.marks_on(sha)


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
    assert marks.marks_on(first) == {"scanned"}                  # the label note: labels only
    labels = _git(ack_repo, "notes", f"--ref={marks.NOTES_REF}", "show", second).stdout
    assert labels.split() == ["reviewed", "scanned"]

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
