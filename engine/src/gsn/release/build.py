"""Building a release: from one commit of the repository to a judged draft in PostgreSQL.

``build_release`` runs the steps of the design (section 6) in this order:

1. read every input at the commit (``load_inputs``) and check its integrity; any problem stops the
   build with ``integrity_failed`` and nothing is written, not even an audit row;
2. work out which record versions already exist (reused as they are) and which vectors already exist
   (copied into the new version), and embed only the texts that have neither. This happens BEFORE the
   draft is written, so no transaction waits while the CPU computes embeddings;
3. write the draft: the release, its services, the new versions and all members (``store.create_draft``)
   together with the ``release.build`` audit row, in ONE transaction, so a crash cannot leave a draft
   that the audit log does not know about (the audit insert does not touch the release row, so it cannot
   deadlock against the member guard's share lock);
4. run every gold query of every live service against the draft (``evals``) and fold the verdicts into
   the gate;
5. give the draft its verdict (``validated``, or ``rejected``; an owner override validates a failing
   release and the reason is kept) and write the matching audit row. The verdict and its audit row are
   ONE transaction, and a separate one from step 3: inserting members holds a share lock on the release
   row until the draft's transaction ends, and changing that row's status while holding it could deadlock.

A stored version is identified by the record's ``content_hash`` AND the ``fields_hash`` of the columns
derived from it (``gsn.release.fields``), per embedding model. Running the build again on the same commit,
for example after a rejected build, finds every version and vector the first run stored, so it creates a
new draft without embedding anything. If only the derivation changed (the manifest's ``verified_at_field``
mapping, a ``url_kind`` rule), the record gets a new version whose vector is copied by text hash: still
nothing is embedded.

The connection should be in autocommit mode, as ``gsn.cli`` opens it.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import psycopg

from gsn import evals, store
from gsn.embed import Embedder
from gsn.gitsource import ContentSource
from gsn.hashing import content_hash, text_hash
from gsn.release.fields import fields_hash, version_fields
from gsn.release.inputs import InputError, ReleaseInputs, load_inputs
from gsn.release.integrity import check_integrity

__all__ = [
    "BuildResult", "INTEGRITY_FAILED", "build_release", "counts_line", "dhaka_today", "evaluate",
    "format_build_summary", "listed", "report_fields", "verdict_json",
]

# Bangladesh has no daylight saving: a fixed offset is exact, and needs no time-zone database.
_DHAKA = timezone(timedelta(hours=6))

INTEGRITY_FAILED = "integrity_failed"


def dhaka_today(now: datetime | None = None) -> date:
    """Today's date in Asia/Dhaka (UTC+6). NID's ``fetched_at`` values are naive Dhaka local times, so
    the "not in the future" rule is measured against the Dhaka date, not the server's."""
    return (now or datetime.now(timezone.utc)).astimezone(_DHAKA).date()


@dataclass
class BuildResult:
    """What a build did. ``status`` is ``validated``, ``rejected`` or ``integrity_failed`` (no release
    written: ``release_id`` is ``None`` and ``problems`` lists everything wrong)."""

    release_id: int | None
    status: str
    problems: list[str]
    gate: evals.GateResult | None
    embedded: int               # texts the model embedded
    reused_versions: int        # record versions that already existed and became members as they were
    records: int = 0            # records in the release
    services: list[str] = field(default_factory=list)   # every service of the manifest, in manifest order
    copied_vectors: int = 0     # new versions that copied a stored vector instead of embedding the text
    overridden: bool = False    # validated only because the owner overrode a failing gate
    commit: str = ""
    embedding_model: str = ""
    override_reason: str | None = None  # the owner's reason, when overridden
    override_given_but_not_needed: bool = False  # --override was passed but the gate passed anyway


def _integrity_failed(problems: list[str]) -> BuildResult:
    return BuildResult(None, INTEGRITY_FAILED, list(problems), None, 0, 0)


def _services_without_eval_rule(inputs: ReleaseInputs) -> list[str]:
    return [
        f"service {entry.service_id}: live, but gsn.evals has no eval rule for it, so its gold queries cannot be judged"
        for entry in inputs.manifest
        if entry.status == "live" and entry.service_id not in evals.RULES
    ]


# --- versions and vectors -----------------------------------------------------------------------


@dataclass
class _Plan:
    member_version_ids: dict[str, int]
    new_versions: list[store.NewVersion]
    embedded: int
    copied_vectors: int


def _plan_versions(conn: psycopg.Connection, inputs: ReleaseInputs, embedder: Embedder) -> _Plan:
    """Reuse what is stored, embed what is not. Writes nothing to the database."""
    model = embedder.model_id
    todo = []
    for entry in inputs.manifest:
        for record in inputs.corpora[entry.service_id]:
            fields = version_fields(record, entry)
            todo.append((entry, record, content_hash(record), fields, fields_hash(fields), text_hash(record["text"])))
    members = store.existing_versions(conn, [(rec["id"], chash, fhash) for _, rec, chash, _, fhash, _ in todo], model)
    pending = [item for item in todo if item[1]["id"] not in members]

    vectors = store.existing_vectors(conn, [thash for *_, thash in pending], model)
    copied = sum(1 for *_, thash in pending if thash in vectors)

    texts: dict[str, str] = {}      # text_hash -> text, for the texts no stored vector covers
    for _, record, *_, thash in pending:
        if thash not in vectors:
            texts.setdefault(thash, record["text"])
    if texts:
        embedded = embedder.encode(list(texts.values()))
        if len(embedded) != len(texts):
            raise RuntimeError(f"the embedder returned {len(embedded)} vectors for {len(texts)} texts")
        vectors = {**vectors, **dict(zip(texts, embedded))}

    new_versions = [
        store.NewVersion(
            record_id=record["id"], service_id=entry.service_id, record=record, embed_text=record["text"],
            content_hash=chash, fields_hash=fhash, text_hash=thash, fields=fields, embedding=vectors[thash],
        )
        for entry, record, chash, fields, fhash, thash in pending
    ]
    return _Plan(members, new_versions, len(texts), copied)


# --- the gold queries and the report ------------------------------------------------------------


def evaluate(
    conn: psycopg.Connection,
    release_id: int,
    embedder: Embedder,
    live_services: Sequence[str],
    gold: Mapping[str, dict],
    known_failures: Mapping[str, str],
) -> tuple[list[evals.Verdict], evals.GateResult]:
    """Run the gold queries of ``live_services`` against one release and fold the verdicts into the gate."""

    def search(service_id: str, vector: list[float], k: int):
        return store.search(conn, release_id, service_id, vector, k)

    verdicts: list[evals.Verdict] = []
    for service_id in live_services:
        verdicts.extend(evals.run_gold(search, embedder, service_id, gold[service_id]))
    return verdicts, evals.gate(verdicts, known_failures)


def verdict_json(verdict: evals.Verdict) -> dict:
    return {
        "query_id": verdict.query_id,
        "service_id": verdict.service_id,
        "verdict": verdict.verdict,
        "reasons": list(verdict.reasons),
        "top": [[record_id, sim] for record_id, sim in verdict.top],
    }


def report_fields(verdicts: Sequence[evals.Verdict], result: evals.GateResult) -> dict:
    """The part of a gate report that ``build`` stores and ``gsn eval`` prints alike."""
    return {
        "passed": result.passed,
        "counts": result.counts,
        "unexpected_failures": list(result.unexpected_failures),
        "known_failures_seen": list(result.known_failures_seen),
        "verdicts": [verdict_json(v) for v in verdicts],
    }


# --- the build ----------------------------------------------------------------------------------


def build_release(
    conn: psycopg.Connection,
    source: ContentSource,
    embedder: Embedder,
    *,
    actor: str,
    note: str | None,
    override: str | None,
    today: date,
) -> BuildResult:
    """Build, judge and record one release from ``source``'s commit; see the module docstring.

    ``override`` is the owner's reason for validating a release whose gate failed; it has an effect
    only when the gate failed (a passing release is simply validated and no reason is stored).
    Errors that are not input problems propagate: ``store.StoreError`` for a server refusal, psycopg's
    own exceptions for a failing connection. ``conn`` must be in autocommit mode: the draft and the
    verdict are separate transactions on purpose (see the module docstring), which an enclosing
    transaction would silently merge.
    """
    if not conn.autocommit:
        raise ValueError("build_release needs a connection in autocommit mode")
    try:
        inputs = load_inputs(source)
    except InputError as exc:
        return _integrity_failed(exc.problems)
    problems = [
        *check_integrity(inputs, store.stored_record_services(conn), store.stored_service_names(conn), today),
        *_services_without_eval_rule(inputs),
    ]
    if problems:
        return _integrity_failed(problems)

    plan = _plan_versions(conn, inputs, embedder)
    records = sum(len(inputs.corpora[entry.service_id]) for entry in inputs.manifest)
    reused = len(plan.member_version_ids)
    model = embedder.model_id
    stats = {"records": records, "embedded": plan.embedded, "reused_versions": reused,
             "copied_vectors": plan.copied_vectors}
    draft = store.Draft(
        source_commit=inputs.commit, manifest_json=inputs.manifest_json, registry=inputs.registry,
        registry_sha256=inputs.registry_sha256, thresholds_json=inputs.thresholds_json, embedding_model=model,
        note=note, created_by=actor, services=inputs.manifest, member_version_ids=plan.member_version_ids,
        new_versions=plan.new_versions,
    )
    # One transaction: the draft and the audit row that records it stand or fall together. create_draft's own
    # transaction becomes a savepoint inside this one.
    with conn.transaction():
        release_id = store.create_draft(conn, draft)
        target = f"release:{release_id}"
        store.audit(conn, actor, "release.build", target,
                    {"commit": inputs.commit, "services": len(inputs.manifest), **stats})

    live = [entry.service_id for entry in inputs.manifest if entry.status == "live"]
    verdicts, gate = evaluate(conn, release_id, embedder, live, inputs.gold, inputs.thresholds.known_failures)

    overridden = bool(override) and not gate.passed
    status = "validated" if gate.passed or overridden else "rejected"
    override_reason = override if overridden else None
    report = {
        "commit": inputs.commit, "embedding_model": model, "integrity_problems": [], "overridden": overridden,
        "build": stats, **report_fields(verdicts, gate),
    }
    action = "release.override" if overridden else "release.validate" if status == "validated" else "release.reject"
    details = {
        "counts": gate.counts,
        "unexpected_failures": list(gate.unexpected_failures),
        "known_failures_seen": list(gate.known_failures_seen),
    }
    if overridden:
        details["override_reason"] = override_reason
    with conn.transaction():
        store.set_gate_result(conn, release_id, status, report, override_reason)
        store.audit(conn, actor, action, target, details)

    return BuildResult(
        release_id, status, [], gate, plan.embedded, reused, records=records,
        services=[entry.service_id for entry in inputs.manifest], copied_vectors=plan.copied_vectors,
        overridden=overridden, commit=inputs.commit, embedding_model=model, override_reason=override_reason,
        override_given_but_not_needed=bool(override) and gate.passed,
    )


# --- what the owner reads -----------------------------------------------------------------------


def listed(ids: Sequence[str], limit: int = 10) -> str:
    """Comma-separated ids for a summary line: at most ``limit`` of them, then how many more there are."""
    shown = ", ".join(ids[:limit])
    return shown if len(ids) <= limit else f"{shown} (and {len(ids) - limit} more; all are in the gate report)"


def counts_line(service_id: str, counts: Mapping[str, int]) -> str:
    return f"  {service_id}: " + ", ".join(f"{kind} {counts.get(kind, 0)}" for kind in evals.VERDICT_KINDS)


def format_build_summary(result: BuildResult) -> str:
    """The text ``gsn release build`` prints. Its first line is exactly ``release_id=<n> status=<status>``
    (``release_id=-`` when nothing was written): scripts parse it."""
    if result.status == INTEGRITY_FAILED:
        lines = ["release_id=- status=integrity_failed", "", f"{len(result.problems)} integrity problem(s); nothing was written:"]
        lines += [f"  - {problem}" for problem in result.problems]
        return "\n".join(lines)

    lines = [
        f"release_id={result.release_id} status={result.status}",
        f"commit: {result.commit}",
        f"embedding model: {result.embedding_model}",
        f"services: {len(result.services)} ({', '.join(result.services)})",
        f"records: {result.records}",
        f"embedded: {result.embedded}",
        f"reused: {result.reused_versions} versions kept as they were, {result.copied_vectors} vectors copied",
    ]
    gate = result.gate
    if gate is not None:
        lines.append("verdicts:")
        lines += [counts_line(sid, counts) for sid, counts in gate.counts.items()]
        if gate.known_failures_seen:
            lines.append(f"known failures (accepted): {listed(gate.known_failures_seen)}")
        if gate.unexpected_failures:
            lines.append(f"unexpected failures: {listed(gate.unexpected_failures)}")
    if result.status == "rejected":
        lines.append(
            f"gate: FAILED - release {result.release_id} is rejected and cannot be published; fix the data and "
            "build again (every version and vector already stored is reused), or build with --override REASON "
            "if you have checked the failures"
        )
    elif result.overridden:
        lines.append(
            f"gate: FAILED, validated by override (reason: {result.override_reason}); "
            f"stored on release {result.release_id}"
        )
    else:
        lines.append("gate: passed")
        if result.override_given_but_not_needed:
            lines.append("override not needed: the gate passed (no override recorded)")
    if result.status == "validated":
        publish = f"docker compose --profile tools run --rm release release publish {result.release_id}"
        if result.embedding_model.startswith("fake-"):
            publish += " --allow-fake"  # a fake-embedder release is refused without it (tests only)
        lines.append(f"publish it with: {publish}")
    return "\n".join(lines)
