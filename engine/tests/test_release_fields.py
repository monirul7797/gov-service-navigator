import ast
import dataclasses
import json
from datetime import date
from pathlib import Path

import pytest

from gsn.release.fields import VersionFields, fields_hash, parse_verified_at, url_kind, version_fields
from gsn.release.inputs import ServiceEntry

REPO = Path(__file__).resolve().parents[2]
CORPORA = ("nid", "epassport", "ereturn", "bdris")


def _entry(verified_at_field):
    return ServiceEntry(
        service_id="nid",
        name_bn="জাতীয় পরিচয়পত্র (NID)",
        name_en="National ID (NID)",
        status="live",
        corpus="data/chunks/nid.jsonl",
        gold="nid_gold_queries.json",
        verified_at_field=verified_at_field,
    )


def _corpus_records():
    records = []
    for name in CORPORA:
        text = (REPO / "data" / "chunks" / f"{name}.jsonl").read_text(encoding="utf-8-sig")
        records.extend(json.loads(line) for line in text.split("\n") if line.strip())
    return records


def _legacy_url_kind():
    """``answer.url_kind`` and its marker tuple, lifted out of answer.py without importing it
    (answer.py imports chromadb, which the host test environment does not have)."""
    tree = ast.parse((REPO / "answer.py").read_text(encoding="utf-8"), filename="answer.py")
    wanted = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_INFO_URL_MARKERS" for t in node.targets)
        )
        or (isinstance(node, ast.FunctionDef) and node.name == "url_kind")
    ]
    assert len(wanted) == 2, "answer.py no longer defines _INFO_URL_MARKERS and url_kind at top level"
    namespace: dict = {}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), "answer.py", "exec"), namespace)
    return namespace["url_kind"], namespace["_INFO_URL_MARKERS"]


def test_url_kind_matches_answer_url_kind_on_every_corpus_url():
    legacy, _ = _legacy_url_kind()
    urls = [record["source_url"] for record in _corpus_records()]
    assert len(urls) == 293
    assert {legacy(u) for u in urls} == {"info", "portal"}, "corpus should exercise both kinds"
    assert [url_kind(u) for u in urls] == [legacy(u) for u in urls]


def test_url_kind_uses_the_same_markers_and_edge_cases_as_the_legacy_function():
    legacy, markers = _legacy_url_kind()
    from gsn.release import fields

    assert fields._INFO_URL_MARKERS == markers
    samples = [
        "",
        "   ",
        "Gazette of Bangladesh, 2018",
        "http://x.test/apply",
        "HTTPS://X.TEST/Apply",
        "httpfoo",
        "https://x.test/FAQ",
        "https://x.test/help?id=1",
        "https://x.test/all-faq",
    ] + [f"https://x.test{marker}page" for marker in markers]
    for sample in samples:
        assert url_kind(sample) == legacy(sample), sample
    assert url_kind(None) == legacy(None) == "info"  # the legacy `(url or "")` guard is kept


def test_parse_verified_at_accepts_date_and_datetime():
    assert parse_verified_at("2026-07-12T23:04:19") == date(2026, 7, 12)
    assert parse_verified_at("2026-07-13") == date(2026, 7, 13)
    assert parse_verified_at("2026-07-12 23:04:19") == date(2026, 7, 12)
    assert parse_verified_at("2026-07-12T23:04:19.123456") == date(2026, 7, 12)
    assert parse_verified_at("2026-07-12T23:04:19+06:00") == date(2026, 7, 12)
    assert parse_verified_at("2026-07-12T23:04:19Z") == date(2026, 7, 12)
    assert parse_verified_at(None) is None


@pytest.mark.parametrize(
    "value",
    [
        "July",
        "",
        " 2026-07-12",
        "2026-07-12 ",
        "2026-13-01",
        "2026-02-30",
        "2026-07-12T25:00:00",
        "2026-07-12T",
        "20260712",
        "2026-W28-7",
        "২০২৬-০৭-১২",  # Bengali digits are not ISO digits
        5,
        2026.0,
        True,
        ["2026-07-12"],
        date(2026, 7, 12),
    ],
)
def test_parse_verified_at_rejects_garbage(value):
    with pytest.raises(ValueError):
        parse_verified_at(value)


def test_source_verified_is_the_negated_flag():
    base = {"source_url": "https://x.test/a"}
    assert version_fields({**base, "source_url_unverified": True}, _entry(None)).source_verified is False
    assert version_fields({**base, "source_url_unverified": False}, _entry(None)).source_verified is True
    assert version_fields(base, _entry(None)).source_verified is True


def test_version_fields_carries_the_record_columns():
    record = {
        "id": "nid-1",
        "category": "fees",
        "subcategory": "etin",
        "heading": "ফি",
        "lang": "bn+en",
        "source_url": "https://x.test/faq/1",
        "official": True,
        "fetched_at": "2026-07-12T23:04:19",
        "last_crawled": "2026-07-13",
    }
    assert version_fields(record, _entry("fetched_at")) == VersionFields(
        category="fees",
        subcategory="etin",
        heading="ফি",
        lang="bn+en",
        source_url="https://x.test/faq/1",
        url_kind="info",
        official=True,
        source_verified=True,
        verified_at=date(2026, 7, 12),
    )
    assert version_fields(record, _entry("last_crawled")).verified_at == date(2026, 7, 13)
    assert version_fields(record, _entry(None)).verified_at is None


def test_version_fields_leaves_absent_optional_columns_empty_and_never_assumes_official():
    fields = version_fields({"source_url": "https://x.test/apply"}, _entry("fetched_at"))
    assert fields.subcategory is None and fields.verified_at is None
    assert fields.url_kind == "portal" and fields.official is False
    assert version_fields({"source_url": "https://x.test/", "official": "true"}, _entry(None)).official is False


def test_version_fields_raises_value_error_for_an_unparseable_mapped_date():
    with pytest.raises(ValueError):
        version_fields({"source_url": "https://x.test/", "fetched_at": "July"}, _entry("fetched_at"))


# --- fields_hash: the identity of the derived columns -------------------------------------------

BASE_FIELDS = VersionFields(
    category="process_steps", subcategory="sub", heading="Heading", lang="en", source_url="https://example.test/a",
    url_kind="portal", official=True, source_verified=True, verified_at=date(2026, 7, 12),
)


def test_fields_hash_is_sha256_of_the_canonical_json_of_the_nine_columns():
    import hashlib

    expected = hashlib.sha256(json.dumps(
        {
            "category": "process_steps", "subcategory": "sub", "heading": "Heading", "lang": "en",
            "source_url": "https://example.test/a", "url_kind": "portal", "official": True,
            "source_verified": True, "verified_at": "2026-07-12",
        },
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    assert fields_hash(BASE_FIELDS) == expected
    assert fields_hash(BASE_FIELDS) == fields_hash(dataclasses.replace(BASE_FIELDS))  # deterministic
    assert len(expected) == 64 and expected == expected.lower()


def test_fields_hash_is_pinned():
    # Changing the format changes every stored identity: this literal must only move together with a decision.
    assert fields_hash(BASE_FIELDS) == "401c457fdaee61cba906d7b0a1945227a1abcbae2746a509dff8c19196a65dab"


@pytest.mark.parametrize(
    "change",
    [
        {"category": "fees"},
        {"category": None},
        {"subcategory": "other"},
        {"subcategory": None},
        {"heading": "Another heading"},
        {"heading": None},
        {"lang": "bn"},
        {"lang": None},
        {"source_url": "https://example.test/b"},
        {"url_kind": "info"},
        {"official": False},
        {"official": None},
        {"source_verified": False},
        {"verified_at": date(2026, 7, 13)},
        {"verified_at": None},
    ],
    ids=lambda change: "-".join(f"{k}={v}" for k, v in change.items()),
)
def test_fields_hash_changes_when_any_one_field_changes(change):
    assert fields_hash(dataclasses.replace(BASE_FIELDS, **change)) != fields_hash(BASE_FIELDS)


def test_fields_hash_tells_a_missing_date_from_every_date_and_all_changes_apart():
    seen = {fields_hash(dataclasses.replace(BASE_FIELDS, verified_at=v)) for v in (None, date(2026, 7, 12), date(2026, 7, 13))}
    assert len(seen) == 3


def test_fields_hash_keeps_bangla_text_literal_and_ignores_nothing_else():
    bangla = dataclasses.replace(BASE_FIELDS, heading="জন্ম নিবন্ধন")
    other = dataclasses.replace(BASE_FIELDS, heading="জন্ম নিবন্ধনা")
    assert fields_hash(bangla) != fields_hash(other)


def test_fields_hash_follows_the_manifest_mapping_and_the_derivation():
    record = {"id": "r", "text": "t", "source_url": "https://e.test/x", "fetched_at": "2026-07-12T23:04:19"}
    unmapped = version_fields(record, _entry(None))
    mapped = version_fields(record, _entry("fetched_at"))
    assert fields_hash(unmapped) != fields_hash(mapped)
    assert fields_hash(mapped) == fields_hash(version_fields(dict(record), _entry("fetched_at")))
    # the same record under a different URL rule would differ too
    assert fields_hash(version_fields({**record, "source_url": "https://e.test/faq"}, _entry("fetched_at"))) \
        != fields_hash(mapped)
