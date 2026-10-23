"""Integrity rules a release must satisfy before anything is written to the database.

``check_integrity`` returns every problem it finds as a plain sentence naming the service, record or
gold query involved; an empty list means the inputs are clean. A release with any problem is refused.
The rules are pure: they read the parsed inputs plus two small views of what the database already
stores (which service each record belongs to, and each service's names).
"""
from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from datetime import date

from gsn.release.fields import parse_verified_at
from gsn.release.inputs import REGISTRY_PATH, ReleaseInputs, ServiceEntry

_BENGALI_SCRIPT = re.compile("[ঀ-৿]")
_BANGLA_LANGS = ("bn", "bn+en", "mixed")
_TODO_MARKER = "todo(verify)"  # the project's "unverified, do not publish" marker, matched ignoring case

_Record = tuple[ServiceEntry, int, dict, str]  # (service, 1-based position in its corpus, record, label)


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _records(inputs: ReleaseInputs) -> Iterator[_Record]:
    """Every corpus record in manifest order, with a label that names it in a problem sentence.

    The label is ``"<service> record <id>"``; a record without a usable id is ``"<service> record #<n>"``.
    """
    for entry in inputs.manifest:
        for number, record in enumerate(inputs.corpora.get(entry.service_id, []), start=1):
            rid = record.get("id")
            label = f"{entry.service_id} record {rid}" if _non_empty(rid) else f"{entry.service_id} record #{number}"
            yield entry, number, record, label


def _missing_registry_entries(inputs: ReleaseInputs) -> list[str]:
    registered = inputs.registry.get("services", {})
    return [
        f"service {entry.service_id}: live, but {REGISTRY_PATH} has no entry for it under \"services\""
        for entry in inputs.manifest
        if entry.status == "live" and entry.service_id not in registered
    ]


def _missing_fields(inputs: ReleaseInputs) -> list[str]:
    problems = []
    for _, _, record, label in _records(inputs):
        if not _non_empty(record.get("id")):
            problems.append(f"{label}: id is missing or not a non-empty string")
        for field in ("text", "source_url"):
            if not _non_empty(record.get(field)):
                problems.append(f"{label}: {field} is missing or empty")
    return problems


def _marker_paths(record: dict) -> list[str]:
    """Paths (``a.b``, ``a[1]``) of every string key or value in ``record`` that contains the marker.

    Iterative, so a deeply nested record cannot exhaust the interpreter's recursion limit.
    """
    hits: list[str] = []
    stack: list[tuple[str, object]] = [("", record)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, str):
            if _TODO_MARKER in value.lower():
                hits.append(path)
        elif isinstance(value, dict):
            children = []
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                if isinstance(key, str) and _TODO_MARKER in key.lower():
                    hits.append(child_path)
                children.append((child_path, child))
            stack.extend(reversed(children))
        elif isinstance(value, list):
            stack.extend(reversed([(f"{path}[{i}]", child) for i, child in enumerate(value)]))
    return hits


def _todo_markers(inputs: ReleaseInputs) -> list[str]:
    problems = []
    for _, _, record, label in _records(inputs):
        paths = _marker_paths(record)
        if paths:
            problems.append(f"{label}: contains TODO(verify) in {', '.join(paths)}")
    return problems


def _duplicate_ids(inputs: ReleaseInputs) -> list[str]:
    where: dict[str, list[str]] = {}
    for entry, number, record, _ in _records(inputs):
        rid = record.get("id")
        if _non_empty(rid):
            where.setdefault(rid, []).append(f"{entry.service_id} #{number}")
    return [
        f"duplicate record id {rid}: {', '.join(places)}"
        for rid, places in where.items()
        if len(places) > 1
    ]


def _duplicate_texts(inputs: ReleaseInputs) -> list[str]:
    labels: dict[str, list[str]] = {}
    for _, _, record, label in _records(inputs):
        text = record.get("text")
        if _non_empty(text):
            labels.setdefault(text, []).append(label)
    return [f"identical text in {', '.join(group)}" for group in labels.values() if len(group) > 1]


def _bangla_without_script(inputs: ReleaseInputs) -> list[str]:
    problems = []
    for _, _, record, label in _records(inputs):
        text = record.get("text")
        lang = record.get("lang")
        if lang in _BANGLA_LANGS and _non_empty(text) and not _BENGALI_SCRIPT.search(text):
            problems.append(f"{label}: lang is {lang!r} but the text has no Bengali script")
    return problems


def _verified_at(inputs: ReleaseInputs, today: date) -> list[str]:
    problems = []
    for entry, _, record, label in _records(inputs):
        field = entry.verified_at_field
        value = record.get(field) if field else None
        if value is None:
            continue
        try:
            verified = parse_verified_at(value)
        except ValueError:
            problems.append(f"{label}: {field} {value!r} is not an ISO date or date-time")
            continue
        if verified is not None and verified > today:
            problems.append(f"{label}: {field} {value!r} is in the future (today is {today.isoformat()})")
    return problems


def _unused_verified_at_fields(inputs: ReleaseInputs) -> list[str]:
    """A live service whose mapped field is in none of its records would publish no dates at all."""
    problems = []
    for entry in inputs.manifest:
        field = entry.verified_at_field
        records = inputs.corpora.get(entry.service_id, [])
        if entry.status == "live" and field and records and all(r.get(field) is None for r in records):
            problems.append(
                f"service {entry.service_id}: verified_at_field {field!r} is absent from every record of its corpus"
            )
    return problems


def _moved_records(inputs: ReleaseInputs, stored: Mapping[str, str]) -> list[str]:
    problems = []
    for entry, _, record, _ in _records(inputs):
        rid = record.get("id")
        before = stored.get(rid) if _non_empty(rid) else None
        if before is not None and before != entry.service_id:
            problems.append(
                f"record {rid}: stored under service {before}, but the corpus now lists it under {entry.service_id}"
            )
    return problems


def _changed_service_names(inputs: ReleaseInputs, stored: Mapping[str, tuple[str, str]]) -> list[str]:
    problems = []
    for entry in inputs.manifest:
        before = stored.get(entry.service_id)
        now = (entry.name_bn, entry.name_en)
        if before is not None and tuple(before) != now:
            problems.append(
                f"service {entry.service_id}: names changed from the stored ones "
                f"(stored bn={before[0]!r}, en={before[1]!r}; manifest bn={now[0]!r}, en={now[1]!r})"
            )
    return problems


def _gold_expected_ids(inputs: ReleaseInputs) -> list[str]:
    problems = []
    for entry in inputs.manifest:
        known = {r["id"] for r in inputs.corpora.get(entry.service_id, []) if _non_empty(r.get("id"))}
        for query in inputs.gold.get(entry.service_id, {}).get("queries", []):
            expected = query.get("expected_id")
            if expected is None:  # no expected_id (or null): an out-of-scope probe or a category-only query
                continue
            where = f"gold query {query.get('id')} ({entry.service_id})"
            if not isinstance(expected, str):
                problems.append(f"{where}: expected_id must be a string or null, got {expected!r}")
            elif expected not in known:
                problems.append(f"{where}: expected_id {expected!r} is not a record of the {entry.service_id} corpus")
    return problems


def _duplicate_gold_ids(inputs: ReleaseInputs) -> list[str]:
    """Gold query ids are unique across the whole release: ``known_failures`` is keyed by the bare id."""
    where: dict[str, list[str]] = {}
    for entry in inputs.manifest:
        for number, query in enumerate(inputs.gold.get(entry.service_id, {}).get("queries", []), start=1):
            qid = query.get("id")
            if _non_empty(qid):
                where.setdefault(qid, []).append(f"{entry.service_id} #{number}")
    return [
        f"duplicate gold query id {qid}: {', '.join(places)}"
        for qid, places in where.items()
        if len(places) > 1
    ]


def _unknown_known_failures(inputs: ReleaseInputs) -> list[str]:
    """Every ``known_failures`` id must be a gold query of this release (a typo or a removed query
    would otherwise sit in the file doing nothing, and the next query given that id would be waved through)."""
    gold_ids = {
        query.get("id")
        for entry in inputs.manifest
        for query in inputs.gold.get(entry.service_id, {}).get("queries", [])
    }
    return [
        f"known_failures lists {qid!r}, which is not a gold query id of this release"
        for qid in sorted(inputs.thresholds.known_failures)
        if qid not in gold_ids
    ]


def check_integrity(
    inputs: ReleaseInputs,
    stored_record_services: Mapping[str, str],
    stored_service_names: Mapping[str, tuple[str, str]],
    today: date,
) -> list[str]:
    """Every integrity problem in ``inputs``; an empty list means clean.

    ``stored_record_services`` maps record id to the service the database already holds it under and
    ``stored_service_names`` maps service id to its stored ``(name_bn, name_en)``; both may be empty
    (first release). ``today`` bounds ``verified_at``: a later date is a problem.
    """
    return [
        *_missing_registry_entries(inputs),
        *_missing_fields(inputs),
        *_todo_markers(inputs),
        *_duplicate_ids(inputs),
        *_duplicate_texts(inputs),
        *_bangla_without_script(inputs),
        *_verified_at(inputs, today),
        *_unused_verified_at_fields(inputs),
        *_moved_records(inputs, stored_record_services),
        *_changed_service_names(inputs, stored_service_names),
        *_gold_expected_ids(inputs),
        *_duplicate_gold_ids(inputs),
        *_unknown_known_failures(inputs),
    ]
