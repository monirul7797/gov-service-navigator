"""Parity proof: does a release retrieve what ChromaDB retrieved? (design section 7.3)

Before ChromaDB is retired, ``gsn parity --release ID`` compares one release with the baseline that
``tools/m1/export_chroma_baseline.py`` recorded and committed under ``docs/reports/m1-parity/``. The
baseline files are read from the release's own commit (the ``source``), so a release is always judged
against the baseline it was built together with. The comparison, in this order:

1. **Documents.** Every member's ``text_hash`` must equal the baseline's ``documents`` entry, and the
   record id sets must match. The baseline may have been recorded on an earlier commit, but the
   texts that were embedded must be identical, otherwise nothing below means anything.
2. **Vectors.** Each stored vector must have cosine similarity of at least ``VECTOR_FLOOR`` with
   ChromaDB's vector for the same record.
3. **Gold queries.** Each recorded gold query is run again, with the baseline entry's own recorded
   ``gq`` and ``top_k`` (the baseline is a frozen snapshot: the current gold files play no part).
   The verdict (``gsn.evals.judge``) must equal the legacy verdict, and the top-k records must be the
   baseline's (``compare_ranked``).
4. **Retrieve runs.** Each recorded ``answer.retrieve`` run is replayed through ``gsn.retrieval``
   with its own recorded queries and services; the selected context must be the baseline's.

A failure in step 1 or 2 does not stop the run: everything is still compared, so one report shows all
the differences. The run is refused (``ParityError``, exit status 2) before any search when the
embedder is not the model the release was built with, or the baseline was not recorded with the
pinned model revision.

``compare_ranked`` allows a similarity to differ by ``TOLERANCE`` and a pair of records to swap places
only when their baseline similarities differ by less than that (a *tie swap*: reported, not a failure).
Every similarity within ``TOLERANCE`` of a decision threshold is listed for a human look.

The report is a plain dict of strings, integers, booleans, lists and dicts: ``format_report`` prints it
with sorted keys, and the same inputs always print the same text.
"""
from __future__ import annotations

import gzip
import json
import math
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace

from gsn import evals, retrieval, store
from gsn.embed import BGE_M3_REVISION, Embedder
from gsn.gitsource import ContentSource
from gsn.release.commands import CommandError, open_repository
from gsn.retrieval import GROUNDING_FLOOR, SERVICE_MIN_SIM

__all__ = [
    "BASELINE_PATH", "RankedDiff", "ParityError", "TOLERANCE", "VECTORS_PATH", "VECTOR_FLOOR", "check_documents",
    "compare_ranked", "compare_vectors", "cosine", "format_report", "run_parity",
]

TOLERANCE = 0.001        # a similarity may differ by this much from the baseline's; a tie is a gap below it
VECTOR_FLOOR = 0.9999    # a stored vector must have at least this cosine similarity with ChromaDB's

PARITY_DIR = "docs/reports/m1-parity"
BASELINE_PATH = f"{PARITY_DIR}/chroma_baseline.json"
VECTORS_PATH = f"{PARITY_DIR}/chroma_vectors.json.gz"

_LISTED_IDS = 20         # a problem line names at most this many record ids


class ParityError(CommandError):
    """The parity run cannot be made as asked (no such release, a different model, an unreadable
    baseline). The message says why and is safe to show; the CLI maps it to exit status 2."""


@dataclass(frozen=True)
class RankedDiff:
    """How two ranked ``(record_id, similarity)`` lists differ. Only ``problems`` fail the comparison."""

    ok: bool
    problems: list[str]
    tie_swaps: list[str]
    near_threshold: list[str]


def _ids(ids: Sequence[str]) -> str:
    """The first record ids of a sorted list, then how many more there are."""
    shown = ", ".join(ids[:_LISTED_IDS])
    return shown if len(ids) <= _LISTED_IDS else f"{shown} ... and {len(ids) - _LISTED_IDS} more"


# --- vectors ------------------------------------------------------------------------------------


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors of equal length; ``nan`` if either has length zero."""
    norm = math.sqrt(math.sumprod(a, a)) * math.sqrt(math.sumprod(b, b))
    return math.sumprod(a, b) / norm if norm else math.nan


def compare_vectors(baseline: Mapping[str, Sequence[float]], current: Mapping[str, Sequence[float]]) -> list[str]:
    """One problem line per record whose vector is missing, unexpected, of another size or too far from
    the baseline's (cosine below ``VECTOR_FLOOR``), in record-id order. Empty when every vector matches."""
    problems = []
    for record_id in sorted(baseline.keys() | current.keys()):
        if record_id not in current:
            problems.append(f"{record_id}: no vector in the release")
        elif record_id not in baseline:
            problems.append(f"{record_id}: a vector in the release but none in the baseline")
        elif len(baseline[record_id]) != len(current[record_id]):
            problems.append(f"{record_id}: {len(current[record_id])} dimensions, the baseline has "
                            f"{len(baseline[record_id])}")
        else:
            similarity = cosine(baseline[record_id], current[record_id])
            if not similarity >= VECTOR_FLOOR:      # also true for nan
                problems.append(f"{record_id}: cosine similarity {similarity:.6f} with the baseline vector, "
                                f"below {VECTOR_FLOOR}")
    return problems


# --- ranked lists -------------------------------------------------------------------------------


def _near(similarity: float | None, threshold: float) -> bool:
    return similarity is not None and abs(similarity - threshold) <= TOLERANCE


def _shown(similarity: float | None) -> str:
    return "-" if similarity is None else f"{similarity:.6f}"


def compare_ranked(
    expected: Sequence[tuple[str, float]], actual: Sequence[tuple[str, float]], thresholds: Iterable[float | None]
) -> RankedDiff:
    """Compare a baseline ranking (best first) with a release's.

    Problems: a record in only one of the two lists, a repeated record, a similarity that differs by more
    than ``TOLERANCE``, and a pair of records in the opposite order whose baseline similarities do not
    differ by less than ``TOLERANCE``. A pair in the opposite order that does is a tie swap (listed, not
    a problem). Order is judged pair by pair only when both lists hold the same records. A similarity
    (the baseline's or the release's) within ``TOLERANCE`` of any threshold is listed in
    ``near_threshold``; a threshold of ``None`` is skipped. ``thresholds`` may be any iterable.
    """
    limits = [threshold for threshold in thresholds if threshold is not None]
    problems: list[str] = []
    tie_swaps: list[str] = []
    near_threshold: list[str] = []
    expected_ids = [record_id for record_id, _ in expected]
    actual_ids = [record_id for record_id, _ in actual]
    want, got = dict(expected), dict(actual)
    for name, ids in (("baseline", expected_ids), ("result", actual_ids)):
        repeated = sorted({i for i in ids if ids.count(i) > 1})
        if repeated:
            problems.append(f"repeated record in the {name}: {_ids(repeated)}")

    missing = sorted(want.keys() - got.keys())
    unexpected = sorted(got.keys() - want.keys())
    if missing:
        problems.append(f"in the baseline but not in the result: {_ids(missing)}")
    if unexpected:
        problems.append(f"in the result but not in the baseline: {_ids(unexpected)}")

    for record_id in dict.fromkeys(expected_ids):
        if record_id in got:
            drift = abs(got[record_id] - want[record_id])
            if not drift <= TOLERANCE:      # also true for nan
                problems.append(f"{record_id}: similarity {got[record_id]:.6f}, the baseline has "
                                f"{want[record_id]:.6f} (differs by {drift:.6f}, more than {TOLERANCE})")

    if not missing and not unexpected:
        position = {record_id: index for index, record_id in enumerate(dict.fromkeys(actual_ids))}
        order = list(dict.fromkeys(expected_ids))
        for index, first in enumerate(order):
            for second in order[index + 1:]:
                if position[first] < position[second]:
                    continue
                gap = abs(want[first] - want[second])
                if gap < TOLERANCE:
                    tie_swaps.append(f"{first} and {second} swapped places (baseline similarities "
                                     f"{want[first]:.6f} and {want[second]:.6f})")
                else:
                    problems.append(f"{second} ranks above {first}, but the baseline has {first} ahead "
                                    f"({want[first]:.6f} against {want[second]:.6f}, a gap of {gap:.6f})")

    for record_id in dict.fromkeys([*expected_ids, *actual_ids]):
        for limit in limits:
            if _near(got.get(record_id), limit) or _near(want.get(record_id), limit):
                near_threshold.append(
                    f"{record_id}: similarity {_shown(got.get(record_id))} (baseline {_shown(want.get(record_id))}) "
                    f"is within {TOLERANCE} of the threshold {limit}"
                )
    return RankedDiff(not problems, problems, tie_swaps, near_threshold)


# --- documents ----------------------------------------------------------------------------------


def check_documents(baseline: Mapping[str, str], current: Mapping[str, str]) -> list[str]:
    """Problem lines when the release's ``text_hash`` per record differs from the baseline's ``documents``:
    first the records whose hash differs, then records the release lacks, then records it has extra."""
    changed = sorted(i for i in baseline.keys() & current.keys() if baseline[i] != current[i])
    missing = sorted(baseline.keys() - current.keys())
    extra = sorted(current.keys() - baseline.keys())
    problems = []
    if changed:
        problems.append(f"text_hash differs from the baseline's documents for {len(changed)} records "
                        f"(the embedded text is not the one ChromaDB held): {_ids(changed)}")
    if missing:
        problems.append(f"{len(missing)} records of the baseline are not in the release: {_ids(missing)}")
    if extra:
        problems.append(f"{len(extra)} records of the release are not in the baseline: {_ids(extra)}")
    return problems


# --- reading the baseline -----------------------------------------------------------------------


def _read(source: ContentSource, path: str, *, compressed: bool = False) -> object:
    try:
        raw = source.read(path)
    except FileNotFoundError:
        raise ParityError(
            f"commit {source.commit} has no {path}: the baseline must be committed together with the release's code"
        ) from None
    try:
        return json.loads(gzip.decompress(raw) if compressed else raw)
    except (OSError, EOFError, zlib.error, ValueError) as exc:     # bad gzip, truncated, not JSON, not UTF-8
        raise ParityError(f"{path} at commit {source.commit} cannot be read ({type(exc).__name__})") from None


def _require(condition: bool, path: str, what: str) -> None:
    if not condition:
        raise ParityError(f"{path} is malformed: {what}")


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_hit(value: object, *fields: str) -> bool:
    return isinstance(value, dict) and isinstance(value.get("id"), str) and _is_number(value.get("sim")) and all(
        isinstance(value.get(field), str) for field in fields)


def _load_baseline(source: ContentSource) -> dict:
    baseline = _read(source, BASELINE_PATH)
    _require(isinstance(baseline, dict), BASELINE_PATH, "not a JSON object")
    for key, kind in (("environment", dict), ("documents", dict), ("gold", list), ("retrieve", list)):
        _require(isinstance(baseline.get(key), kind), BASELINE_PATH, f"'{key}' must be a {kind.__name__}")
    _require(all(isinstance(k, str) and isinstance(v, str) for k, v in baseline["documents"].items()),
             BASELINE_PATH, "'documents' must map record ids to text hashes")
    for entry in baseline["gold"]:
        _require(isinstance(entry, dict), BASELINE_PATH, "a gold entry is not an object")
        label = f"gold entry {entry.get('id')!r}"
        gq = entry.get("gq")
        _require(isinstance(entry.get("id"), str) and isinstance(entry.get("service"), str)
                 and entry["service"] in evals.RULES, BASELINE_PATH,
                 f"{label} needs a string id and a service with an eval rule")
        _require(isinstance(gq, dict) and isinstance(gq.get("id"), str) and isinstance(gq.get("query"), str),
                 BASELINE_PATH, f"{label}: 'gq' needs an id and a query")
        _require(isinstance(entry.get("top_k"), int) and entry["top_k"] > 0 and isinstance(entry.get("top"), list)
                 and all(_is_hit(hit) for hit in entry["top"]) and isinstance(entry.get("legacy_verdict"), str),
                 BASELINE_PATH, f"{label} needs top_k, top and legacy_verdict")
    for run in baseline["retrieve"]:
        _require(isinstance(run, dict), BASELINE_PATH, "a retrieve entry is not an object")
        label = f"retrieve entry {run.get('id')!r}"
        _require(isinstance(run.get("id"), str) and isinstance(run.get("queries"), list)
                 and all(isinstance(q, str) for q in run["queries"]), BASELINE_PATH, f"{label} needs an id and queries")
        _require(run.get("services") is None or (
            isinstance(run["services"], list) and all(isinstance(s, str) for s in run["services"])),
            BASELINE_PATH, f"{label}: 'services' must be null or a list")
        _require(isinstance(run.get("hits"), list) and all(_is_hit(hit, "service") for hit in run["hits"]),
                 BASELINE_PATH, f"{label} needs hits")
    return baseline


def _load_vectors(source: ContentSource) -> dict[str, list[float]]:
    vectors = _read(source, VECTORS_PATH, compressed=True)
    _require(isinstance(vectors, dict) and all(
        isinstance(v, list) and all(_is_number(x) for x in v) for v in vectors.values()),
        VECTORS_PATH, "it must map record ids to lists of numbers")
    return vectors


# --- the run ------------------------------------------------------------------------------------


def _with_problems(diff: RankedDiff, problems: list[str]) -> RankedDiff:
    return diff if not problems else replace(diff, ok=False, problems=[*diff.problems, *problems])


def run_parity(conn, release_id: int, source: ContentSource | None, embedder: Embedder) -> dict:
    """Compare release ``release_id`` with the committed ChromaDB baseline; returns the report.

    ``source`` is where the baseline files are read from, opened at the release's ``source_commit``;
    ``None`` opens that commit of the mounted repository (as ``gsn eval`` does). Raises ``ParityError``
    when there is no such release, when ``embedder`` is not the model the release was built with
    (similarities of two models cannot be compared) or when the baseline was not recorded with the
    pinned model revision; all before any search. See the module docstring for the comparison.
    """
    row = store.release_row(conn, release_id)
    if row is None:
        raise ParityError(f"release {release_id} does not exist")
    if embedder.model_id != row["embedding_model"]:
        raise ParityError(
            f"release {release_id} was built with {row['embedding_model']}, but the embedder is "
            f"{embedder.model_id}: scores from different models cannot be compared. Set GSN_EMBEDDER to "
            "match the release"
        )
    if source is None:
        source = open_repository(row["source_commit"])
    baseline = _load_baseline(source)
    revision = baseline["environment"].get("model_revision")
    if revision != BGE_M3_REVISION:
        raise ParityError(
            f"the baseline was recorded with model revision {revision!r}, but the pinned revision is "
            f"{BGE_M3_REVISION}: it cannot prove parity for the pinned model"
        )
    baseline_vectors = _load_vectors(source)

    # These two come first and decide nothing on their own: the rest is compared either way.
    document_problems = check_documents(baseline["documents"], store.member_text_hashes(conn, release_id))
    vector_problems = compare_vectors(baseline_vectors, store.stored_vectors(conn, release_id))

    def search(service_id: str, vector: list[float], k: int):
        return store.search(conn, release_id, service_id, vector, k)

    tie_swaps: list[str] = []
    near_threshold: list[str] = []

    def collect(label: str, diff: RankedDiff) -> None:
        tie_swaps.extend(f"{label}: {line}" for line in diff.tie_swaps)
        near_threshold.extend(f"{label}: {line}" for line in diff.near_threshold)

    gold_report = []
    for entry in baseline["gold"]:
        service, gq, top_k = entry["service"], entry["gq"], entry["top_k"]
        rule = evals.RULES[service]
        vector = embedder.encode([gq["query"]])[0]
        verdict = evals.judge(rule, gq, search(service, vector, top_k), top_k, service_id=service)
        expected = [(hit["id"], hit["sim"]) for hit in entry["top"][:top_k]]
        diff = compare_ranked(expected, verdict.top, [rule.ceiling, gq.get("min_similarity")])
        collect(f"gold {service}/{entry['id']}", diff)
        gold_report.append({
            "id": entry["id"], "service": service,
            "ok": diff.ok and entry["legacy_verdict"] == verdict.verdict,
            "verdict_expected": entry["legacy_verdict"], "verdict_actual": verdict.verdict, "diff": asdict(diff),
        })

    live = store.live_services(conn, release_id)
    retrieve_report = []
    for run in baseline["retrieve"]:
        hits = retrieval.retrieve(search, embedder, run["queries"], live, run["services"])
        expected = [(hit["id"], hit["sim"]) for hit in run["hits"]]
        diff = compare_ranked(expected, [(hit.record_id, hit.sim) for hit in hits],
                              [SERVICE_MIN_SIM, GROUNDING_FLOOR])
        recorded_service = {hit["id"]: hit["service"] for hit in run["hits"]}
        diff = _with_problems(diff, [
            f"{hit.record_id}: belongs to service {hit.service_id}, the baseline has {recorded_service[hit.record_id]}"
            for hit in hits
            if hit.record_id in recorded_service and recorded_service[hit.record_id] != hit.service_id
        ])
        collect(f"retrieve {run['id']}", diff)
        retrieve_report.append({"id": run["id"], "ok": diff.ok, "diff": asdict(diff)})

    verdict_mismatches = sum(e["verdict_expected"] != e["verdict_actual"] for e in gold_report)
    gold_ranked_problems = sum(not e["diff"]["ok"] for e in gold_report)
    retrieve_problems = sum(not e["ok"] for e in retrieve_report)

    problems = list(document_problems)
    if vector_problems:
        problems.append(f"vectors: {len(vector_problems)} stored vectors do not match the baseline "
                        "(see 'vectors')")
    if verdict_mismatches:
        problems.append(f"gold: {verdict_mismatches} of {len(gold_report)} verdicts differ from the legacy verdict")
    if gold_ranked_problems:
        problems.append(f"gold: {gold_ranked_problems} of {len(gold_report)} queries have hits that differ "
                        "from the baseline")
    if retrieve_problems:
        problems.append(f"retrieve: {retrieve_problems} of {len(retrieve_report)} runs select a context that "
                        "differs from the baseline")

    return {
        "ok": not problems,
        "release_id": release_id,
        "source_commit": row["source_commit"],
        "embedding_model": row["embedding_model"],
        "baseline_commit": baseline.get("commit"),
        "problems": problems,
        "summary": {
            "documents": {"compared": len(baseline["documents"]), "problems": len(document_problems)},
            "vectors": {"compared": len(baseline_vectors), "failing": len(vector_problems)},
            "gold": {"compared": len(gold_report), "verdict_mismatches": verdict_mismatches,
                     "ranked_problems": gold_ranked_problems},
            "retrieve": {"compared": len(retrieve_report), "problems": retrieve_problems},
            "tie_swaps": len(tie_swaps),
            "near_threshold": len(near_threshold),
        },
        "vectors": vector_problems,
        "gold": gold_report,
        "retrieve": retrieve_report,
        "tie_swaps": tie_swaps,
        "near_threshold": near_threshold,
    }


def format_report(report: dict) -> str:
    """The report as JSON text with sorted keys (the same report always gives the same text)."""
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
