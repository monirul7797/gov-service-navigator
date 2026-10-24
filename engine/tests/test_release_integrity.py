import copy
import json
from datetime import date
from pathlib import Path

import pytest

from gsn.gitsource import DictSource, GitSource
from gsn.release.inputs import (
    MANIFEST_PATH,
    REGISTRY_PATH,
    THRESHOLDS_PATH,
    ReleaseInputs,
    load_inputs,
)
from gsn.release.integrity import check_integrity

REPO = Path(__file__).resolve().parents[2]
COMMIT = "0123456789abcdef0123456789abcdef01234567"
TODAY = date(2026, 10, 23)

NID_NAMES = ("জাতীয় পরিচয়পত্র (NID)", "National ID (NID)")
BDRIS_NAMES = ("জন্ম ও মৃত্যু নিবন্ধন", "Birth and death registration")


def _state() -> dict:
    """A clean two-service release as plain Python objects; each test breaks one thing."""
    return {
        "manifest": {
            "services": [
                {
                    "service_id": "nid",
                    "name_bn": NID_NAMES[0],
                    "name_en": NID_NAMES[1],
                    "status": "live",
                    "corpus": "data/chunks/nid.jsonl",
                    "gold": "nid_gold_queries.json",
                    "verified_at_field": "fetched_at",
                },
                {
                    "service_id": "bdris",
                    "name_bn": BDRIS_NAMES[0],
                    "name_en": BDRIS_NAMES[1],
                    "status": "live",
                    "corpus": "data/chunks/bdris.jsonl",
                    "gold": "bdris_gold_queries.json",
                    "verified_at_field": None,
                },
            ]
        },
        "thresholds": {"known_failures": {}},
        "registry": {"_readme": "t", "services": {"nid": {}, "bdris": {}}, "concepts": {}},
        "corpora": {
            "nid": [
                {
                    "id": "nid-1",
                    "text": "ভোটার তালিকা হালনাগাদ",
                    "heading": "ভোটার",
                    "category": "enrollment",
                    "lang": "bn",
                    "source_url": "https://example.test/nid/1",
                    "official": True,
                    "fetched_at": "2026-07-12T23:04:19",
                },
                {
                    "id": "nid-2",
                    "text": "How to correct a voter record",
                    "heading": "Correction",
                    "category": "correction",
                    "lang": "en",
                    "source_url": "https://example.test/nid/2",
                    "official": True,
                    "fetched_at": "2026-07-12T23:04:20",
                },
            ],
            "bdris": [
                {
                    "id": "bdris-1",
                    "text": "জন্ম নিবন্ধন birth registration",
                    "heading": "Birth",
                    "category": "registration",
                    "subcategory": "birth",
                    "lang": "bn+en",
                    "source_url": "https://example.test/bdris/1",
                    "official": True,
                }
            ],
        },
        "gold": {
            "nid": {
                "top_k": 4,
                "queries": [
                    {"id": "gq_nid_001", "query": "voter list", "lang": "en", "expected_category": "enrollment", "expected_id": "nid-1"},
                    {"id": "gq_nid_002", "query": "weather", "lang": "en", "expected_category": None, "expected_id": None},
                    {"id": "gq_nid_003", "query": "verify", "lang": "en", "expected_category": "correction"},
                ],
            },
            "bdris": {
                "top_k": 4,
                "queries": [
                    {"id": "gq_001", "query": "birth", "lang": "en", "expected_category": "registration", "expected_id": "bdris-1"}
                ],
            },
        },
    }


def _inputs(state: dict) -> ReleaseInputs:
    def dump(obj) -> bytes:
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")

    files = {
        MANIFEST_PATH: dump(state["manifest"]),
        THRESHOLDS_PATH: dump(state["thresholds"]),
        REGISTRY_PATH: dump(state["registry"]),
    }
    for entry in state["manifest"]["services"]:
        sid = entry["service_id"]
        files[entry["corpus"]] = "".join(dump(r).decode("utf-8") + "\n" for r in state["corpora"][sid]).encode("utf-8")
        files[entry["gold"]] = dump(state["gold"][sid])
    return load_inputs(DictSource(COMMIT, files))


def _check(state: dict, stored_records=None, stored_names=None, today: date = TODAY) -> list[str]:
    return check_integrity(_inputs(state), stored_records or {}, stored_names or {}, today)


def _only(problems: list[str], *mentions: str) -> str:
    assert len(problems) == 1, problems
    for fragment in mentions:
        assert fragment in problems[0], (fragment, problems[0])
    return problems[0]


def test_clean_inputs_have_no_problems():
    assert _check(_state()) == []


def test_clean_inputs_that_match_the_stored_data_have_no_problems():
    stored_records = {"nid-1": "nid", "nid-2": "nid", "bdris-1": "bdris", "retired-record": "nid"}
    stored_names = {"nid": NID_NAMES, "bdris": BDRIS_NAMES, "retired": ("x", "y")}
    assert _check(_state(), stored_records, stored_names) == []


def test_live_service_without_registry_entry():
    state = _state()
    del state["registry"]["services"]["bdris"]
    _only(_check(state), "bdris", "registry")


def test_hidden_service_without_registry_entry_is_allowed():
    state = _state()
    del state["registry"]["services"]["bdris"]
    state["manifest"]["services"][1]["status"] = "hidden"
    assert _check(state) == []


@pytest.mark.parametrize(
    "change, field",
    [
        (lambda r: r.pop("text"), "text"),
        (lambda r: r.update(text=""), "text"),
        (lambda r: r.update(text="  \n\t"), "text"),
        (lambda r: r.update(text=5), "text"),
        (lambda r: r.pop("source_url"), "source_url"),
        (lambda r: r.update(source_url=""), "source_url"),
        (lambda r: r.update(source_url=None), "source_url"),
    ],
)
def test_record_missing_text_or_source_url(change, field):
    state = _state()
    change(state["corpora"]["nid"][1])
    _only(_check(state), "nid-2", field)


@pytest.mark.parametrize("bad", ["<absent>", "", "   ", 5, None, ["nid-2"]])
def test_record_missing_id_or_id_that_is_not_a_non_empty_string(bad):
    state = _state()
    record = state["corpora"]["nid"][1]
    if bad == "<absent>":
        del record["id"]
    else:
        record["id"] = bad
    _only(_check(state), "nid", "record #2", "id")


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(text=r["text"] + " TODO(verify)"),
        lambda r: r.update(edit_note="TODO(verify): fee"),
        lambda r: r.update(bangla_issues=["fine", "TODO(verify)"]),
        lambda r: r.update(extra={"deep": [{"note": "see TODO(verify)"}]}),
        lambda r: r.update(heading="todo(verify)"),
        lambda r: r.update({"note TODO(verify)": "x"}),
    ],
)
def test_todo_verify_anywhere_in_record(change):
    state = _state()
    change(state["corpora"]["nid"][1])
    _only(_check(state), "nid-2", "TODO(verify)")


def test_todo_verify_lists_every_place_in_one_problem_per_record():
    state = _state()
    state["corpora"]["nid"][1].update(heading="TODO(verify)", extra=["a", "TODO(verify)"])
    problem = _only(_check(state), "nid-2")
    assert "heading" in problem and "extra[1]" in problem


def test_duplicate_record_ids_across_services():
    state = _state()
    state["corpora"]["bdris"].append({**state["corpora"]["bdris"][0], "id": "nid-2", "text": "ভিন্ন bdris text"})
    _only(_check(state), "nid-2", "nid", "bdris")


def test_duplicate_record_ids_within_a_service():
    state = _state()
    state["corpora"]["nid"][1]["id"] = "nid-1"
    state["corpora"]["nid"][1]["text"] = "another text"
    # nid-2 is gone, but no gold query expects it
    _only(_check(state), "nid-1", "duplicate")


def test_duplicate_texts():
    state = _state()
    state["corpora"]["nid"][1]["text"] = state["corpora"]["nid"][0]["text"]
    _only(_check(state), "nid-1", "nid-2", "identical text")


def test_texts_that_only_differ_are_not_duplicates():
    state = _state()
    state["corpora"]["nid"][1]["text"] = state["corpora"]["nid"][0]["text"] + " "
    assert _check(state) == []


@pytest.mark.parametrize("lang", ["bn", "bn+en", "mixed"])
def test_bangla_lang_without_bengali_script(lang):
    state = _state()
    state["corpora"]["nid"][1].update(lang=lang)  # text is all ASCII
    _only(_check(state), "nid-2", lang, "Bengali")


def test_english_lang_does_not_need_bengali_script_and_bangla_lang_with_it_is_fine():
    state = _state()
    state["corpora"]["nid"][0].update(lang="mixed", text="NID কার্ড")
    assert _check(state) == []


def test_unparseable_and_future_verified_at():
    state = _state()
    state["corpora"]["nid"][0]["fetched_at"] = "July"
    state["corpora"]["nid"][1]["fetched_at"] = "2026-10-24T00:00:00"
    problems = _check(state)
    assert len(problems) == 2, problems
    assert "nid-1" in problems[0] and "fetched_at" in problems[0] and "ISO" in problems[0]
    assert "nid-2" in problems[1] and "future" in problems[1]


def test_verified_at_today_or_earlier_is_not_future():
    state = _state()
    state["corpora"]["nid"][0]["fetched_at"] = "2026-10-23T23:59:59"
    state["corpora"]["nid"][1]["fetched_at"] = "2026-10-23"
    assert _check(state) == []
    assert len(_check(state, today=date(2026, 10, 22))) == 2


@pytest.mark.parametrize("value", [5, "", ["2026-07-12"]])
def test_non_string_or_empty_verified_at_is_unparseable(value):
    state = _state()
    state["corpora"]["nid"][1]["fetched_at"] = value
    _only(_check(state), "nid-2", "fetched_at")


def test_only_the_mapped_verified_at_field_is_checked():
    state = _state()
    state["corpora"]["nid"][0]["last_crawled"] = "garbage"  # nid maps fetched_at, not last_crawled
    state["corpora"]["bdris"][0]["fetched_at"] = "garbage"  # bdris maps no field
    assert _check(state) == []


@pytest.mark.parametrize("how", ["absent", "null"])
def test_mapped_verified_at_field_missing_from_every_record_of_a_live_service(how):
    state = _state()
    for record in state["corpora"]["nid"]:
        if how == "absent":
            del record["fetched_at"]
        else:
            record["fetched_at"] = None
    _only(_check(state), "nid", "fetched_at", "every record")


def test_mapped_verified_at_field_present_in_some_records_is_fine():
    state = _state()
    del state["corpora"]["nid"][1]["fetched_at"]
    assert _check(state) == []


def test_mapped_verified_at_field_missing_everywhere_is_allowed_for_a_hidden_service():
    state = _state()
    state["manifest"]["services"][0]["status"] = "hidden"
    for record in state["corpora"]["nid"]:
        del record["fetched_at"]
    assert _check(state) == []


def test_record_moved_to_another_service():
    state = _state()
    _only(_check(state, stored_records={"nid-1": "bdris"}), "nid-1", "bdris", "nid")


def test_record_stored_under_the_same_service_or_not_stored_is_fine():
    assert _check(_state(), stored_records={"nid-1": "nid", "something-else": "bdris"}) == []


def test_service_names_changed_from_stored():
    stored = {"nid": (NID_NAMES[0], "National Identity Card")}
    _only(_check(_state(), stored_names=stored), "nid", "National Identity Card", "National ID (NID)")
    stored = {"nid": ("পুরনো নাম", NID_NAMES[1])}
    _only(_check(_state(), stored_names=stored), "nid")


def test_gold_expected_id_missing_from_corpus():
    state = _state()
    state["gold"]["nid"]["queries"][0]["expected_id"] = "nid-99"
    _only(_check(state), "gq_nid_001", "nid-99")


def test_gold_expected_id_that_belongs_to_another_service_is_a_problem():
    state = _state()
    state["gold"]["nid"]["queries"][0]["expected_id"] = "bdris-1"
    _only(_check(state), "gq_nid_001", "bdris-1")


def test_gold_expected_id_removed_from_the_corpus_by_a_data_edit():
    state = _state()
    del state["corpora"]["nid"][0]
    _only(_check(state), "gq_nid_001", "nid-1")


def test_gold_expected_id_null_or_absent_is_not_checked():
    # Distinct from test_clean_inputs_have_no_problems, which only happens to pass over the fixture's
    # queries: here the two shapes are set up and asserted explicitly, on queries that DO name a category
    # (a category-only query, or one whose id was cleared), so nothing depends on how the fixture is cut.
    state = _state()
    queries = state["gold"]["nid"]["queries"]
    explicit_null, absent = queries[0], queries[2]
    explicit_null["expected_id"] = None
    absent.pop("expected_id", None)
    assert "expected_id" in explicit_null and explicit_null["expected_id"] is None
    assert "expected_id" not in absent
    assert explicit_null["expected_category"] and absent["expected_category"]
    assert _check(state) == []
    # not vacuous: the very same query is reported once it names an id that is not in the corpus
    absent["expected_id"] = "nid-99"
    _only(_check(state), absent["id"], "nid-99")


def test_gold_expected_id_that_is_not_a_string():
    state = _state()
    state["gold"]["nid"]["queries"][0]["expected_id"] = 7
    _only(_check(state), "gq_nid_001", "expected_id")


def test_gold_query_id_used_in_two_services():
    state = _state()
    state["gold"]["bdris"]["queries"][0]["id"] = "gq_nid_001"
    problem = _only(_check(state), "gq_nid_001", "nid", "bdris")
    assert "duplicate" in problem


def test_gold_query_id_used_twice_in_one_file():
    state = _state()
    state["gold"]["nid"]["queries"][1]["id"] = "gq_nid_001"
    _only(_check(state), "gq_nid_001", "nid #1", "nid #2")


def test_gold_query_ids_that_differ_only_in_case_are_distinct():
    state = _state()
    state["gold"]["bdris"]["queries"][0]["id"] = "GQ_NID_001"
    assert _check(state) == []


def test_known_failure_that_is_a_gold_query_of_any_service_is_fine():
    state = _state()
    state["thresholds"]["known_failures"] = {"gq_nid_003": "why", "gq_001": "why"}
    assert _check(state) == []


def test_known_failure_that_is_not_a_gold_query_id_is_a_problem():
    state = _state()
    state["thresholds"]["known_failures"] = {"gq_nid_003": "why", "gq_nid_999": "a typo"}
    problem = _only(_check(state), "known_failures", "gq_nid_999")
    assert "gq_nid_003" not in problem


def test_known_failure_whose_gold_query_was_removed_by_a_data_edit():
    state = _state()
    state["thresholds"]["known_failures"] = {"gq_nid_002": "why"}
    del state["gold"]["nid"]["queries"][1]
    _only(_check(state), "known_failures", "gq_nid_002")


def test_known_failure_ids_are_matched_exactly():
    state = _state()
    state["thresholds"]["known_failures"] = {"GQ_NID_001": "case differs", " gq_nid_001": "space"}
    problems = _check(state)
    assert len(problems) == 2, problems


def test_every_unknown_known_failure_is_listed():
    state = _state()
    state["thresholds"]["known_failures"] = {"nope_1": "x", "nope_2": "y"}
    problems = _check(state)
    assert len(problems) == 2 and "nope_1" in problems[0] and "nope_2" in problems[1]


def test_problems_are_collected_not_just_the_first():
    state = _state()
    state["corpora"]["nid"][1]["text"] = ""
    state["gold"]["bdris"]["queries"][0]["expected_id"] = "bdris-99"
    del state["registry"]["services"]["nid"]
    problems = _check(state, stored_records={"bdris-1": "nid"})
    assert len(problems) == 4, problems


def test_the_inputs_are_not_modified():
    state = _state()
    inputs = _inputs(state)
    before = copy.deepcopy(inputs.corpora), copy.deepcopy(inputs.gold)
    check_integrity(inputs, {}, {}, TODAY)
    assert (inputs.corpora, inputs.gold) == before


def test_the_committed_corpus_is_clean():
    git_dir = REPO / ".git"
    if not git_dir.is_dir():
        pytest.skip("this checkout has no .git directory (a worktree or an export)")
    inputs = load_inputs(GitSource(str(git_dir), "HEAD"))
    assert check_integrity(inputs, {}, {}, date.today()) == []
