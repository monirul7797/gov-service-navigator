"""Tests for tools/m1/export_chroma_baseline.py (the ChromaDB baseline exporter).

The exporter itself runs with the *global* Python (chromadb, sentence-transformers and the legacy
root modules are imported lazily, inside the functions that need them). These host tests run in
``.venv``, which has none of those packages, so they cover only the pure helpers and the freshness
gate; nothing here loads a model or opens a vector store.
"""
import gzip
import json
import os
import platform
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

M1_DIR = Path(__file__).resolve().parents[1] / "m1"
if str(M1_DIR) not in sys.path:
    sys.path.insert(0, str(M1_DIR))

import export_chroma_baseline as exp  # noqa: E402

from gsn.evals import RULES, judge  # noqa: E402
from gsn.hashing import text_hash as gsn_text_hash  # noqa: E402
from gsn.retrieval import Hit  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_FILE = REPO_ROOT / "docs" / "reports" / "m1-parity" / "retrieve_cases.json"
SERVICES = ("nid", "epassport", "ereturn", "bdris")
REAL_LOAD_STORED = exp.load_stored      # main() tests replace exp.load_stored; one test needs the real one


# ------------------------------------------------------------------ hashing (no gsn in the exporter)

@pytest.mark.parametrize("text", ["জন্ম নিবন্ধন সংশোধন", "birth registration correction", "", "মিশ্র mixed ১২৩ 123"])
def test_inline_text_hash_equals_gsn_text_hash(text):
    assert exp.text_hash(text) == gsn_text_hash(text)


def test_exporter_does_not_import_gsn_or_heavy_packages_at_top_level():
    source = (M1_DIR / "export_chroma_baseline.py").read_text(encoding="utf-8")
    top_level_imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    for banned in ("gsn", "chromadb", "sentence_transformers", "torch", "numpy", "answer", "eval_"):
        assert not any(banned in line for line in top_level_imports), (banned, top_level_imports)


# ------------------------------------------------------------------ corpus parsing

def _jsonl(*records, newline="\n"):
    return newline.join(json.dumps(r, ensure_ascii=False) for r in records).encode("utf-8")


def test_parse_corpus_maps_id_to_text():
    raw = _jsonl({"id": "a", "text": "alpha", "x": 1}, {"id": "b", "text": "বাংলা"})
    assert exp.parse_corpus(raw, "data/chunks/x.jsonl") == {"a": "alpha", "b": "বাংলা"}


def test_parse_corpus_tolerates_bom_crlf_and_blank_lines():
    clean = _jsonl({"id": "a", "text": "alpha"}, {"id": "b", "text": "beta"})
    messy = b"\xef\xbb\xbf" + _jsonl({"id": "a", "text": "alpha"}, {"id": "b", "text": "beta"}, newline="\r\n\r\n") + b"\r\n\r\n"
    assert exp.parse_corpus(messy, "p") == exp.parse_corpus(clean, "p")


def test_parse_corpus_keeps_a_record_whose_text_contains_a_unicode_line_separator():
    raw = _jsonl({"id": "a", "text": "one two"})
    assert exp.parse_corpus(raw, "p") == {"a": "one two"}


@pytest.mark.parametrize(
    "records",
    [
        [{"text": "no id"}],
        [{"id": "a"}],
        [{"id": "a", "text": 5}],
        [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}],
    ],
)
def test_parse_corpus_rejects_malformed_records(records):
    with pytest.raises(ValueError):
        exp.parse_corpus(_jsonl(*records), "p")


def test_parse_corpus_rejects_invalid_json_with_line_number():
    with pytest.raises(ValueError, match=r":2:"):
        exp.parse_corpus(b'{"id": "a", "text": "x"}\n{not json}\n', "p")


# ------------------------------------------------------------------ freshness diff

def test_diff_finds_an_added_a_removed_and_a_changed_document():
    stored = {"keep": "same", "gone": "only in chroma", "edit": "old text"}
    committed = {"keep": "same", "new": "only in corpus", "edit": "new text"}
    diff = exp.diff_documents(stored, committed)
    assert diff.added == ["new"]          # in the committed corpus, not (yet) in ChromaDB
    assert diff.removed == ["gone"]       # in ChromaDB, no longer in the corpus
    assert diff.changed == ["edit"]
    assert not diff.is_clean


def test_diff_of_identical_documents_is_clean():
    diff = exp.diff_documents({"a": "x", "b": "y"}, {"b": "y", "a": "x"})
    assert (diff.added, diff.removed, diff.changed) == ([], [], [])
    assert diff.is_clean


def test_diff_lists_are_sorted():
    diff = exp.diff_documents({}, {"c": "1", "a": "1", "b": "1"})
    assert diff.added == ["a", "b", "c"]


def test_a_whitespace_only_change_is_a_change():
    assert exp.diff_documents({"a": "text"}, {"a": "text "}).changed == ["a"]


def test_freshness_problems_names_the_service_and_every_differing_id():
    stored = {"nid": {"a": "1"}, "bdris": {"b": "2", "gone": "3"}}
    committed = {"nid": {"a": "1"}, "bdris": {"b": "CHANGED", "new": "4"}}
    problems = exp.freshness_problems(stored, committed)
    text = "\n".join(problems)
    assert "bdris" in text and "new" in text and "gone" in text and "b" in text
    assert "nid" not in text      # the clean service is not mentioned


def test_freshness_problems_is_empty_when_all_services_match():
    docs = {"nid": {"a": "1"}, "bdris": {"b": "2"}}
    assert exp.freshness_problems(docs, {k: dict(v) for k, v in docs.items()}) == []


def test_freshness_problems_flags_a_service_missing_on_either_side():
    problems = exp.freshness_problems({"nid": {"a": "1"}}, {"nid": {"a": "1"}, "bdris": {"b": "2"}})
    assert any("bdris" in p for p in problems)
    problems = exp.freshness_problems({"nid": {"a": "1"}, "bdris": {"b": "2"}}, {"nid": {"a": "1"}})
    assert any("bdris" in p for p in problems)


# ------------------------------------------------------------------ document / vector maps

def test_document_hashes_map_record_id_to_text_hash():
    hashes = exp.document_hashes({"nid": {"a": "alpha"}, "bdris": {"b": "বাংলা"}})
    assert hashes == {"a": gsn_text_hash("alpha"), "b": gsn_text_hash("বাংলা")}


def test_document_hashes_are_sorted_by_record_id():
    hashes = exp.document_hashes({"nid": {"z": "1", "a": "2"}, "bdris": {"m": "3"}})
    assert list(hashes) == ["a", "m", "z"]


def test_a_record_id_used_by_two_services_is_refused():
    with pytest.raises(ValueError, match="dup"):
        exp.document_hashes({"nid": {"dup": "1"}, "bdris": {"dup": "2"}})
    with pytest.raises(ValueError, match="dup"):
        exp.merge_vectors({"nid": {"dup": [0.1]}, "bdris": {"dup": [0.2]}})


def test_merge_vectors_sorts_by_id_and_keeps_floats():
    merged = exp.merge_vectors({"nid": {"b": [0.5, 0.25]}, "bdris": {"a": [1.0, 0.0]}})
    assert list(merged) == ["a", "b"]
    assert merged["b"] == [0.5, 0.25]


def test_to_float_list_accepts_numpy_like_and_plain_sequences():
    class Arr:
        def tolist(self):
            return [0.5, 0.25]

    assert exp.to_float_list(Arr()) == [0.5, 0.25]
    assert exp.to_float_list((1, 2)) == [1.0, 2.0]


# ------------------------------------------------------------------ query results -> hits

CHROMA_RESULT = {
    "ids": [["a", "b", "c"]],
    "metadatas": [[{"category": "fees", "subcategory": "x"}, {"category": "faq"}, None]],
    "distances": [[0.1, 0.25, 0.3333333333333333]],
}


def test_shape_hits_records_unrounded_sim_and_metadata_in_order():
    hits = exp.shape_hits(CHROMA_RESULT)
    assert [h["id"] for h in hits] == ["a", "b", "c"]
    assert [h["sim"] for h in hits] == [1 - 0.1, 1 - 0.25, 1 - 0.3333333333333333]    # not rounded
    assert hits[0] == {"id": "a", "sim": 1 - 0.1, "category": "fees", "subcategory": "x"}
    assert hits[1]["subcategory"] is None                        # absent -> null
    assert (hits[2]["category"], hits[2]["subcategory"]) == (None, None)    # no metadata at all


def test_shape_hits_of_an_empty_result_is_empty():
    assert exp.shape_hits({"ids": [[]], "metadatas": [[]], "distances": [[]]}) == []


# ------------------------------------------------------------------ cross-check with the legacy output

def test_legacy_top_hits_agree_with_the_recorded_hits():
    hits = exp.shape_hits(CHROMA_RESULT)
    legacy = [("a", "fees", round(1 - 0.1, 3)), ("b", "faq", round(1 - 0.25, 3)), ("c", None, round(1 - 0.3333333333333333, 3))]
    exp.check_against_legacy("nid", "gq_1", hits, legacy)       # no exception


@pytest.mark.parametrize(
    "legacy",
    [
        [("a", "fees", 0.9), ("b", "faq", 0.75)],                                    # one hit short
        [("a", "fees", 0.9), ("zzz", "faq", 0.75), ("c", None, 0.667)],               # other id
        [("a", "fees", 0.8), ("b", "faq", 0.75), ("c", None, 0.667)],                 # other sim
        [("a", "OTHER", 0.9), ("b", "faq", 0.75), ("c", None, 0.667)],                # other category
    ],
)
def test_a_legacy_run_that_disagrees_is_an_error(legacy):
    with pytest.raises(RuntimeError, match="gq_1"):
        exp.check_against_legacy("nid", "gq_1", exp.shape_hits(CHROMA_RESULT), legacy)


def test_legacy_ereturn_hits_are_compared_on_subcategory():
    hits = exp.shape_hits(CHROMA_RESULT)
    legacy = [("a", "x", 0.9), ("b", None, 0.75), ("c", None, 0.667)]
    exp.check_against_legacy("ereturn", "gq_1", hits, legacy)


# ------------------------------------------------------------------ entries and output shape

GQ = {"id": "gq_nid_001", "query": "what documents", "lang": "en", "expected_category": "fees", "expected_id": "a"}


def test_gold_entry_shape():
    legacy = {"verdict": "PASS", "reasons": ["expected record 'a' found"]}
    entry = exp.gold_entry("nid", GQ, 4, exp.shape_hits(CHROMA_RESULT), legacy)
    assert list(entry) == ["service", "id", "gq", "top_k", "top", "legacy_verdict", "legacy_reasons"]
    assert entry["service"] == "nid" and entry["id"] == "gq_nid_001"
    assert entry["gq"] == GQ and entry["top_k"] == 4
    assert entry["legacy_verdict"] == "PASS" and entry["legacy_reasons"] == ["expected record 'a' found"]
    assert [h["id"] for h in entry["top"]] == ["a", "b", "c"]


def test_gold_entry_for_ereturn_records_subcategory_available():
    legacy = {"verdict": "FAIL", "reasons": ["r"]}
    entry = exp.gold_entry("ereturn", GQ, 4, [], legacy, subcategory_available=True)
    assert entry["subcategory_available"] is True
    assert "subcategory_available" not in exp.gold_entry("nid", GQ, 4, [], legacy)


def test_a_gold_entry_replays_through_gsn_judge_to_its_legacy_verdict():
    """The recorded shape is exactly what the replay test needs to rebuild ``Hit`` objects."""
    chroma = {
        "ids": [["a", "b"]],
        "metadatas": [[{"category": "fees"}, {"category": "faq"}]],
        "distances": [[0.1, 0.2]],
    }
    entry = exp.gold_entry("nid", GQ, 4, exp.shape_hits(chroma), {"verdict": "PASS", "reasons": []})
    hits = [
        Hit(h["id"], entry["service"], h["sim"], "", h["category"], h["subcategory"], None, "")
        for h in entry["top"]
    ]
    assert judge(RULES["nid"], entry["gq"], hits, entry["top_k"], service_id="nid").verdict == entry["legacy_verdict"]


def test_retrieve_entry_shape_and_selected_hits():
    selected = [
        {"service": "nid", "id": "a", "sim": 0.8123456789, "text": "t", "category": "c", "heading": "h", "source_url": "u"},
        {"service": "bdris", "id": "b", "sim": 0.5, "text": "t2", "category": "c", "heading": "h", "source_url": "u"},
    ]
    entry = exp.retrieve_entry("rc_01", "case", ["q1", "q2"], ["nid", "bdris"], selected)
    assert list(entry) == ["id", "source", "queries", "services", "hits"]
    assert entry["services"] == ["nid", "bdris"] and entry["queries"] == ["q1", "q2"]
    assert entry["hits"] == [
        {"id": "a", "service": "nid", "sim": 0.8123456789},
        {"id": "b", "service": "bdris", "sim": 0.5},
    ]
    assert exp.retrieve_entry("x", "gold", ["q"], None, [])["services"] is None


def test_assemble_baseline_has_exactly_the_documented_keys():
    baseline = exp.assemble_baseline("c" * 40, {"torch": "1"}, {"a": "h"}, [{"id": "g"}], [{"id": "r"}])
    assert list(baseline) == ["commit", "environment", "documents", "gold", "retrieve"]
    assert baseline["commit"] == "c" * 40 and baseline["documents"] == {"a": "h"}


def test_write_outputs_writes_utf8_json_and_gzipped_vectors(tmp_path):
    baseline = exp.assemble_baseline("c" * 40, {}, {"a": "h"}, [{"id": "জন্ম"}], [])
    vectors = {"a": [0.25, -0.5], "b": [1.0, 0.0]}
    exp.write_outputs(tmp_path / "out", baseline, vectors)
    raw = (tmp_path / "out" / "chroma_baseline.json").read_bytes()
    assert "জন্ম".encode("utf-8") in raw                       # ensure_ascii=False
    assert b"\r\n" not in raw and raw.endswith(b"\n")
    assert raw.startswith(b'{\n "commit"')                     # indent=1
    assert json.loads(raw) == baseline
    with gzip.open(tmp_path / "out" / "chroma_vectors.json.gz", "rt", encoding="utf-8") as f:
        assert json.load(f) == vectors
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["chroma_baseline.json", "chroma_vectors.json.gz"]


def test_the_vectors_file_is_byte_for_byte_reproducible(tmp_path):
    vectors = {"a": [0.1, 0.2]}
    baseline = exp.assemble_baseline("c" * 40, {}, {}, [], [])
    exp.write_outputs(tmp_path / "one", baseline, vectors)
    exp.write_outputs(tmp_path / "two", baseline, vectors)
    assert (tmp_path / "one" / "chroma_vectors.json.gz").read_bytes() == (tmp_path / "two" / "chroma_vectors.json.gz").read_bytes()


# ------------------------------------------------------------------ the model snapshot

class _Cfg:
    def __init__(self, name_or_path, commit_hash=None):
        self._name_or_path = name_or_path
        self._commit_hash = commit_hash


class _Auto:
    def __init__(self, cfg):
        self.config = cfg


class _FirstModule:
    def __init__(self, cfg):
        self.auto_model = _Auto(cfg)


class _FakeModel:
    device = "cpu"

    def __init__(self, cfg):
        self._first = _FirstModule(cfg)

    def __getitem__(self, index):
        assert index == 0
        return self._first


def test_snapshot_dir_is_the_local_path_the_model_was_loaded_from(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    assert exp.model_snapshot_dir(_FakeModel(_Cfg(str(snap)))) == snap


def test_snapshot_dir_of_a_hub_model_is_resolved_through_the_cache_with_the_loaded_revision(tmp_path):
    snap = tmp_path / "snapshots" / ("a" * 40)
    snap.mkdir(parents=True)
    calls = []

    def resolver(repo_id, revision):
        calls.append((repo_id, revision))
        return str(snap)

    model = _FakeModel(_Cfg("BAAI/bge-m3", "a" * 40))
    assert exp.model_snapshot_dir(model, resolver=resolver) == snap
    assert calls == [("BAAI/bge-m3", "a" * 40)]


def test_snapshot_dir_refuses_to_guess_without_a_revision():
    with pytest.raises(RuntimeError, match="revision"):
        exp.model_snapshot_dir(_FakeModel(_Cfg("BAAI/bge-m3", None)), resolver=lambda repo, rev: "unused")


def test_weight_file_is_the_pytorch_bin_when_that_is_all_the_snapshot_holds(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "pytorch_model.bin").write_bytes(b"123")
    assert exp.find_weight_file(tmp_path) == tmp_path / "pytorch_model.bin"


def test_weight_file_prefers_safetensors_as_transformers_does(tmp_path):
    (tmp_path / "pytorch_model.bin").write_bytes(b"1")
    (tmp_path / "model.safetensors").write_bytes(b"1")
    assert exp.find_weight_file(tmp_path) == tmp_path / "model.safetensors"


def test_weight_file_missing_is_an_error(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    with pytest.raises(RuntimeError, match="weight"):
        exp.find_weight_file(tmp_path)


def test_package_versions_use_the_given_lookup():
    seen = {"torch": "2.0", "numpy": "9.9"}
    assert exp.package_versions(("torch", "numpy"), lookup=seen.__getitem__) == {"torch": "2.0", "numpy": "9.9"}


# ------------------------------------------------------------------ model revision and public paths

REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


def _is_absolute(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def test_model_revision_is_the_loaded_configs_commit_hash():
    model = _FakeModel(_Cfg("BAAI/bge-m3", REVISION))
    assert exp.model_revision(model, Path("/somewhere/else")) == REVISION


def test_model_revision_falls_back_to_the_snapshot_directory_name():
    model = _FakeModel(_Cfg("/local/path/with/no/hash", None))
    assert exp.model_revision(model, Path("/hub/models--BAAI--bge-m3/snapshots") / REVISION) == REVISION


@pytest.mark.parametrize(
    "path, expected",
    [
        (
            "C:/Users/someone/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/abc/pytorch_model.bin",
            "models--BAAI--bge-m3/snapshots/abc/pytorch_model.bin",
        ),
        ("/home/someone/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/abc", "models--BAAI--bge-m3/snapshots/abc"),
        ("/srv/models/bge-m3-copy", "bge-m3-copy"),        # not in a Hugging Face cache: the last name only
    ],
)
def test_cache_relative_keeps_the_path_from_the_hub_cache_entry_and_drops_the_home(path, expected):
    assert exp.cache_relative(Path(path)) == expected


def _hub_snapshot(tmp_path):
    snap = tmp_path / "home" / "someone" / ".cache" / "huggingface" / "hub" / "models--BAAI--bge-m3" / "snapshots" / REVISION
    snap.mkdir(parents=True)
    (snap / "pytorch_model.bin").write_bytes(b"12345")
    return snap


def _collect(snap, name="BAAI/bge-m3"):
    model = _FakeModel(_Cfg(name, REVISION))
    return exp.collect_environment(model, "c" * 40, lookup=lambda package: "1.2.3", resolver=lambda repo, rev: str(snap))


def test_the_environment_records_the_revision_and_cache_relative_model_files(tmp_path):
    env = _collect(_hub_snapshot(tmp_path))
    assert env["model_revision"] == REVISION
    assert env["model_snapshot_dir"] == f"models--BAAI--bge-m3/snapshots/{REVISION}"
    assert env["model_weight_file"] == f"models--BAAI--bge-m3/snapshots/{REVISION}/pytorch_model.bin"
    assert env["model_weight_file_bytes"] == 5
    assert env["model_name"] == "BAAI/bge-m3"
    assert env["git_head"] == "c" * 40
    assert env["torch"] == "1.2.3" and env["numpy"] == "1.2.3"


def test_no_environment_value_is_an_absolute_path_or_leaks_the_home_directory(tmp_path):
    env = _collect(_hub_snapshot(tmp_path))
    for key, value in env.items():
        if isinstance(value, str):
            assert not _is_absolute(value), (key, value)
    dumped = json.dumps(env)
    assert str(tmp_path) not in dumped and tmp_path.as_posix() not in dumped
    assert str(Path.home()) not in dumped and "someone" not in dumped


def test_a_model_loaded_from_an_absolute_local_path_is_recorded_by_its_name_only(tmp_path):
    snap = tmp_path / "my_models" / "bge-m3-local"
    snap.mkdir(parents=True)
    (snap / "model.safetensors").write_bytes(b"1")
    model = _FakeModel(_Cfg(str(snap), None))
    env = exp.collect_environment(model, "c" * 40, lookup=lambda package: "1")
    assert env["model_name"] == "bge-m3-local"
    assert env["model_snapshot_dir"] == "bge-m3-local"
    assert env["model_weight_file"] == "bge-m3-local/model.safetensors"
    assert env["model_revision"] == "bge-m3-local"          # the snapshot directory name, as the fallback says
    assert str(tmp_path) not in json.dumps(env)


def test_platform_is_the_os_name_and_version_without_the_host_name():
    description = exp.os_description()
    assert platform.system() in description
    node = platform.node()
    if node:
        assert node not in description


# ------------------------------------------------------------------ retrieve cases

def test_the_committed_cases_file_has_twelve_cases_four_each_over_two_three_and_four_services():
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    assert [c["id"] for c in cases] == [f"rc_{n:02d}" for n in range(1, 13)]
    by_width = {2: 0, 3: 0, 4: 0}
    for case in cases:
        assert list(case) == ["id", "queries", "services"]
        assert 2 <= len(case["queries"]) <= 3
        assert len(case["services"]) == len(set(case["services"]))
        assert set(case["services"]) <= set(SERVICES)
        by_width[len(case["services"])] += 1
    assert by_width == {2: 4, 3: 4, 4: 4}


# validate_cases is the export-time guard that every case query is a verbatim gold query. It is tested
# with a small synthetic gold fixture: the live gold files may change after the baseline is committed.
SYNTHETIC_GOLD = {"nid": ["q1", "q2"], "bdris": ["q3"]}


def test_validate_cases_accepts_well_formed_cases_with_queries_from_any_service():
    cases = [
        {"id": "rc_01", "queries": ["q1", "q3"], "services": ["nid", "bdris"]},
        {"id": "rc_02", "queries": ["q1", "q2", "q3"], "services": ["bdris"]},
    ]
    exp.validate_cases(cases, SYNTHETIC_GOLD)       # no exception


@pytest.mark.parametrize(
    "case",
    [
        {"id": "rc_x", "queries": ["only one"], "services": ["nid", "bdris"]},
        {"id": "rc_x", "queries": ["q1", "q2", "q3", "q4"], "services": ["nid", "bdris"]},
        {"id": "rc_x", "queries": ["q1", "not a gold query"], "services": ["nid", "bdris"]},
        {"id": "rc_x", "queries": ["q1", "q2"], "services": ["nid", "visa"]},
        {"id": "rc_x", "queries": ["q1", "q2"], "services": []},
        {"id": "rc_x", "queries": ["q1", "q2"]},
    ],
)
def test_validate_cases_rejects_bad_cases(case):
    with pytest.raises(ValueError):
        exp.validate_cases([case], SYNTHETIC_GOLD)


def test_validate_cases_rejects_duplicate_ids():
    gold = {"nid": ["q1", "q2"]}
    case = {"id": "rc_01", "queries": ["q1", "q2"], "services": ["nid"]}
    with pytest.raises(ValueError, match="rc_01"):
        exp.validate_cases([case, dict(case)], gold)


# ------------------------------------------------------------------ the vector store must already exist

def test_a_missing_vector_store_is_refused_and_not_created(tmp_path):
    missing = tmp_path / "data" / "vectordb"
    with pytest.raises(exp.ExportRefused, match="embed_"):
        exp.require_vector_store(missing)
    assert not missing.exists() and not missing.parent.exists()


def test_a_vector_store_path_that_is_a_file_is_refused(tmp_path):
    (tmp_path / "vectordb").write_text("not a directory")
    with pytest.raises(exp.ExportRefused):
        exp.require_vector_store(tmp_path / "vectordb")


def test_an_existing_vector_store_is_accepted(tmp_path):
    (tmp_path / "vectordb").mkdir()
    exp.require_vector_store(tmp_path / "vectordb")


def test_load_stored_checks_the_directory_before_importing_or_creating_anything(tmp_path):
    """chromadb.PersistentClient would create the directory; only the embed_*.py scripts may do that."""
    with pytest.raises(exp.ExportRefused, match="vectordb"):
        exp.load_stored(tmp_path)
    assert not (tmp_path / "data").exists()


# ------------------------------------------------------------------ the pinned inputs must equal HEAD

PINNED = (
    "answer.py",
    "eval_nid.py",
    "eval_epassport.py",
    "eval_ereturn.py",
    "eval_bdris.py",
    "nid_gold_queries.json",
    "epassport_gold_queries.json",
    "ereturn_gold_queries.json",
    "bdris_gold_queries.json",
    "docs/reports/m1-parity/retrieve_cases.json",
)


def test_the_pinned_inputs_are_the_legacy_code_the_gold_files_and_the_cases_file():
    assert exp.PINNED_INPUTS == PINNED


def test_dirty_inputs_asks_git_status_about_exactly_the_pinned_paths(monkeypatch, tmp_path):
    calls = []

    def fake_git(root, *args):
        calls.append((root, args))
        return b" M answer.py\n?? docs/reports/m1-parity/retrieve_cases.json\nR  old.json -> nid_gold_queries.json\n"

    monkeypatch.setattr(exp, "_git", fake_git)
    assert exp.dirty_inputs(tmp_path) == [
        "answer.py",
        "docs/reports/m1-parity/retrieve_cases.json",
        "nid_gold_queries.json",
    ]
    assert calls == [(tmp_path, ("status", "--porcelain", "--", *PINNED))]


def test_dirty_inputs_is_empty_when_git_reports_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(exp, "_git", lambda root, *args: b"")
    assert exp.dirty_inputs(tmp_path) == []


def test_git_runs_as_an_argv_list_without_a_shell_and_without_git_environment_variables(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"], seen["kwargs"] = args, kwargs
        return subprocess.CompletedProcess(args, 0, stdout=b"ok", stderr=b"")

    monkeypatch.setattr(exp.subprocess, "run", fake_run)
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.setenv(name, "/elsewhere")
    assert exp._git(tmp_path, "status", "--porcelain") == b"ok"
    assert seen["args"] == ["git", "-C", str(tmp_path), "status", "--porcelain"]
    assert not seen["kwargs"].get("shell")
    assert not [k for k in seen["kwargs"]["env"] if k.startswith("GIT_")]


def _plain_git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    config = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]
    subprocess.run(["git", *config, *args], cwd=cwd, env=env, check=True, capture_output=True)


def test_dirty_inputs_in_a_real_repository_ignores_unrelated_files(tmp_path):
    for name in (*PINNED, "acquisition/acquire.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    _plain_git(tmp_path, "init", "-q")
    _plain_git(tmp_path, "add", *PINNED, "acquisition/acquire.py")
    _plain_git(tmp_path, "commit", "-q", "-m", "inputs")
    assert exp.dirty_inputs(tmp_path) == []

    # the owner's other uncommitted edits and the untracked review bundles must not trigger the refusal
    (tmp_path / "acquisition" / "acquire.py").write_text("changed\n", encoding="utf-8")
    (tmp_path / "freshness_v21_final_review").mkdir()
    (tmp_path / "freshness_v21_final_review" / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "phase3_6_visa_review_bundle.zip").write_bytes(b"zip")
    assert exp.dirty_inputs(tmp_path) == []

    # but an edit to a pinned file does, and so does a pinned file that was deleted
    (tmp_path / "nid_gold_queries.json").write_text('{"changed": true}\n', encoding="utf-8")
    (tmp_path / "answer.py").unlink()
    assert sorted(exp.dirty_inputs(tmp_path)) == ["answer.py", "nid_gold_queries.json"]


# ------------------------------------------------------------------ the gates in main()

def _fake_run(monkeypatch, tmp_path, stored_docs, committed_docs, *, dirty=(), vector_store=True):
    """Replace everything main() touches outside the repo; returns the output directory."""
    root = tmp_path / "repo"
    root.mkdir()
    if vector_store:
        (root / "data" / "vectordb").mkdir(parents=True)
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"id": "rc_01", "queries": ["q1", "q2"], "services": ["nid"]}]), encoding="utf-8")
    synthetic_gold = {s: {"queries": [{"id": "g1", "query": "q1"}, {"id": "g2", "query": "q2"}]} for s in SERVICES}
    monkeypatch.setattr(exp, "REPO_ROOT", root)
    monkeypatch.setattr(exp, "CASES_FILE", cases)
    monkeypatch.setattr(exp, "load_gold_files", lambda r: synthetic_gold)
    monkeypatch.setattr(exp, "git_head", lambda r: "d" * 40)
    monkeypatch.setattr(exp, "dirty_inputs", lambda r: list(dirty))
    monkeypatch.setattr(exp, "read_committed_corpora", lambda r, commit: committed_docs)
    monkeypatch.setattr(exp, "load_stored", lambda r: exp.Stored(colls={}, documents=stored_docs, vectors={}))

    def must_not_run(*args, **kwargs):
        raise AssertionError("the model must not be loaded when a gate fails")

    monkeypatch.setattr(exp, "load_model", must_not_run)
    return tmp_path / "out"


def test_main_prints_the_differing_ids_and_exits_1_before_writing_anything(monkeypatch, tmp_path, capsys):
    out = _fake_run(
        monkeypatch,
        tmp_path,
        stored_docs={"nid": {"same": "1", "gone": "2", "edit": "old"}},
        committed_docs={"nid": {"same": "1", "new": "3", "edit": "new"}},
    )
    assert exp.main(["--out", str(out)]) == 1
    printed = capsys.readouterr().out
    for record_id in ("gone", "new", "edit"):
        assert record_id in printed
    assert not out.exists()


def test_main_with_matching_documents_gets_past_the_gates(monkeypatch, tmp_path):
    out = _fake_run(monkeypatch, tmp_path, stored_docs={"nid": {"a": "1"}}, committed_docs={"nid": {"a": "1"}})
    with pytest.raises(AssertionError, match="model must not be loaded"):
        exp.main(["--out", str(out)])      # reached load_model, i.e. every gate passed


def test_main_refuses_with_exit_1_and_lists_the_paths_when_a_pinned_input_differs_from_head(monkeypatch, tmp_path, capsys):
    out = _fake_run(
        monkeypatch,
        tmp_path,
        stored_docs={"nid": {"a": "1"}},
        committed_docs={"nid": {"a": "1"}},
        dirty=["nid_gold_queries.json", "answer.py"],
    )

    def must_not_read(*args, **kwargs):
        raise AssertionError("nothing may be read once an input is known to differ from HEAD")

    monkeypatch.setattr(exp, "read_committed_corpora", must_not_read)
    monkeypatch.setattr(exp, "load_stored", must_not_read)
    assert exp.main(["--out", str(out)]) == 1
    printed = capsys.readouterr().out
    assert "nid_gold_queries.json" in printed and "answer.py" in printed
    assert not out.exists()


def test_main_refuses_with_exit_1_when_the_vector_store_does_not_exist(monkeypatch, tmp_path, capsys):
    out = _fake_run(monkeypatch, tmp_path, stored_docs={}, committed_docs={}, vector_store=False)
    monkeypatch.setattr(exp, "load_stored", REAL_LOAD_STORED)      # its directory check precedes any import
    assert exp.main(["--out", str(out)]) == 1
    printed = capsys.readouterr().out
    assert "vectordb" in printed and "embed_" in printed
    assert not (tmp_path / "repo" / "data").exists()
    assert not out.exists()
