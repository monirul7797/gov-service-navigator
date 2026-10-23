"""``gsn.release.build`` and ``gsn.release.commands`` against the real database, as ``gsn_worker``.

The inputs are an in-memory repository (``DictSource``): two services of three records each, whose gold
queries are the records' own texts, so ``FakeEmbedder`` finds each at similarity 1.0. The stack tests
share one database across runs, so every test makes its own service, record and query ids (a random
suffix) and never assumes an empty database. The services need an eval rule, so each test registers its
service ids in ``gsn.evals.RULES`` for its duration.
"""
import json
from datetime import date

import pytest
from release_helpers import COMMIT, active_release_id, audit_rows, new_actor, new_tag

from gsn import evals, store
from gsn.embed import FakeEmbedder
from gsn.gitsource import DictSource
from gsn.release import build, commands
from gsn.release.commands import CommandError

TODAY = date(2026, 10, 23)
EMBEDDER = FakeEmbedder()


def _dump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class Project:
    """A tiny repository whose files are generated from the current ``records`` and settings."""

    def __init__(self, tag):
        self.tag = tag
        # Manifest order is deliberately NOT alphabetical order (the store lists services alphabetically).
        self.sids = (f"svc_{tag}_z", f"svc_{tag}_a")
        self.records = {
            sid: [
                {
                    "id": f"{sid}-{n}", "text": f"{sid} {category} text number {n}", "heading": f"Heading {n}",
                    "category": category, "lang": "en", "source_url": f"https://example.test/{sid}/{n}",
                    "official": True,
                    **({"fetched_at": f"2026-07-1{n}T10:00:00"} if sid == self.sids[0] else {}),
                }
                for n, category in enumerate(("fees", "process", "documents"), start=1)
            ]
            for sid in self.sids
        }
        self.known_failures = {}
        self.top_k = 4
        self.expected_override = {}     # query id -> a different expected_id (makes that query fail at top_k 1)
        self.status = {sid: "live" for sid in self.sids}
        self.verified_at_field = "fetched_at"   # the manifest mapping of the first service (the other has none)

    def query_id(self, record):
        return f"gq_{record['id']}"

    def gold(self, sid):
        queries = [
            {"id": self.query_id(r), "query": r["text"], "lang": "en", "expected_category": r["category"],
             "expected_id": self.expected_override.get(self.query_id(r), r["id"])}
            for r in self.records[sid]
        ]
        queries.append({"id": f"gq_{sid}_oos", "query": "what is the weather like today", "lang": "en",
                        "expected_category": None, "expected_id": None})
        return {"top_k": self.top_k, "queries": queries}

    def source(self):
        manifest = {"services": [
            {"service_id": sid, "name_bn": f"সেবা {sid}", "name_en": f"Service {sid}", "status": self.status[sid],
             "corpus": f"data/chunks/{sid}.jsonl", "gold": f"{sid}_gold.json",
             "verified_at_field": self.verified_at_field if sid == self.sids[0] else None}
            for sid in self.sids
        ]}
        files = {
            "data/release_manifest.json": _dump(manifest),
            "data/release_thresholds.json": _dump({"known_failures": self.known_failures}),
            "concept_registry.json": _dump({"services": {sid: {} for sid in self.sids}}),
        }
        for sid in self.sids:
            files[f"data/chunks/{sid}.jsonl"] = "".join(
                _dump(r).decode("utf-8") + "\n" for r in self.records[sid]).encode("utf-8")
            files[f"{sid}_gold.json"] = _dump(self.gold(sid))
        return DictSource(COMMIT, files)

    def all_records(self):
        return [r for sid in self.sids for r in self.records[sid]]


@pytest.fixture
def project(monkeypatch):
    p = Project(new_tag())
    for sid in p.sids:
        monkeypatch.setitem(evals.RULES, sid, evals.RULES["nid"])
    return p


def run_build(connect, project, *, actor=None, embedder=EMBEDDER, **kwargs):
    kwargs = {"actor": actor or new_actor(), "note": None, "override": None, "today": TODAY, **kwargs}
    return build.build_release(connect("worker"), project.source(), embedder, **kwargs)


def release_row(connect, release_id):
    return store.release_row(connect("worker"), release_id)


def members(connect, release_id):
    """record_id -> version_id of a release."""
    return dict(connect("worker").execute(
        "SELECT record_id, version_id FROM release_members WHERE release_id = %s", (release_id,)).fetchall())


def count(connect, sql, *params):
    return connect("worker").execute(sql, params).fetchone()[0]


def audit_for(connect, actor, release_id=None):
    rows = audit_rows(connect("web"), actor)
    return [r for r in rows if release_id is None or r[1] == f"release:{release_id}"]


# --- a clean build ------------------------------------------------------------------------------


def test_clean_build_is_validated_and_audited(connect, project):
    actor = new_actor()
    result = run_build(connect, project, actor=actor, note="first build")
    assert (result.status, result.problems) == ("validated", [])
    assert isinstance(result.release_id, int)
    assert (result.records, result.embedded, result.reused_versions, result.copied_vectors) == (6, 6, 0, 0)

    row = release_row(connect, result.release_id)
    assert row["status"] == "validated" and row["source_commit"] == COMMIT and row["note"] == "first build"
    assert row["embedding_model"] == "fake-sha256-v1" and row["created_by"] == actor
    assert row["override_reason"] is None and row["is_active"] is False
    assert row["gate_report"]["passed"] is True and len(row["gate_report"]["verdicts"]) == 8
    assert row["gate_report"]["commit"] == COMMIT and row["gate_report"]["overridden"] is False
    assert row["manifest"]["services"][0]["service_id"] == project.sids[0]
    assert len(row["registry_sha256"]) == 64

    got = members(connect, result.release_id)
    assert sorted(got) == sorted(r["id"] for r in project.all_records())
    assert store.live_services(connect("worker"), result.release_id) == sorted(project.sids)
    # every stored version is complete: its text, its record, its derived columns and a unit vector
    version = connect("worker").execute(
        "SELECT embed_text, record, url_kind, verified_at, source_verified, official, embedding_model, change_note, "
        "created_by FROM record_versions WHERE version_id = %s", (got[project.records[project.sids[0]][0]["id"]],)
    ).fetchone()
    first = project.records[project.sids[0]][0]
    assert version == (first["text"], first, "portal", date(2026, 7, 11), True, True, "fake-sha256-v1",
                       "first build", actor)

    assert audit_for(connect, actor) == [
        ("release.build", f"release:{result.release_id}",
         {"commit": COMMIT, "services": 2, "records": 6, "embedded": 6, "reused_versions": 0, "copied_vectors": 0}),
        ("release.validate", f"release:{result.release_id}",
         {"counts": result.gate.counts, "unexpected_failures": [], "known_failures_seen": []}),
    ]


def test_a_search_of_the_built_release_finds_each_record_first(connect, project):
    result = run_build(connect, project)
    worker = connect("worker")
    for record in project.all_records():
        sid = next(s for s in project.sids if record in project.records[s])
        (hit, *_) = store.search(worker, result.release_id, sid, EMBEDDER.encode([record["text"]])[0], 4)
        assert hit.record_id == record["id"] and hit.sim == pytest.approx(1.0, abs=1e-6)


# --- the draft and its build audit row are one transaction -------------------------------------


def test_a_failing_build_audit_row_leaves_no_draft_behind(connect, project, monkeypatch):
    real_audit = store.audit

    def failing(conn, actor, action, target, details):
        if action == "release.build":
            raise RuntimeError("injected audit failure")
        return real_audit(conn, actor, action, target, details)

    monkeypatch.setattr(store, "audit", failing)
    releases_before = count(connect, "SELECT count(*) FROM releases")
    members_before = count(connect, "SELECT count(*) FROM release_members")
    actor = new_actor()
    with pytest.raises(RuntimeError, match="injected audit failure"):
        run_build(connect, project, actor=actor)

    # the audit step ran after create_draft had inserted everything, yet nothing at all remains
    assert count(connect, "SELECT count(*) FROM releases") == releases_before
    assert count(connect, "SELECT count(*) FROM release_members") == members_before
    assert count(connect, "SELECT count(*) FROM services WHERE service_id = ANY(%s)", list(project.sids)) == 0
    assert count(connect, "SELECT count(*) FROM records WHERE record_id LIKE %s", f"svc_{project.tag}%") == 0
    assert count(connect, "SELECT count(*) FROM record_versions WHERE record_id LIKE %s", f"svc_{project.tag}%") == 0
    assert audit_for(connect, actor) == []

    # the abandoned attempt blocks nothing: the same build now runs from scratch
    monkeypatch.setattr(store, "audit", real_audit)
    again = run_build(connect, project, actor=actor)
    assert again.status == "validated" and (again.embedded, again.reused_versions) == (6, 0)
    assert [a[0] for a in audit_for(connect, actor, again.release_id)] == ["release.build", "release.validate"]


# --- the gate -----------------------------------------------------------------------------------


def test_failing_gold_query_rejects_unless_known_or_overridden(connect, project):
    first = project.records[project.sids[0]][0]
    failing = project.query_id(first)
    project.top_k = 1
    project.expected_override[failing] = project.records[project.sids[0]][1]["id"]  # not the best hit

    # 1. unexpected FAIL: rejected, and the audit trail says so
    actor = new_actor()
    rejected = run_build(connect, project, actor=actor)
    assert rejected.status == "rejected" and rejected.gate.unexpected_failures == [failing]
    row = release_row(connect, rejected.release_id)
    assert row["status"] == "rejected" and row["override_reason"] is None
    assert row["gate_report"]["unexpected_failures"] == [failing] and row["gate_report"]["passed"] is False
    assert [a[0] for a in audit_for(connect, actor, rejected.release_id)] == ["release.build", "release.reject"]
    assert audit_for(connect, actor, rejected.release_id)[1][2]["unexpected_failures"] == [failing]

    # 2. the same commit with the failure listed in known_failures: validated (everything reused)
    project.known_failures = {failing: "documented reason"}
    actor = new_actor()
    known = run_build(connect, project, actor=actor)
    assert known.status == "validated" and known.overridden is False
    assert known.gate.known_failures_seen == [failing] and known.release_id != rejected.release_id
    assert (known.embedded, known.reused_versions) == (0, 6)
    assert release_row(connect, known.release_id)["override_reason"] is None
    assert [a[0] for a in audit_for(connect, actor, known.release_id)] == ["release.build", "release.validate"]

    # 3. no known failure but an override: validated, with the reason on the release and in the audit log
    project.known_failures = {}
    actor = new_actor()
    overridden = run_build(connect, project, actor=actor, override="owner looked at it")
    assert overridden.status == "validated" and overridden.overridden is True
    row = release_row(connect, overridden.release_id)
    assert row["status"] == "validated" and row["override_reason"] == "owner looked at it"
    assert row["gate_report"]["overridden"] is True
    trail = audit_for(connect, actor, overridden.release_id)
    assert [a[0] for a in trail] == ["release.build", "release.override"]
    assert trail[1][2]["override_reason"] == "owner looked at it" and trail[1][2]["unexpected_failures"] == [failing]


def test_a_rejected_release_cannot_be_published(connect, project):
    project.top_k = 1
    project.expected_override[project.query_id(project.records[project.sids[0]][0])] = (
        project.records[project.sids[0]][1]["id"])
    rejected = run_build(connect, project)
    before = active_release_id(connect("worker"))
    with pytest.raises(store.StoreError, match="not validated"):
        commands.publish(connect("worker"), rejected.release_id, new_actor(), allow_fake=True)
    assert active_release_id(connect("worker")) == before


def test_a_fake_release_is_published_only_with_allow_fake(connect, project):
    built = run_build(connect, project)
    actor = new_actor()
    before = active_release_id(connect("worker"))
    with pytest.raises(CommandError, match="--allow-fake"):
        commands.publish(connect("worker"), built.release_id, actor, allow_fake=False)
    assert active_release_id(connect("worker")) == before
    assert audit_for(connect, actor) == []

    commands.publish(connect("worker"), built.release_id, actor, allow_fake=True)
    assert active_release_id(connect("worker")) == built.release_id
    # the database function wrote the publish audit row; the CLI writes no second one
    assert [a[0] for a in audit_for(connect, actor)] == ["release.publish"]


# --- integrity ----------------------------------------------------------------------------------


def test_integrity_problem_writes_nothing(connect, project):
    project.records[project.sids[0]][1]["text"] = ""
    actor = new_actor()
    releases_before = count(connect, "SELECT count(*) FROM releases")
    result = run_build(connect, project, actor=actor)
    assert result.status == "integrity_failed" and result.release_id is None and result.gate is None
    assert any(project.records[project.sids[0]][1]["id"] in problem for problem in result.problems)
    assert count(connect, "SELECT count(*) FROM releases") == releases_before
    assert audit_for(connect, actor) == []
    assert count(connect, "SELECT count(*) FROM services WHERE service_id = ANY(%s)", list(project.sids)) == 0
    assert count(connect, "SELECT count(*) FROM records WHERE record_id LIKE %s", f"svc_{project.tag}%") == 0


def test_a_record_that_moved_to_another_service_is_an_integrity_problem(connect, project):
    run_build(connect, project)
    moved = project.records[project.sids[0]].pop()
    project.records[project.sids[1]].append(moved)  # the same record id, now listed under the other service
    result = run_build(connect, project)
    assert result.status == "integrity_failed" and any("stored under service" in p for p in result.problems)


def test_changed_service_names_are_an_integrity_problem(connect, project):
    run_build(connect, project)
    source = project.source()
    manifest = json.loads(source.read("data/release_manifest.json"))
    manifest["services"][0]["name_en"] = "A different name"
    source._files["data/release_manifest.json"] = _dump(manifest)
    result = build.build_release(connect("worker"), source, EMBEDDER, actor=new_actor(), note=None, override=None,
                                 today=TODAY)
    assert result.status == "integrity_failed" and any("names changed" in p for p in result.problems)


def test_an_unknown_known_failure_id_is_an_integrity_problem(connect, project):
    project.known_failures = {"gq_no_such_query": "typo"}
    result = run_build(connect, project)
    assert result.status == "integrity_failed" and any("gq_no_such_query" in p for p in result.problems)


# --- reuse --------------------------------------------------------------------------------------


def test_rebuild_same_commit_reuses_all_versions_and_vectors(connect, project):
    first = run_build(connect, project)
    versions_after_first = count(connect, "SELECT count(*) FROM record_versions WHERE record_id LIKE %s",
                                 f"svc_{project.tag}%")
    second = run_build(connect, project)
    assert second.release_id != first.release_id and second.status == "validated"
    assert (second.embedded, second.reused_versions, second.copied_vectors, second.records) == (0, 6, 0, 6)
    assert count(connect, "SELECT count(*) FROM record_versions WHERE record_id LIKE %s",
                 f"svc_{project.tag}%") == versions_after_first == 6
    assert members(connect, second.release_id) == members(connect, first.release_id)


def test_rebuild_after_a_rejected_build_creates_a_new_draft_without_failing_on_unique_keys(connect, project):
    project.top_k = 1
    project.expected_override[project.query_id(project.records[project.sids[0]][0])] = (
        project.records[project.sids[0]][1]["id"])
    rejected = run_build(connect, project)
    assert rejected.status == "rejected"
    again = run_build(connect, project, override="reviewed")
    assert again.status == "validated" and again.release_id != rejected.release_id
    assert (again.embedded, again.reused_versions) == (0, 6)
    assert members(connect, again.release_id) == members(connect, rejected.release_id)


def test_changed_provenance_field_new_version_same_vector(connect, project):
    first = run_build(connect, project)
    record = project.records[project.sids[0]][0]
    old_version = members(connect, first.release_id)[record["id"]]
    record["fetched_at"] = "2026-08-02T09:30:00"

    second = run_build(connect, project)
    assert second.status == "validated"
    assert (second.embedded, second.reused_versions, second.copied_vectors) == (0, 5, 1)
    new_version = members(connect, second.release_id)[record["id"]]
    assert new_version != old_version
    worker = connect("worker")
    assert worker.execute("SELECT verified_at FROM record_versions WHERE version_id = %s",
                          (new_version,)).fetchone()[0] == date(2026, 8, 2)
    assert worker.execute("SELECT verified_at FROM record_versions WHERE version_id = %s",
                          (old_version,)).fetchone()[0] == date(2026, 7, 11)
    assert store.stored_vectors(worker, second.release_id)[record["id"]] == \
        store.stored_vectors(worker, first.release_id)[record["id"]]
    # the other five records are the very same versions
    others = {rid: vid for rid, vid in members(connect, second.release_id).items() if rid != record["id"]}
    assert others == {rid: vid for rid, vid in members(connect, first.release_id).items() if rid != record["id"]}


def test_a_changed_manifest_mapping_creates_new_versions_with_the_dates_and_embeds_nothing(connect, project):
    first_sid, second_sid = project.sids
    project.verified_at_field = None       # commit A: the records carry fetched_at, but no field is mapped to it
    a = run_build(connect, project)
    assert a.status == "validated" and a.embedded == 6
    worker = connect("worker")
    a_members = members(connect, a.release_id)
    dated = "SELECT count(*) FROM record_versions WHERE version_id = ANY(%s) AND verified_at IS NOT NULL"
    assert worker.execute(dated, (list(a_members.values()),)).fetchone()[0] == 0

    project.verified_at_field = "fetched_at"    # commit B: the very same records, now mapped
    b = run_build(connect, project)
    assert b.status == "validated" and b.release_id != a.release_id
    assert (b.embedded, b.reused_versions, b.copied_vectors) == (0, 3, 3)
    b_members = members(connect, b.release_id)
    for n, record in enumerate(project.records[first_sid], start=1):
        assert b_members[record["id"]] != a_members[record["id"]]            # a NEW version ...
        assert worker.execute("SELECT verified_at FROM record_versions WHERE version_id = %s",
                              (b_members[record["id"]],)).fetchone()[0] == date(2026, 7, 10 + n)  # ... with the date
        assert store.stored_vectors(worker, b.release_id)[record["id"]] == \
            store.stored_vectors(worker, a.release_id)[record["id"]]       # ... and the very same vector
        assert count(connect, "SELECT count(*) FROM record_versions WHERE record_id = %s", record["id"]) == 2
    for record in project.records[second_sid]:                               # the service without a mapping: untouched
        assert b_members[record["id"]] == a_members[record["id"]]
        assert count(connect, "SELECT count(*) FROM record_versions WHERE record_id = %s", record["id"]) == 1

    c = run_build(connect, project)                                          # a plain rebuild reuses everything
    assert (c.embedded, c.reused_versions, c.copied_vectors) == (0, 6, 0)
    assert members(connect, c.release_id) == b_members


def test_changed_text_is_embedded_and_the_old_version_stays(connect, project):
    first = run_build(connect, project)
    record = project.records[project.sids[1]][2]
    record["text"] = f"{record['text']}, revised"
    second = run_build(connect, project)
    assert (second.embedded, second.reused_versions, second.copied_vectors) == (1, 5, 0)
    assert members(connect, second.release_id)[record["id"]] != members(connect, first.release_id)[record["id"]]
    assert count(connect, "SELECT count(*) FROM record_versions WHERE record_id = %s", record["id"]) == 2


def test_a_hidden_service_is_a_member_but_not_searched(connect, project):
    project.status[project.sids[1]] = "hidden"
    result = run_build(connect, project)
    assert result.status == "validated" and list(result.gate.counts) == [project.sids[0]]
    assert len(members(connect, result.release_id)) == 6
    assert store.live_services(connect("worker"), result.release_id) == [project.sids[0]]


# --- show, list, eval, rollback -----------------------------------------------------------------


def test_show_diff_vs_active(connect, project):
    first = run_build(connect, project)
    actor = new_actor()
    commands.publish(connect("worker"), first.release_id, actor, allow_fake=True)

    sid_a, sid_b = project.sids
    changed = project.records[sid_a][0]
    changed["text"] = f"{changed['text']}, revised"
    removed = project.records[sid_b].pop()
    added = dict(project.records[sid_b][0], id=f"{sid_b}-new", text=f"{sid_b} brand new text")
    project.records[sid_b].append(added)
    second = run_build(connect, project)
    assert second.status == "validated"

    shown = commands.show(connect("worker"), second.release_id)
    assert shown["active_release_id"] == first.release_id
    assert shown["diff_vs_active"] == {
        "added": [added["id"]], "removed": [removed["id"]], "changed": [changed["id"]]}
    assert shown["records"] == 6 and shown["status"] == "validated" and shown["is_active"] is False
    assert "manifest" not in shown and "registry" not in shown
    assert shown["gate_report"]["passed"] is True
    json.dumps(shown, default=str)

    own = commands.show(connect("worker"), first.release_id)
    assert own["is_active"] is True and own["diff_vs_active"] == {"added": [], "removed": [], "changed": []}


def test_list_releases_shows_the_new_release_and_marks_the_active_one(connect, project):
    built = run_build(connect, project)
    commands.publish(connect("worker"), built.release_id, new_actor(), allow_fake=True)
    rows = commands.list_releases(connect("worker"))
    ours = next(r for r in rows if r["release_id"] == built.release_id)
    assert ours["is_active"] is True and ours["status"] == "validated"
    assert [r["release_id"] for r in rows] == sorted(r["release_id"] for r in rows)
    assert sum(r["is_active"] for r in rows) == 1
    assert f"{built.release_id}" in commands.format_release_list(rows)


def test_rollback_goes_back_to_the_release_that_was_active_before(connect, project):
    one = run_build(connect, project)
    project.records[project.sids[0]][0]["fetched_at"] = "2026-08-03T09:30:00"
    two = run_build(connect, project)
    actor = new_actor()
    worker = connect("worker")
    commands.publish(worker, one.release_id, actor, allow_fake=True)
    commands.publish(worker, two.release_id, actor, allow_fake=True)
    assert commands.rollback(worker, actor) == one.release_id
    assert active_release_id(worker) == one.release_id
    assert [a[0] for a in audit_for(connect, actor)] == ["release.publish", "release.publish", "release.rollback"]


def test_eval_release_reproduces_the_gate_report_of_the_build(connect, project):
    built = run_build(connect, project)
    # the store lists services alphabetically, the build judged them in manifest order: the two differ here,
    # so this test passing proves eval follows the release's frozen manifest and not the store's order
    assert store.live_services(connect("worker"), built.release_id) == sorted(project.sids) != list(project.sids)
    result = commands.eval_release(connect("worker"), built.release_id, EMBEDDER, source=project.source())
    report = release_row(connect, built.release_id)["gate_report"]
    assert result["passed"] is True and result["counts"] == report["counts"]
    assert list(result["counts"]) == list(project.sids)
    assert [v["service_id"] for v in report["verdicts"]][0] == project.sids[0]
    assert [v["query_id"] for v in result["verdicts"]] == [v["query_id"] for v in report["verdicts"]]
    assert result["verdicts"] == report["verdicts"]


def test_eval_release_refuses_an_embedder_other_than_the_builders(connect, project):
    class Other(FakeEmbedder):
        model_id = "fake-other-v1"

    built = run_build(connect, project, embedder=Other())
    assert release_row(connect, built.release_id)["embedding_model"] == "fake-other-v1"
    with pytest.raises(CommandError, match="fake-other-v1"):
        commands.eval_release(connect("worker"), built.release_id, EMBEDDER, source=project.source())
