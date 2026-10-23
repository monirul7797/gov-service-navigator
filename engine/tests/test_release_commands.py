"""``gsn.release.commands`` with the store replaced by small fakes (the real SQL is in tests/integration)."""
import json
from datetime import datetime, timezone

import pytest

from gsn import evals, store
from gsn.embed import FakeEmbedder
from gsn.gitsource import DictSource, GitSourceError
from gsn.release import commands
from gsn.release.commands import CommandError
from gsn.retrieval import Hit

COMMIT = "0123456789abcdef0123456789abcdef01234567"
REAL_MODEL = "BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181"


def _manifest(*service_ids):
    return {"services": [{"service_id": sid, "status": "live"} for sid in service_ids]}


def _release(release_id=7, *, model="fake-sha256-v1", status="validated", is_active=False, known=None,
             services=("alpha",)):
    return {
        "release_id": release_id, "status": status, "source_commit": COMMIT, "manifest": _manifest(*services),
        "registry": {"services": {"big": {}}}, "registry_sha256": "f" * 64,
        "thresholds": {"known_failures": known or {}}, "gate_report": {"passed": True},
        "embedding_model": model, "note": "n", "override_reason": None,
        "created_at": datetime(2026, 10, 23, 10, 15, tzinfo=timezone.utc), "created_by": "owner",
        "is_active": is_active,
    }


class Recorder:
    def __init__(self, monkeypatch, *, rows=None):
        self.rows = rows if rows is not None else {7: _release()}
        self.published = []
        self.rolled_back = []
        monkeypatch.setattr(store, "release_row", lambda conn, rid: self.rows.get(rid))
        monkeypatch.setattr(store, "publish", lambda conn, rid, actor: self.published.append((rid, actor)))
        monkeypatch.setattr(store, "rollback", lambda conn, actor: self.rolled_back.append(actor) or 3)


@pytest.fixture
def rec(monkeypatch):
    return Recorder(monkeypatch)


# --- publish ------------------------------------------------------------------------------------


def test_publish_refuses_a_fake_model_release_unless_allowed(rec):
    with pytest.raises(CommandError, match="--allow-fake") as exc:
        commands.publish(object(), 7, "alice", False)
    assert "fake-sha256-v1" in str(exc.value) and rec.published == []


def test_publish_allows_a_fake_model_release_with_the_flag(rec):
    commands.publish(object(), 7, "alice", True)
    assert rec.published == [(7, "alice")]


def test_publish_does_not_need_the_flag_for_the_real_model(monkeypatch):
    rec = Recorder(monkeypatch, rows={7: _release(model=REAL_MODEL)})
    commands.publish(object(), 7, "alice", False)
    assert rec.published == [(7, "alice")]


def test_publish_of_a_missing_release_is_a_command_error(rec):
    with pytest.raises(CommandError, match="release 99 does not exist"):
        commands.publish(object(), 99, "alice", True)
    assert rec.published == []


def test_publish_leaves_every_other_refusal_to_the_database(monkeypatch):
    rec = Recorder(monkeypatch, rows={7: _release(model=REAL_MODEL, status="rejected")})

    def refuse(conn, rid, actor):
        raise store.StoreError("release 7 is not validated")

    monkeypatch.setattr(store, "publish", refuse)
    with pytest.raises(store.StoreError, match="not validated"):
        commands.publish(object(), 7, "alice", False)
    assert rec.published == []


def test_rollback_returns_the_release_now_active(rec):
    assert commands.rollback(object(), "alice") == 3
    assert rec.rolled_back == ["alice"]


# --- list ---------------------------------------------------------------------------------------


def test_list_releases_is_the_stores_list(monkeypatch):
    rows = [{"release_id": 1}, {"release_id": 2}]
    monkeypatch.setattr(store, "list_releases", lambda conn: rows)
    assert commands.list_releases(object()) == rows


def test_format_release_list_has_a_header_and_marks_the_active_release():
    rows = [
        {"release_id": 1, "status": "rejected", "source_commit": COMMIT, "embedding_model": "fake-sha256-v1",
         "created_at": datetime(2026, 10, 23, 10, 15, tzinfo=timezone.utc), "is_active": False},
        {"release_id": 12, "status": "validated", "source_commit": COMMIT, "embedding_model": REAL_MODEL,
         "created_at": datetime(2026, 10, 24, 1, 5, tzinfo=timezone.utc), "is_active": True},
    ]
    lines = commands.format_release_list(rows).splitlines()
    assert lines[0].split()[:4] == ["ID", "STATUS", "ACTIVE", "COMMIT"]
    assert lines[1].split()[:4] == ["1", "rejected", "-", COMMIT[:12]]
    assert lines[2].split()[:4] == ["12", "validated", "yes", COMMIT[:12]]
    assert "2026-10-23 10:15" in lines[1] and "2026-10-24 01:05" in lines[2]
    assert REAL_MODEL in lines[2]


def test_format_release_list_of_nothing_says_so():
    assert commands.format_release_list([]) == "no releases yet"


# --- show ---------------------------------------------------------------------------------------


def _patch_members(monkeypatch, members, active):
    monkeypatch.setattr(store, "member_hashes", lambda conn, rid: members[rid])
    monkeypatch.setattr(store, "active_release_id", lambda conn: active)
    monkeypatch.setattr(store, "live_services", lambda conn, rid: ["alpha", "beta"])


def test_show_drops_the_large_blobs_and_keeps_the_gate_report(monkeypatch):
    Recorder(monkeypatch)
    _patch_members(monkeypatch, {7: {"r1": "h1"}, 5: {"r1": "h1"}}, active=5)
    shown = commands.show(object(), 7)
    assert "manifest" not in shown and "registry" not in shown
    assert shown["gate_report"] == {"passed": True} and shown["registry_sha256"] == "f" * 64
    assert shown["release_id"] == 7 and shown["status"] == "validated" and shown["thresholds"] == {"known_failures": {}}
    assert shown["records"] == 1 and shown["live_services"] == ["alpha", "beta"]
    json.dumps(shown, default=str)


def test_show_diffs_against_the_active_release_by_record_id_and_hash(monkeypatch):
    Recorder(monkeypatch)
    _patch_members(
        monkeypatch,
        {
            7: {"keep": "h", "edited": "new", "added-b": "x", "added-a": "y"},
            5: {"keep": "h", "edited": "old", "gone-2": "z", "gone-1": "z"},
        },
        active=5,
    )
    shown = commands.show(object(), 7)
    assert shown["active_release_id"] == 5
    assert shown["diff_vs_active"] == {
        "added": ["added-a", "added-b"], "removed": ["gone-1", "gone-2"], "changed": ["edited"]}


def test_show_of_the_active_release_has_an_empty_diff(monkeypatch):
    Recorder(monkeypatch, rows={7: _release(is_active=True)})
    _patch_members(monkeypatch, {7: {"a": "1", "b": "2"}}, active=7)
    assert commands.show(object(), 7)["diff_vs_active"] == {"added": [], "removed": [], "changed": []}


def test_show_with_nothing_published_yet_lists_everything_as_added(monkeypatch):
    Recorder(monkeypatch)
    _patch_members(monkeypatch, {7: {"b": "2", "a": "1"}}, active=None)
    shown = commands.show(object(), 7)
    assert shown["active_release_id"] is None
    assert shown["diff_vs_active"] == {"added": ["a", "b"], "removed": [], "changed": []}


def test_show_of_a_missing_release_is_a_command_error(rec):
    with pytest.raises(CommandError, match="release 99 does not exist"):
        commands.show(object(), 99)


# --- eval ---------------------------------------------------------------------------------------

GOLD = {
    "top_k": 4,
    "queries": [
        {"id": "gq_ok", "query": "q1", "expected_category": "fees", "expected_id": "alpha-1"},
        {"id": "gq_known", "query": "q2", "expected_category": "fees", "expected_id": "alpha-2"},
        {"id": "gq_bad", "query": "q3", "expected_category": "fees", "expected_id": "alpha-3"},
    ],
}


def _eval_source(*service_ids, **extra):
    """A repository whose services (default: just ``alpha``) each have a corpus and the ``GOLD`` queries."""

    def dump(obj):
        return json.dumps(obj).encode("utf-8")

    service_ids = service_ids or ("alpha",)
    files = {
        "data/release_manifest.json": dump({"services": [{
            "service_id": sid, "name_bn": f"{sid} bn", "name_en": sid.title(), "status": "live",
            "corpus": f"{sid}.jsonl", "gold": f"{sid}_gold.json", "verified_at_field": None}
            for sid in service_ids]}),
        "data/release_thresholds.json": dump({"known_failures": {}}),
        "concept_registry.json": dump({"services": {sid: {} for sid in service_ids}}),
    }
    for sid in service_ids:
        files[f"{sid}.jsonl"] = f'{{"id": "{sid}-1", "text": "t", "source_url": "https://e.test/1"}}\n'.encode("utf-8")
        # alpha keeps the plain ids; another service gets its own, because query ids are unique per release
        suffix = "" if sid == "alpha" else f"_{sid}"
        files[f"{sid}_gold.json"] = dump({**GOLD, "queries": [{**q, "id": q["id"] + suffix} for q in GOLD["queries"]]})
    files.update(extra)
    return DictSource(COMMIT, files)


def _hit(rid, sim=0.9):
    return Hit(rid, "alpha", sim, "t", "fees", None, "h", "https://e.test")


@pytest.fixture
def eval_world(monkeypatch):
    monkeypatch.setitem(evals.RULES, "alpha", evals.RULES["nid"])
    rec = Recorder(monkeypatch, rows={7: _release(known={"gq_known": "documented"})})
    monkeypatch.setattr(store, "live_services", lambda conn, rid: ["alpha"])
    searches = []

    def search(conn, rid, sid, vector, k):
        searches.append((rid, sid, k))
        # the first query finds its record, the other two do not (only alpha-1 is returned)
        return [_hit("alpha-1")]

    monkeypatch.setattr(store, "search", search)
    rec.searches = searches
    return rec


def test_eval_release_runs_the_gold_queries_of_the_release_commit(eval_world):
    result = commands.eval_release(object(), 7, FakeEmbedder(), source=_eval_source())
    assert result["release_id"] == 7 and result["source_commit"] == COMMIT
    assert result["embedding_model"] == "fake-sha256-v1"
    assert result["counts"] == {"alpha": {"PASS": 1, "PASS (warn)": 0, "OBSERVE": 0, "FAIL": 2}}
    assert result["unexpected_failures"] == ["gq_bad"] and result["known_failures_seen"] == ["gq_known"]
    assert result["passed"] is False
    assert [v["query_id"] for v in result["verdicts"]] == ["gq_ok", "gq_known", "gq_bad"]
    assert eval_world.searches == [(7, "alpha", 4)] * 3
    json.dumps(result)


def test_eval_release_follows_the_frozen_manifest_order_not_the_alphabet(monkeypatch, eval_world):
    monkeypatch.setitem(evals.RULES, "zeta", evals.RULES["nid"])
    eval_world.rows[7] = _release(services=("zeta", "alpha"))          # the build ran zeta first ...
    monkeypatch.setattr(store, "live_services", lambda conn, rid: ["alpha", "zeta"])  # ... the store sorts
    monkeypatch.setattr(store, "search", lambda conn, rid, sid, vector, k: [Hit("x", sid, 0.9, "t", "fees", None, "h", "u")])
    result = commands.eval_release(object(), 7, FakeEmbedder(), source=_eval_source("alpha", "zeta"))
    assert list(result["counts"]) == ["zeta", "alpha"]
    assert [v["service_id"] for v in result["verdicts"]] == ["zeta"] * 3 + ["alpha"] * 3


def test_eval_release_runs_only_the_services_that_are_live_in_the_release(monkeypatch, eval_world):
    monkeypatch.setitem(evals.RULES, "zeta", evals.RULES["nid"])
    eval_world.rows[7] = _release(services=("zeta", "alpha"))
    monkeypatch.setattr(store, "live_services", lambda conn, rid: ["alpha"])    # zeta is hidden in this release
    result = commands.eval_release(object(), 7, FakeEmbedder(), source=_eval_source("alpha", "zeta"))
    assert list(result["counts"]) == ["alpha"]


def test_eval_release_passes_when_every_failure_is_known(monkeypatch, eval_world):
    eval_world.rows[7] = _release(known={"gq_known": "a", "gq_bad": "b"})
    assert commands.eval_release(object(), 7, FakeEmbedder(), source=_eval_source())["passed"] is True


def test_eval_release_refuses_an_embedder_other_than_the_one_that_built_the_release(monkeypatch, eval_world):
    eval_world.rows[7] = _release(model=REAL_MODEL)
    with pytest.raises(CommandError, match="fake-sha256-v1") as exc:
        commands.eval_release(object(), 7, FakeEmbedder(), source=_eval_source())
    assert REAL_MODEL in str(exc.value) and eval_world.searches == []


def test_the_model_check_comes_before_the_repository_is_opened(monkeypatch, eval_world):
    eval_world.rows[7] = _release(model=REAL_MODEL)

    def boom(*_a, **_k):
        raise AssertionError("the repository must not be opened for a model mismatch")

    monkeypatch.setattr(commands, "GitSource", boom)
    with pytest.raises(CommandError, match="built with"):
        commands.eval_release(object(), 7, FakeEmbedder())


def test_eval_release_of_a_missing_release_is_a_command_error(eval_world):
    with pytest.raises(CommandError, match="release 99 does not exist"):
        commands.eval_release(object(), 99, FakeEmbedder(), source=_eval_source())


def test_eval_release_reports_unreadable_inputs_as_a_command_error(eval_world):
    source = _eval_source()
    del source._files["alpha_gold.json"]
    with pytest.raises(CommandError, match="alpha_gold.json"):
        commands.eval_release(object(), 7, FakeEmbedder(), source=source)


def test_eval_release_opens_the_release_commit_when_no_source_is_given(monkeypatch, eval_world):
    opened = []

    class Fake(DictSource):
        def __init__(self, git_dir, ref):
            opened.append((git_dir, ref))
            super().__init__(COMMIT, _eval_source()._files)

        def secret_paths_in_history(self):
            return []

    monkeypatch.setattr(commands, "GitSource", Fake)
    monkeypatch.setenv("GSN_REPO_GIT", "/somewhere.git")
    commands.eval_release(object(), 7, FakeEmbedder())
    assert opened == [("/somewhere.git", COMMIT)]


def test_format_eval_summarises_per_service_and_names_the_failures(eval_world):
    text = commands.format_eval(commands.eval_release(object(), 7, FakeEmbedder(), source=_eval_source()))
    lines = text.splitlines()
    assert lines[0].startswith("release 7") and "fake-sha256-v1" in lines[0]
    assert any(line.strip().startswith("alpha:") and "FAIL 2" in line for line in lines)
    assert "gq_bad" in text and "gq_known" in text and "would be rejected" in text.lower()


# --- opening the repository ---------------------------------------------------------------------


class _Repo:
    secrets: list[str] = []

    def __init__(self, git_dir, ref="HEAD"):
        self.git_dir, self.ref = git_dir, ref
        self.commit = "a" * 40

    def secret_paths_in_history(self):
        return list(self.secrets)


def test_open_repository_uses_the_given_ref_and_the_environment_directory(monkeypatch):
    monkeypatch.setattr(commands, "GitSource", _Repo)
    monkeypatch.delenv("GSN_REPO_GIT", raising=False)
    repo = commands.open_repository("main")
    assert (repo.git_dir, repo.ref) == ("/repo.git", "main")
    monkeypatch.setenv("GSN_REPO_GIT", "/other.git")
    assert commands.open_repository("HEAD").git_dir == "/other.git"


def test_open_repository_refuses_a_history_with_secret_paths(monkeypatch):
    class Dirty(_Repo):
        secrets = ["setenv_deepseek.bat", "old/.env"]

    monkeypatch.setattr(commands, "GitSource", Dirty)
    with pytest.raises(CommandError) as exc:
        commands.open_repository("HEAD")
    assert "setenv_deepseek.bat" in str(exc.value) and "old/.env" in str(exc.value)


def test_open_repository_lets_a_git_error_through(monkeypatch):
    def bad(git_dir, ref="HEAD"):
        raise GitSourceError(f"cannot resolve ref {ref!r}")

    monkeypatch.setattr(commands, "GitSource", bad)
    with pytest.raises(GitSourceError, match="no-such"):
        commands.open_repository("no-such")
