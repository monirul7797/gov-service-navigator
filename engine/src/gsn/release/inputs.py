"""The committed files a release is built from, read from one commit through a ``ContentSource``.

Inputs: the release manifest, the thresholds file, the concept registry, and per service a corpus
(JSON lines) and a gold-query file. Nothing here touches a database or an embedding model, and
nothing reads the working tree.

Loading is all-or-nothing but reports everything at once: every missing file, unparseable file and
wrongly shaped value is collected and raised together as one ``InputError``, so the owner fixes the
whole list in one pass. What the files *mean* (duplicate ids, missing registry entries, ...) is
checked afterwards by ``gsn.release.integrity``.

Every JSON document is parsed strictly (``strict_loads``): ``NaN``, ``Infinity`` and numbers that
overflow to infinity are refused, because ``json.dumps`` would write them back as non-JSON tokens and
silently change what a record hashes to.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

from gsn.gitsource import ContentSource, GitSourceError

MANIFEST_PATH = "data/release_manifest.json"
THRESHOLDS_PATH = "data/release_thresholds.json"
REGISTRY_PATH = "concept_registry.json"

_SERVICE_ID = re.compile(r"[a-z][a-z0-9_]*")  # matches the services.service_id constraint in the database
_STATUSES = ("live", "hidden")
_JSON_WHITESPACE = " \t\r"


class InputError(Exception):
    """One or more release inputs are missing, unparseable or wrongly shaped; ``problems`` lists all."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("release inputs are invalid:\n" + "\n".join(f"  - {p}" for p in self.problems))


@dataclass(frozen=True)
class ServiceEntry:
    """One manifest entry: a service, its files, and which record field says when it was verified."""

    service_id: str
    name_bn: str
    name_en: str
    status: str
    corpus: str
    gold: str
    verified_at_field: str | None


@dataclass(frozen=True)
class Thresholds:
    """Eval-gate settings. ``known_failures`` maps a gold-query id to the reason its FAIL is accepted."""

    known_failures: dict[str, str]


@dataclass(frozen=True)
class ReleaseInputs:
    """Everything a build reads, parsed. ``corpora`` and ``gold`` are keyed by service id."""

    commit: str
    manifest: list[ServiceEntry]
    manifest_json: dict
    thresholds: Thresholds
    thresholds_json: dict
    registry: dict
    registry_sha256: str
    corpora: dict[str, list[dict]]
    gold: dict[str, dict]


class _NonFinite(ValueError):
    """A JSON document contained NaN, Infinity or a number too large for a float."""


def _reject_constant(token: str) -> Any:
    raise _NonFinite(token)


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise _NonFinite(text)
    return value


def strict_loads(text: str) -> Any:
    """``json.loads`` that refuses ``NaN``, ``Infinity``, ``-Infinity`` and overflowing numbers.

    Raises ``ValueError`` (``json.JSONDecodeError`` is one) for any invalid document.
    """
    return json.loads(text, parse_constant=_reject_constant, parse_float=_finite_float)


def _read(source: ContentSource, path: str, problems: list[str]) -> bytes | None:
    try:
        return source.read(path)
    except FileNotFoundError:
        problems.append(f"{path}: not found at this commit")
    except GitSourceError as exc:
        problems.append(f"{path}: cannot be read ({exc})")
    return None


def _decode(raw: bytes, path: str, problems: list[str]) -> str | None:
    try:
        return raw.decode("utf-8-sig")  # tolerates a byte-order mark, which Windows editors add
    except UnicodeDecodeError:
        problems.append(f"{path}: not valid UTF-8")
        return None


def _load_json(source: ContentSource, path: str, problems: list[str]) -> tuple[Any, bytes] | None:
    """Parse a whole-file JSON document; returns ``(value, raw_bytes)`` or ``None`` with a problem noted."""
    raw = _read(source, path, problems)
    if raw is None:
        return None
    text = _decode(raw, path, problems)
    if text is None:
        return None
    try:
        return strict_loads(text), raw
    except (ValueError, RecursionError):
        problems.append(f"{path}: invalid JSON")
        return None


def _load_jsonl(source: ContentSource, path: str, problems: list[str]) -> list[dict] | None:
    """Parse a JSON-lines file: one object per line, CRLF and blank lines tolerated, BOM ignored.

    Lines are split on ``\\n`` only (``str.splitlines`` would also split inside a record on U+2028 and
    friends), and line numbers in problems are file line numbers, blank lines included.
    """
    raw = _read(source, path, problems)
    if raw is None:
        return None
    text = _decode(raw, path, problems)
    if text is None:
        return None
    records: list[dict] = []
    clean = True
    for number, line in enumerate(text.split("\n"), start=1):
        line = line.strip(_JSON_WHITESPACE)
        if not line:
            continue
        try:
            value = strict_loads(line)
        except (ValueError, RecursionError):
            problems.append(f"{path}:{number}: invalid JSON")
            clean = False
            continue
        if not isinstance(value, dict):
            problems.append(f"{path}:{number}: invalid JSON (expected an object)")
            clean = False
            continue
        records.append(value)
    return records if clean else None


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_repo_path(value: object) -> bool:
    """A repository-relative path with `/` separators: no leading slash, no empty, `.` or `..` parts."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and "\\" not in value
        and "\0" not in value
        and not value.startswith("/")
        and all(part not in ("", ".", "..") for part in value.split("/"))
    )


def _parse_entry(raw: object, index: int, problems: list[str]) -> ServiceEntry | None:
    where = f"{MANIFEST_PATH}: services[{index}]"
    if not isinstance(raw, dict):
        problems.append(f"{where} is not an object")
        return None
    found = len(problems)
    sid = raw.get("service_id")
    if not (isinstance(sid, str) and _SERVICE_ID.fullmatch(sid)):
        problems.append(f"{where}: service_id must match [a-z][a-z0-9_]*, got {sid!r}")
    else:
        where = f"{where} ({sid})"
    for key in ("name_bn", "name_en"):
        if not _is_text(raw.get(key)):
            problems.append(f"{where}: {key} must be a non-empty string")
    if raw.get("status") not in _STATUSES:
        problems.append(f"{where}: status must be one of {', '.join(_STATUSES)}, got {raw.get('status')!r}")
    for key in ("corpus", "gold"):
        if not _is_repo_path(raw.get(key)):
            problems.append(f"{where}: {key} must be a repository-relative path, got {raw.get(key)!r}")
    if "verified_at_field" not in raw:
        problems.append(f"{where}: verified_at_field is missing (use null when the service has none)")
    elif raw["verified_at_field"] is not None and not _is_text(raw["verified_at_field"]):
        problems.append(f"{where}: verified_at_field must be a field name or null, got {raw['verified_at_field']!r}")
    if len(problems) > found:
        return None
    return ServiceEntry(
        service_id=raw["service_id"],
        name_bn=raw["name_bn"],
        name_en=raw["name_en"],
        status=raw["status"],
        corpus=raw["corpus"],
        gold=raw["gold"],
        verified_at_field=raw["verified_at_field"],
    )


def _load_manifest(source: ContentSource, problems: list[str]) -> tuple[dict, list[ServiceEntry]] | None:
    loaded = _load_json(source, MANIFEST_PATH, problems)
    if loaded is None:
        return None
    manifest, _ = loaded
    services = manifest.get("services") if isinstance(manifest, dict) else None
    if not isinstance(services, list):
        problems.append(f'{MANIFEST_PATH}: must be an object with a "services" list')
        return None
    if not services:
        problems.append(f"{MANIFEST_PATH}: lists no services")
        return None
    entries: list[ServiceEntry] = []
    seen: set[str] = set()
    for index, raw in enumerate(services):
        entry = _parse_entry(raw, index, problems)
        if entry is None:
            continue
        if entry.service_id in seen:
            problems.append(f"{MANIFEST_PATH}: duplicate service_id {entry.service_id!r}")
            continue
        seen.add(entry.service_id)
        entries.append(entry)
    return manifest, entries


def _load_thresholds(source: ContentSource, problems: list[str]) -> tuple[dict, Thresholds] | None:
    loaded = _load_json(source, THRESHOLDS_PATH, problems)
    if loaded is None:
        return None
    thresholds, _ = loaded
    known = thresholds.get("known_failures") if isinstance(thresholds, dict) else None
    if not isinstance(known, dict):
        problems.append(f'{THRESHOLDS_PATH}: must be an object with a "known_failures" object')
        return None
    bad = [qid for qid, reason in known.items() if not _is_text(reason)]
    if bad:
        problems.append(f"{THRESHOLDS_PATH}: known_failures needs a non-empty reason for {', '.join(sorted(bad))}")
        return None
    return thresholds, Thresholds(known_failures=dict(known))


def _load_registry(source: ContentSource, problems: list[str]) -> tuple[dict, str] | None:
    loaded = _load_json(source, REGISTRY_PATH, problems)
    if loaded is None:
        return None
    registry, raw = loaded
    if not isinstance(registry, dict) or not isinstance(registry.get("services"), dict):
        problems.append(f'{REGISTRY_PATH}: must be an object with a "services" object')
        return None
    return registry, hashlib.sha256(raw).hexdigest()


def _load_gold(source: ContentSource, path: str, problems: list[str]) -> dict | None:
    loaded = _load_json(source, path, problems)
    if loaded is None:
        return None
    gold, _ = loaded
    queries = gold.get("queries") if isinstance(gold, dict) else None
    if not isinstance(queries, list):
        problems.append(f'{path}: must be an object with a "queries" list')
        return None
    found = len(problems)
    for index, query in enumerate(queries):
        if not isinstance(query, dict):
            problems.append(f"{path}: queries[{index}] is not an object")
        elif not _is_text(query.get("id")):
            problems.append(f"{path}: queries[{index}] has no id (a non-empty string is required)")
    return gold if len(problems) == found else None


def load_inputs(source: ContentSource) -> ReleaseInputs:
    """Read and parse every release input at ``source.commit``.

    Raises ``InputError`` listing every missing, unparseable or wrongly shaped input; nothing is
    returned unless all of them load.
    """
    problems: list[str] = []
    manifest = _load_manifest(source, problems)
    thresholds = _load_thresholds(source, problems)
    registry = _load_registry(source, problems)
    corpora: dict[str, list[dict]] = {}
    gold: dict[str, dict] = {}
    for entry in manifest[1] if manifest else []:
        records = _load_jsonl(source, entry.corpus, problems)
        if records is not None:
            corpora[entry.service_id] = records
        queries = _load_gold(source, entry.gold, problems)
        if queries is not None:
            gold[entry.service_id] = queries
    if problems or manifest is None or thresholds is None or registry is None:
        raise InputError(problems)
    return ReleaseInputs(
        commit=source.commit,
        manifest=manifest[1],
        manifest_json=manifest[0],
        thresholds=thresholds[1],
        thresholds_json=thresholds[0],
        registry=registry[0],
        registry_sha256=registry[1],
        corpora=corpora,
        gold=gold,
    )
