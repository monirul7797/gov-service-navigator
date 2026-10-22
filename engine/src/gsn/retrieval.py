"""Multi-service retrieval selection: a port of ``answer.retrieve()`` (its ``queries=`` path).

The legacy function searched one ChromaDB collection per service. Here the search itself is injected
(``SearchFn``, backed by PostgreSQL in production and by hand-made hits in tests), so this module holds
only the selection rules that decide which records reach the answer model:

* every stripped, non-empty, distinct query is embedded once and searched in every scoped service;
* per service, each record keeps its best similarity across the queries;
* a service contributes only if its best record reaches ``SERVICE_MIN_SIM``, and then only its top
  ``per_service_k`` records, so no service crowds the others out;
* the contributions are merged best-first and cut to ``max_context``;
* when the caller narrowed the search to exactly one service there is no crowding to guard against,
  so more of that service's records are pulled (``SCOPED_TOP_K``).

``gsn`` never imports the legacy root modules; the constants below are copies of the ones in
``answer.py`` and must change together with them (the parity task compares the two systems' results).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

CANDIDATE_N = 8           # pulled per service per query
PER_SERVICE_K = 2         # records kept per qualifying service, so no service crowds the others out
SERVICE_MIN_SIM = 0.40    # a service must have a hit at least this strong to contribute context
MAX_CONTEXT = 6           # cap on the total records fed to the model
SCOPED_TOP_K = 8          # when the search is scoped to ONE service, pull more of its records (no
                          # crowding risk) so multi-record answers, e.g. a fee table split across
                          # several records, come through complete
GROUNDING_FLOOR = 0.42    # if the best hit is below this, decline instead of guessing


@dataclass(frozen=True)
class Hit:
    """One retrieved record: what the answer model and the evals need to know about it."""

    record_id: str
    service_id: str
    sim: float                  # cosine similarity (1 - cosine distance); higher is closer
    text: str
    category: str | None
    subcategory: str | None
    heading: str | None
    source_url: str


class _Encoder(Protocol):
    """The part of an embedder retrieval needs: query texts in, one vector per text out."""

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


# (service_id, query vector, k) -> at most k hits of that service, best first.
SearchFn = Callable[[str, list[float], int], list[Hit]]


def _distinct_queries(queries: Sequence[str]) -> list[str]:
    """Stripped, non-empty, de-duplicated queries, in their original order."""
    return list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))


def _scope(live_services: Sequence[str], services: Sequence[str] | None) -> tuple[list[str], bool]:
    """The services to search and whether the caller narrowed the search to some of them.

    Requested services we do not serve (unknown, or not live) are ignored; if none is left the
    search is not narrowed and covers every live service.
    """
    live = list(dict.fromkeys(live_services))
    if services:
        requested = [s for s in dict.fromkeys(services) if s in live]
        if requested:
            return requested, True
    return live, False


def _rank_key(hit: Hit) -> tuple[float, str]:
    """Best similarity first; equal similarities in record-id order, so the result is deterministic."""
    return (-hit.sim, hit.record_id)


def retrieve(
    search: SearchFn,
    embedder: _Encoder,
    queries: Sequence[str],
    live_services: Sequence[str],
    services: Sequence[str] | None = None,
) -> list[Hit]:
    """The records to ground an answer in, best first (at most ``MAX_CONTEXT``, or ``SCOPED_TOP_K``
    when ``services`` narrows the search to exactly one live service)."""
    scope, narrowed = _scope(live_services, services)
    per_service_k, max_context, candidate_n = PER_SERVICE_K, MAX_CONTEXT, CANDIDATE_N
    if narrowed and len(scope) == 1:
        per_service_k = max_context = SCOPED_TOP_K
        candidate_n = max(CANDIDATE_N, SCOPED_TOP_K + 4)

    best: dict[str, dict[str, Hit]] = {}          # service -> record id -> its best hit across queries
    for query in _distinct_queries(queries):
        vector = embedder.encode([query])[0]
        for service in scope:
            bucket = best.setdefault(service, {})
            for hit in search(service, vector, candidate_n):
                kept = bucket.get(hit.record_id)
                if kept is None or hit.sim > kept.sim:
                    bucket[hit.record_id] = hit

    chosen: list[Hit] = []
    for bucket in best.values():
        ranked = sorted(bucket.values(), key=_rank_key)
        if ranked and ranked[0].sim >= SERVICE_MIN_SIM:      # only relevant services
            chosen.extend(ranked[:per_service_k])            # each gets representation
    return sorted(chosen, key=_rank_key)[:max_context]


def grounded(hits: Sequence[Hit]) -> bool:
    """True if the best hit is strong enough to answer from; otherwise the bot should decline."""
    return bool(hits) and hits[0].sim >= GROUNDING_FLOOR
