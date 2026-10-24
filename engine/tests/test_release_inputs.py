import hashlib
import json
from pathlib import Path

import pytest

from gsn.gitsource import DictSource, GitRuntimeError, GitSource, GitSourceError
from gsn.release.inputs import (
    MANIFEST_PATH,
    REGISTRY_PATH,
    THRESHOLDS_PATH,
    InputError,
    ServiceEntry,
    Thresholds,
    load_inputs,
    strict_loads,
)

REPO = Path(__file__).resolve().parents[2]
COMMIT = "0123456789abcdef0123456789abcdef01234567"
NID_CORPUS = "data/chunks/nid.jsonl"
BDRIS_CORPUS = "data/chunks/bdris.jsonl"
NID_GOLD = "nid_gold_queries.json"
BDRIS_GOLD = "bdris_gold_queries.json"

NID_RECORDS = [
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
]
BDRIS_RECORDS = [
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
]
MANIFEST = {
    "services": [
        {
            "service_id": "nid",
            "name_bn": "জাতীয় পরিচয়পত্র (NID)",
            "name_en": "National ID (NID)",
            "status": "live",
            "corpus": NID_CORPUS,
            "gold": NID_GOLD,
            "verified_at_field": "fetched_at",
        },
        {
            "service_id": "bdris",
            "name_bn": "জন্ম ও মৃত্যু নিবন্ধন",
            "name_en": "Birth and death registration",
            "status": "live",
            "corpus": BDRIS_CORPUS,
            "gold": BDRIS_GOLD,
            "verified_at_field": None,
        },
    ]
}
THRESHOLDS = {"known_failures": {"gq_nid_002": "baseline failure"}}
REGISTRY = {"_readme": "test", "services": {"nid": {}, "bdris": {}}, "concepts": {}}
NID_GOLD_FILE = {
    "top_k": 4,
    "queries": [
        {"id": "gq_nid_001", "query": "voter list", "lang": "en", "expected_category": "enrollment", "expected_id": "nid-1"},
        {"id": "gq_nid_002", "query": "weather", "lang": "en", "expected_category": None, "expected_id": None},
    ],
}
BDRIS_GOLD_FILE = {
    "top_k": 4,
    "queries": [{"id": "gq_001", "query": "birth", "lang": "en", "expected_category": "registration", "expected_id": "bdris-1"}],
}


def _json(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


def _jsonl(records, eol="\n") -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False) + eol for r in records).encode("utf-8")


def _files() -> dict[str, bytes]:
    return {
        MANIFEST_PATH: _json(MANIFEST),
        THRESHOLDS_PATH: _json(THRESHOLDS),
        REGISTRY_PATH: _json(REGISTRY),
        NID_CORPUS: _jsonl(NID_RECORDS),
        BDRIS_CORPUS: _jsonl(BDRIS_RECORDS),
        NID_GOLD: _json(NID_GOLD_FILE),
        BDRIS_GOLD: _json(BDRIS_GOLD_FILE),
    }


def _load(files: dict[str, bytes]):
    return load_inputs(DictSource(COMMIT, files))


def _problems(files: dict[str, bytes]) -> list[str]:
    with pytest.raises(InputError) as caught:
        _load(files)
    return caught.value.problems


def test_loads_the_committed_inputs_of_this_repository():
    git_dir = REPO / ".git"
    if not git_dir.is_dir():
        pytest.skip("this checkout has no .git directory (a worktree or an export)")
    source = GitSource(str(git_dir), "HEAD")
    inputs = load_inputs(source)
    assert inputs.commit == source.commit
    assert [e.service_id for e in inputs.manifest] == ["nid", "epassport", "ereturn", "bdris"]
    assert {e.status for e in inputs.manifest} == {"live"}
    assert [e.verified_at_field for e in inputs.manifest] == ["fetched_at", "last_crawled", None, None]
    assert sum(len(records) for records in inputs.corpora.values()) == 293
    assert sum(len(g["queries"]) for g in inputs.gold.values()) == 74
    known = inputs.thresholds.known_failures
    gold_ids = {q["id"] for g in inputs.gold.values() for q in g["queries"]}
    assert known, "the committed thresholds file lists the known failures of the baseline run"
    assert set(known) <= gold_ids, sorted(set(known) - gold_ids)
    assert all(isinstance(reason, str) and reason.strip() for reason in known.values())
    assert set(inputs.registry["services"]) >= {"nid", "epassport", "ereturn", "bdris"}
    assert inputs.registry_sha256 == hashlib.sha256(source.read(REGISTRY_PATH)).hexdigest()


def test_loads_a_clean_fixture_into_typed_inputs():
    inputs = _load(_files())
    assert inputs.commit == COMMIT
    assert inputs.manifest == [
        ServiceEntry(
            service_id="nid",
            name_bn="জাতীয় পরিচয়পত্র (NID)",
            name_en="National ID (NID)",
            status="live",
            corpus=NID_CORPUS,
            gold=NID_GOLD,
            verified_at_field="fetched_at",
        ),
        ServiceEntry(
            service_id="bdris",
            name_bn="জন্ম ও মৃত্যু নিবন্ধন",
            name_en="Birth and death registration",
            status="live",
            corpus=BDRIS_CORPUS,
            gold=BDRIS_GOLD,
            verified_at_field=None,
        ),
    ]
    assert inputs.manifest_json == MANIFEST
    assert inputs.thresholds == Thresholds(known_failures={"gq_nid_002": "baseline failure"})
    assert inputs.thresholds_json == THRESHOLDS
    assert inputs.registry == REGISTRY
    assert inputs.corpora == {"nid": NID_RECORDS, "bdris": BDRIS_RECORDS}
    assert inputs.gold == {"nid": NID_GOLD_FILE, "bdris": BDRIS_GOLD_FILE}


def test_bom_crlf_and_blank_lines_load_like_clean_jsonl():
    clean = _load(_files()).corpora
    lines = [json.dumps(r, ensure_ascii=False) for r in NID_RECORDS]
    variants = {
        "bom": b"\xef\xbb\xbf" + _jsonl(NID_RECORDS),
        "crlf": _jsonl(NID_RECORDS, eol="\r\n"),
        "blank lines": ("\n\n" + lines[0] + "\n\n   \n\t\n" + lines[1] + "\n\n\n").encode("utf-8"),
        "no final newline": "\n".join(lines).encode("utf-8"),
        "all three": b"\xef\xbb\xbf" + ("\r\n" + lines[0] + "\r\n\r\n" + lines[1] + "\r\n\r\n").encode("utf-8"),
    }
    for label, data in variants.items():
        loaded = _load({**_files(), NID_CORPUS: data}).corpora
        assert loaded == clean, label


def test_line_separator_characters_inside_a_record_do_not_split_it():
    record = {**NID_RECORDS[0], "text": "first second\u0085third\x0cfourth\x1dfifth"}
    data = _jsonl([record, NID_RECORDS[1]])
    assert " " in data.decode("utf-8")  # json.dumps(ensure_ascii=False) leaves it raw
    loaded = _load({**_files(), NID_CORPUS: data}).corpora["nid"]
    assert loaded == [record, NID_RECORDS[1]]


def test_an_invalid_jsonl_line_is_reported_with_its_line_number():
    good = json.dumps(NID_RECORDS[0], ensure_ascii=False)
    data = f"{good}\n\n{{not json}}\n{good}\n[1, 2]\n".encode("utf-8")
    problems = _problems({**_files(), NID_CORPUS: data})
    assert problems == [f"{NID_CORPUS}:3: invalid JSON", f"{NID_CORPUS}:5: invalid JSON (expected an object)"]


def test_only_json_whitespace_makes_a_line_blank():
    good = json.dumps(NID_RECORDS[0], ensure_ascii=False)
    data = f"{good}\n\u00a0\n".encode("utf-8")  # the second line is a no-break space
    assert _problems({**_files(), NID_CORPUS: data}) == [f"{NID_CORPUS}:2: invalid JSON"]


def test_a_corpus_that_is_not_utf8_is_reported():
    problems = _problems({**_files(), NID_CORPUS: b'{"id": "\xff\xfe"}\n'})
    assert len(problems) == 1 and NID_CORPUS in problems[0] and "UTF-8" in problems[0]


def test_missing_or_invalid_files_are_reported_together():
    files = _files()
    del files[THRESHOLDS_PATH]
    files[REGISTRY_PATH] = b"{ this is not json"
    problems = _problems(files)
    assert len(problems) == 2
    assert THRESHOLDS_PATH in problems[0] and "not found" in problems[0]
    assert REGISTRY_PATH in problems[1] and "invalid JSON" in problems[1]


def test_a_missing_corpus_and_gold_file_are_reported_with_the_manifest_still_read():
    files = _files()
    del files[BDRIS_CORPUS]
    del files[NID_GOLD]
    problems = _problems(files)
    assert len(problems) == 2
    assert any(BDRIS_CORPUS in p and "not found" in p for p in problems)
    assert any(NID_GOLD in p and "not found" in p for p in problems)


def test_a_missing_manifest_is_reported_and_the_other_files_are_still_checked():
    files = _files()
    del files[MANIFEST_PATH]
    files[THRESHOLDS_PATH] = b"[]"
    problems = _problems(files)
    assert len(problems) == 2
    assert MANIFEST_PATH in problems[0] and THRESHOLDS_PATH in problems[1]


def test_input_error_message_lists_every_problem():
    files = _files()
    del files[THRESHOLDS_PATH]
    files[REGISTRY_PATH] = b"nope"
    with pytest.raises(InputError) as caught:
        _load(files)
    text = str(caught.value)
    assert THRESHOLDS_PATH in text and REGISTRY_PATH in text


def test_registry_sha256_is_of_the_raw_bytes():
    raw = b"\xef\xbb\xbf" + json.dumps(REGISTRY, ensure_ascii=False, indent=4).replace("\n", "\r\n").encode("utf-8")
    inputs = _load({**_files(), REGISTRY_PATH: raw})
    assert inputs.registry == REGISTRY
    assert inputs.registry_sha256 == hashlib.sha256(raw).hexdigest()
    assert inputs.registry_sha256 != hashlib.sha256(_json(REGISTRY)).hexdigest()


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
@pytest.mark.parametrize(
    "path, build",
    [
        (MANIFEST_PATH, lambda t: b'{"services": [], "x": ' + t + b"}"),
        (THRESHOLDS_PATH, lambda t: b'{"known_failures": {}, "x": ' + t + b"}"),
        (REGISTRY_PATH, lambda t: b'{"services": {}, "x": ' + t + b"}"),
        (NID_GOLD, lambda t: b'{"top_k": 4, "queries": [], "x": ' + t + b"}"),
        (NID_CORPUS, lambda t: _jsonl(NID_RECORDS[:1]) + b'{"id": "nid-9", "x": ' + t + b"}\n"),
    ],
)
def test_non_finite_numbers_are_invalid_json_in_every_input(path, build, token):
    problems = _problems({**_files(), path: build(token.encode())})
    assert any(p.startswith(path) and "invalid JSON" in p for p in problems), problems
    if path == NID_CORPUS:
        assert problems == [f"{NID_CORPUS}:2: invalid JSON"]


def test_strict_loads_rejects_non_finite_numbers_and_accepts_ordinary_ones():
    assert strict_loads('{"a": [1, 2.5, -3e2, 0.0], "b": "NaN"}') == {"a": [1, 2.5, -300.0, 0.0], "b": "NaN"}
    for bad in ("NaN", "Infinity", "-Infinity", "[1e999]", '{"a": NaN}'):
        with pytest.raises(ValueError):
            strict_loads(bad)


def test_a_jsonl_line_with_a_duplicate_key_is_invalid_json():
    good = json.dumps(NID_RECORDS[0], ensure_ascii=False)
    twice = '{"id": "nid-9", "text": "what the reviewer saw", "text": "what would be published"}'
    problems = _problems({**_files(), NID_CORPUS: f"{good}\n{twice}\n".encode("utf-8")})
    assert problems == [f"{NID_CORPUS}:2: invalid JSON"]


def test_a_nested_duplicate_key_in_a_whole_file_is_invalid_json():
    gold = b'{"top_k": 4, "queries": [{"id": "gq_1", "query": "a", "expected": {"x": 1, "x": 1}}]}'
    problems = _problems({**_files(), NID_GOLD: gold})
    assert problems == [f"{NID_GOLD}: invalid JSON"]


def test_strict_loads_rejects_duplicate_keys_at_any_depth_but_not_repeated_keys_elsewhere():
    for bad in ('{"a": 1, "a": 2}', '{"a": 1, "a": 1}', '[{"b": {"c": [{"d": 1, "d": 2}]}}]'):
        with pytest.raises(ValueError):
            strict_loads(bad)
    assert strict_loads('[{"a": 1}, {"a": 2}, {"a": {"a": 3}}]') == [{"a": 1}, {"a": 2}, {"a": {"a": 3}}]
    assert strict_loads('{"a": 1, "A": 2}') == {"a": 1, "A": 2}


def test_deeply_nested_json_is_invalid_not_a_crash():
    deep = b"[" * 100_000 + b"]" * 100_000
    problems = _problems({**_files(), THRESHOLDS_PATH: deep})
    assert len(problems) == 1 and "invalid JSON" in problems[0]


def _with_service(**changes):
    entry = {**MANIFEST["services"][0], **changes}
    return _json({"services": [entry, MANIFEST["services"][1]]})


@pytest.mark.parametrize(
    "manifest_bytes, fragment",
    [
        (_json([]), '"services" list'),
        (_json({"services": []}), "no services"),
        (_json({"services": "nid"}), '"services" list'),
        (_json({"services": ["nid"]}), "services[0]"),
        (_with_service(status="draft"), "status"),
        (_with_service(service_id="NID"), "service_id"),
        (_with_service(service_id="1nid"), "service_id"),
        (_with_service(service_id="nid\n"), "service_id"),
        (_with_service(name_bn=""), "name_bn"),
        (_with_service(name_en=5), "name_en"),
        (_with_service(corpus="/etc/passwd"), "corpus"),
        (_with_service(corpus="data/../../secret.jsonl"), "corpus"),
        (_with_service(gold="data\\gold.json"), "gold"),
        (_with_service(verified_at_field=5), "verified_at_field"),
        (_with_service(verified_at_field="fetchd_at"), "verified_at_field"),  # a typo would silently drop every date
        (_with_service(verified_at_field="last_updated"), "verified_at_field"),  # the source's own date: forbidden
        (_with_service(verified_at_field=""), "verified_at_field"),
        (_json({"services": [MANIFEST["services"][0], MANIFEST["services"][0]]}), "duplicate"),
    ],
)
def test_manifest_shape_problems_are_reported(manifest_bytes, fragment):
    problems = _problems({**_files(), MANIFEST_PATH: manifest_bytes})
    assert len(problems) == 1, problems
    assert problems[0].startswith(MANIFEST_PATH) and fragment in problems[0]


def test_an_unknown_verified_at_field_names_the_service_and_the_allowed_values():
    problems = _problems({**_files(), MANIFEST_PATH: _with_service(verified_at_field="last_updated")})
    assert len(problems) == 1
    assert "(nid)" in problems[0] and "'last_updated'" in problems[0]
    assert "fetched_at" in problems[0] and "last_crawled" in problems[0] and "null" in problems[0]


def test_only_fetched_at_last_crawled_or_null_are_accepted():
    for value in ("fetched_at", "last_crawled", None):
        manifest = _with_service(verified_at_field=value)
        assert _load({**_files(), MANIFEST_PATH: manifest}).manifest[0].verified_at_field == value


def test_a_manifest_entry_without_verified_at_field_is_a_problem_not_a_silent_none():
    entry = {k: v for k, v in MANIFEST["services"][0].items() if k != "verified_at_field"}
    problems = _problems({**_files(), MANIFEST_PATH: _json({"services": [entry, MANIFEST["services"][1]]})})
    assert len(problems) == 1 and "verified_at_field" in problems[0]


@pytest.mark.parametrize(
    "thresholds",
    [[], {}, {"known_failures": []}, {"known_failures": {"gq_1": 5}}, {"known_failures": {"gq_1": ""}}],
)
def test_thresholds_shape_problems_are_reported(thresholds):
    problems = _problems({**_files(), THRESHOLDS_PATH: _json(thresholds)})
    assert len(problems) == 1 and problems[0].startswith(THRESHOLDS_PATH)


@pytest.mark.parametrize("registry", [[], {"services": []}, {"concepts": {}}])
def test_registry_shape_problems_are_reported(registry):
    problems = _problems({**_files(), REGISTRY_PATH: _json(registry)})
    assert len(problems) == 1 and problems[0].startswith(REGISTRY_PATH)


@pytest.mark.parametrize(
    "gold, fragment",
    [
        ([], '"queries" list'),
        ({"top_k": 4}, '"queries" list'),
        ({"queries": ["q"]}, "queries[0]"),
        ({"queries": [{"query": "no id"}]}, "queries[0]"),
        ({"queries": [{"id": "", "query": "empty id"}]}, "queries[0]"),
    ],
)
def test_gold_shape_problems_are_reported(gold, fragment):
    problems = _problems({**_files(), NID_GOLD: _json(gold)})
    assert len(problems) == 1, problems
    assert problems[0].startswith(NID_GOLD) and fragment in problems[0]


def test_a_hidden_service_still_needs_its_listed_files():
    manifest = {"services": [{**MANIFEST["services"][0], "status": "hidden"}, MANIFEST["services"][1]]}
    files = {**_files(), MANIFEST_PATH: _json(manifest)}
    assert [e.status for e in _load(files).manifest] == ["hidden", "live"]
    del files[NID_CORPUS]
    assert any(NID_CORPUS in p for p in _problems(files))


# --- git failures while reading (final review M2) --------------------------------------------------


class _FailingSource:
    """A source whose ``read`` fails with ``error`` for the paths in ``failing`` and serves the rest."""

    def __init__(self, error: Exception, failing: set[str]) -> None:
        self.commit = COMMIT
        self._error, self._failing, self._files = error, failing, _files()

    def read(self, path: str) -> bytes:
        if path in self._failing:
            raise self._error
        return self._files[path]


@pytest.mark.parametrize("path", [MANIFEST_PATH, THRESHOLDS_PATH, REGISTRY_PATH, NID_CORPUS, NID_GOLD])
def test_a_git_runtime_failure_is_not_an_input_problem(path):
    with pytest.raises(GitRuntimeError):
        load_inputs(_FailingSource(GitRuntimeError("could not run git (TimeoutExpired)"), {path}))


def test_a_git_runtime_failure_wins_over_ordinary_input_problems():
    files = _files()
    del files[BDRIS_CORPUS]  # an ordinary problem, found before the git failure is hit
    source = _FailingSource(GitRuntimeError("could not run git (OSError)"), {NID_GOLD})
    source._files = files
    with pytest.raises(GitRuntimeError):
        load_inputs(source)


def test_a_git_refusal_while_reading_is_an_input_problem():
    # e.g. the manifest names a directory, which git cannot read as a file: the owner fixes the manifest
    source = _FailingSource(GitSourceError("path 'data/chunks/nid.jsonl' at commit abc is not a file"), {NID_CORPUS})
    with pytest.raises(InputError) as caught:
        load_inputs(source)
    assert len(caught.value.problems) == 1
    assert caught.value.problems[0].startswith(f"{NID_CORPUS}: cannot be read") and "is not a file" in caught.value.problems[0]


def test_files_missing_at_the_commit_are_still_input_problems():
    files = _files()
    del files[NID_GOLD]
    assert _problems(files) == [f"{NID_GOLD}: not found at this commit"]
