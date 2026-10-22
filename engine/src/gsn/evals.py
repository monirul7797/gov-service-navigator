"""Gold-query evals: per-service verdict rules and the release gate.

``judge`` is a port of the ``eval_one`` function of the four legacy scripts (``eval_nid.py``,
``eval_epassport.py``, ``eval_ereturn.py``, ``eval_bdris.py``). The verdicts must not differ from
theirs: the parity task replays the recorded ChromaDB baseline through ``judge`` and compares every
verdict. Only the reason strings are free to differ.

What the four scripts do, in this order, for one gold query (``gq``) and the top-k hits:

1. *Out-of-scope probe* - no gate field (``expected_category`` / ``expected_subcategory``), no
   ``expected_id`` and no ``min_similarity`` (BDRIS: any null ``expected_category``). PASS if the best
   hit stays under the service's ceiling, FAIL if it reaches it.
2. *Observational probe* (not BDRIS) - no gate field and no ``expected_id`` but a ``min_similarity``:
   OBSERVE. Reported, never fails.
3. *Gate field* - the expected category (e-Return: subcategory) must occur among the top-k hits,
   else FAIL.
4. *Record* - a non-empty ``expected_id`` must be among the top-k hits, else FAIL.
5. *Soft floor* - ``min_similarity`` above the best relevant similarity downgrades PASS to
   ``PASS (warn)``; it never fails.

The e-Return script assumes the subcategory metadata is stored (its "metadata missing" fallback
only skipped the gate for stores that lacked it); the release always stores it.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from gsn.retrieval import Hit, SearchFn, _Encoder

PASS = "PASS"
WARN = "PASS (warn)"
OBSERVE = "OBSERVE"
FAIL = "FAIL"
VERDICT_KINDS = (PASS, WARN, OBSERVE, FAIL)

DEFAULT_TOP_K = 4


@dataclass(frozen=True)
class Rule:
    """How one service's gold queries are judged."""

    gate_field: str     # gold-query key holding the expected category / subcategory
    meta_field: str     # the Hit attribute it is compared with
    ceiling: float      # an out-of-scope probe fails if the best hit reaches this similarity
    observe: bool       # False: no observational probe; a null gate field is always out-of-scope


RULES: dict[str, Rule] = {
    "nid": Rule("expected_category", "category", 0.55, True),
    "epassport": Rule("expected_category", "category", 0.55, True),
    "ereturn": Rule("expected_subcategory", "subcategory", 0.55, True),
    "bdris": Rule("expected_category", "category", 0.62, False),
}

# eval_ereturn.py runs its gate only when a subcategory is expected (``if exp_sub is not None``);
# the other scripts run theirs unconditionally, so a null category together with an expected_id
# is searched for as ``None`` among the hits' categories and fails. Both behaviours are kept.
_GATE_NEEDS_A_VALUE = frozenset({"expected_subcategory"})


@dataclass(frozen=True)
class Verdict:
    """The outcome of one gold query. ``top`` is ``(record_id, similarity)`` for the top-k hits."""

    query_id: str
    service_id: str
    verdict: str        # one of VERDICT_KINDS
    reasons: list[str]
    top: list[tuple[str, float]]


def _is_out_of_scope(rule: Rule, gate_value: object, expected_id: object, min_sim: object) -> bool:
    if gate_value is not None:
        return False
    return not rule.observe or (expected_id is None and min_sim is None)


def judge(rule: Rule, gq: dict, hits: list[Hit], top_k: int, *, service_id: str = "") -> Verdict:
    """The verdict for one gold query, given the hits the search returned for it (best first).

    Pure: no I/O. ``service_id`` is only copied into the verdict.
    """
    shown = hits[:top_k]
    ids = [h.record_id for h in shown]
    sims = [h.sim for h in shown]
    gate_value = gq.get(rule.gate_field)
    expected_id = gq.get("expected_id")
    min_sim = gq.get("min_similarity")
    top = [(h.record_id, h.sim) for h in shown]

    def verdict(kind: str, *reasons: str) -> Verdict:
        return Verdict(gq["id"], service_id, kind, list(reasons), top)

    best_sim = sims[0] if sims else 0.0

    # 1. out-of-scope probe: nothing should clear the ceiling
    if _is_out_of_scope(rule, gate_value, expected_id, min_sim):
        if best_sim < rule.ceiling:
            return verdict(PASS, f"correctly found no strong match (best sim={best_sim:.3f} < {rule.ceiling})")
        return verdict(FAIL, f"expected no strong match, but got sim={best_sim:.3f} >= {rule.ceiling} "
                             f"(top hit: {ids[0]})")

    # 2. observational probe: reported, never gated
    if rule.observe and gate_value is None and expected_id is None:
        return verdict(OBSERVE, f"observational only - top sim={best_sim:.3f}, top id={ids[0] if ids else 'none'}")

    # 3. gate field (category / subcategory) among the top-k hits
    present = {getattr(h, rule.meta_field) for h in shown}
    if (gate_value is not None or rule.gate_field not in _GATE_NEEDS_A_VALUE) and gate_value not in present:
        return verdict(FAIL, f"expected {rule.meta_field} '{gate_value}' not in top-{top_k} "
                             f"(got: {sorted(p for p in present if p)})")

    # 4. record
    if expected_id:
        if expected_id not in ids:
            return verdict(FAIL, f"{rule.meta_field} matched but expected record '{expected_id}' "
                                 f"not in top-{top_k} (got ids: {ids})")
        reason = f"expected record '{expected_id}' found"
    else:
        reason = f"{rule.meta_field}-level match only (expected_id not set) - OK"

    # 5. soft similarity floor
    if min_sim is not None:
        best_relevant = max((h.sim for h in shown if h.record_id == expected_id or not expected_id),
                            default=best_sim)
        if best_relevant < min_sim:
            return verdict(WARN, reason, f"similarity {best_relevant:.3f} below expected floor {min_sim} "
                                         "- likely thin coverage, not broken retrieval")
    return verdict(PASS, reason)


def run_gold(search: SearchFn, embedder: _Encoder, service_id: str, gold: dict) -> list[Verdict]:
    """Judge every query of a service's gold file: one embedding and one search per query."""
    rule = RULES.get(service_id)
    if rule is None:
        raise ValueError(f"no eval rule for service {service_id!r}")
    top_k = gold.get("top_k", DEFAULT_TOP_K)
    verdicts = []
    for gq in gold["queries"]:
        vector = embedder.encode([gq["query"]])[0]
        verdicts.append(judge(rule, gq, search(service_id, vector, top_k), top_k, service_id=service_id))
    return verdicts


@dataclass(frozen=True)
class GateResult:
    """The release gate: ``passed`` unless a query FAILs that is not a known failure."""

    passed: bool
    unexpected_failures: list[str]      # query ids that FAIL and are not in known_failures
    known_failures_seen: list[str]      # query ids that FAIL and are listed in known_failures
    counts: dict[str, dict[str, int]]   # service id -> verdict kind -> number of queries


def gate(verdicts: Iterable[Verdict], known_failures: Mapping[str, str]) -> GateResult:
    """Fold verdicts into the gate result. Only FAIL blocks; ``PASS (warn)`` and OBSERVE never do."""
    counts: dict[str, dict[str, int]] = {}
    unexpected: list[str] = []
    known: list[str] = []
    for v in verdicts:
        counts.setdefault(v.service_id, dict.fromkeys(VERDICT_KINDS, 0))[v.verdict] += 1
        if v.verdict == FAIL:
            (known if v.query_id in known_failures else unexpected).append(v.query_id)
    return GateResult(not unexpected, unexpected, known, counts)
