"""Export what the legacy ChromaDB code returns today, as a committed baseline (milestone M1, task D1).

Before ChromaDB is retired, the PostgreSQL search has to be shown to return the same results. This
script records the "before" picture: for every gold query, the hits the legacy code produces and the
verdict the legacy eval script gives, plus the multi-service ``answer.retrieve`` selections, plus
every stored vector. A later task compares a PostgreSQL release against this file, query by query.

Run it with the GLOBAL Python (the one that has chromadb, sentence-transformers and torch - not the
``.venv``), from the repository root:

    $env:PYTHONIOENCODING = "utf-8"
    python tools/m1/export_chroma_baseline.py --out docs/reports/m1-parity

What it does, in order (nothing is written until every step has succeeded):

1. Freshness: the documents stored in the four collections must equal the corpus committed at
   ``HEAD`` (``git show HEAD:data/chunks/<service>.jsonl``). Any difference prints the differing
   record ids and exits with status 1: a baseline of an out-of-date index would prove nothing.
2. Loads bge-m3 (cached copy only: ``HF_HUB_OFFLINE`` defaults to 1, so a changed upstream revision
   can never replace the one the baseline records) and notes the environment: package versions, the
   commit, the model snapshot directory and weight file actually loaded.
3. Gold queries: each is queried exactly the way its legacy ``eval_*.py`` queries (NID, e-Passport and
   e-Return embed the query and pass ``query_embeddings``; BDRIS passes ``query_texts`` through its
   embedding-function collection). The hits are recorded with their unrounded similarity
   (``1 - distance``) and metadata, next to the verdict that script's own ``eval_one`` gives. The
   recorded hits are cross-checked against that ``eval_one``'s own ``top_hits``.
4. Retrieve runs: ``answer.retrieve`` with ``queries=[query]`` for every gold query, once over all
   services and once scoped to the query's own service, plus every case of ``retrieve_cases.json``.
5. Writes ``chroma_baseline.json`` and ``chroma_vectors.json.gz`` into ``--out``.

The legacy root modules are imported only inside the functions that need them, so the pure helpers
here can be unit-tested in ``.venv`` (``tools/tests/test_export_chroma_baseline.py``). This file must
not import ``gsn``: it computes the text hash inline, and a test pins it to ``gsn.hashing.text_hash``.
"""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PARITY_DIR = REPO_ROOT / "docs" / "reports" / "m1-parity"
CASES_FILE = PARITY_DIR / "retrieve_cases.json"

SERVICES = ("nid", "epassport", "ereturn", "bdris")
DEFAULT_TOP_K = 4
BASELINE_NAME = "chroma_baseline.json"
VECTORS_NAME = "chroma_vectors.json.gz"

# the package versions the environment block records (distribution names, as `pip freeze` prints them)
VERSION_PACKAGES = ("torch", "sentence-transformers", "transformers", "tokenizers", "chromadb", "numpy")
# transformers prefers safetensors weights over a pickled .bin when both are present
WEIGHT_FILES = ("model.safetensors", "pytorch_model.bin")

# which hit attribute each legacy script reports in its ``top_hits`` (e-Return reports subcategory)
LEGACY_META_FIELD = {"nid": "category", "epassport": "category", "ereturn": "subcategory", "bdris": "category"}
# the legacy scripts round similarities to 3 decimals; allow that rounding plus float noise
LEGACY_SIM_TOLERANCE = 0.0006

MIN_CASE_QUERIES, MAX_CASE_QUERIES = 2, 3
_JSON_WHITESPACE = " \t\r"


# ------------------------------------------------------------------------------------------ hashing

def text_hash(text: str) -> str:
    """SHA-256 (lowercase hex) of the UTF-8 text. Same as ``gsn.hashing.text_hash`` (a test pins it)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------- corpus and freshness

def parse_corpus(raw: bytes, path: str) -> dict[str, str]:
    """``{record id: text}`` of a JSON-lines corpus file. BOM, CRLF and blank lines are tolerated.

    Lines are split on ``\\n`` only (``splitlines`` would also split inside a record on U+2028).
    Raises ``ValueError`` (naming ``path:line``) for invalid JSON, a record without a string ``id`` and
    ``text``, or a repeated id.
    """
    documents: dict[str, str] = {}
    for number, line in enumerate(raw.decode("utf-8-sig").split("\n"), start=1):
        line = line.strip(_JSON_WHITESPACE)
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path}:{number}: expected a JSON object")
        record_id, text = record.get("id"), record.get("text")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError(f"{path}:{number}: record has no string 'id'")
        if not isinstance(text, str):
            raise ValueError(f"{path}:{number}: record {record_id!r} has no string 'text'")
        if record_id in documents:
            raise ValueError(f"{path}:{number}: duplicate record id {record_id!r}")
        documents[record_id] = text
    return documents


@dataclasses.dataclass(frozen=True)
class DocumentDiff:
    """How the committed corpus differs from the documents stored in ChromaDB (both ``{id: text}``)."""

    added: list[str]      # in the committed corpus, not in ChromaDB
    removed: list[str]    # in ChromaDB, no longer in the committed corpus
    changed: list[str]    # in both, with different text

    @property
    def is_clean(self) -> bool:
        return not (self.added or self.removed or self.changed)


def diff_documents(stored: Mapping[str, str], committed: Mapping[str, str]) -> DocumentDiff:
    """Compare ChromaDB's stored documents with the committed corpus; every list is sorted by id."""
    return DocumentDiff(
        added=sorted(committed.keys() - stored.keys()),
        removed=sorted(stored.keys() - committed.keys()),
        changed=sorted(i for i in stored.keys() & committed.keys() if stored[i] != committed[i]),
    )


def freshness_problems(
    stored: Mapping[str, Mapping[str, str]], committed: Mapping[str, Mapping[str, str]]
) -> list[str]:
    """One line per difference between ChromaDB and the committed corpus, per service; empty if in sync."""
    problems: list[str] = []
    services = [s for s in SERVICES if s in stored or s in committed]
    services += sorted((set(stored) | set(committed)) - set(SERVICES))
    for service in services:
        if service not in stored:
            problems.append(f"{service}: no such collection in ChromaDB")
            continue
        if service not in committed:
            problems.append(f"{service}: no corpus file in the committed tree")
            continue
        diff = diff_documents(stored[service], committed[service])
        if diff.added:
            problems.append(f"{service}: in the committed corpus but not in ChromaDB: {', '.join(diff.added)}")
        if diff.removed:
            problems.append(f"{service}: in ChromaDB but not in the committed corpus: {', '.join(diff.removed)}")
        if diff.changed:
            problems.append(f"{service}: text differs between ChromaDB and the committed corpus: {', '.join(diff.changed)}")
    return problems


def _merge_by_record_id(per_service: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    owner: dict[str, str] = {}
    for service, records in per_service.items():
        for record_id, value in records.items():
            if record_id in merged:
                raise ValueError(f"record id {record_id!r} is used by two services ({owner[record_id]} and {service})")
            merged[record_id] = value
            owner[record_id] = service
    return dict(sorted(merged.items()))


def document_hashes(documents: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    """``{record id: text hash}`` across all services, sorted by id. Refuses an id used by two services."""
    return {i: text_hash(t) for i, t in _merge_by_record_id(documents).items()}


def merge_vectors(vectors: Mapping[str, Mapping[str, list[float]]]) -> dict[str, list[float]]:
    """``{record id: vector}`` across all services, sorted by id. Refuses an id used by two services."""
    return _merge_by_record_id(vectors)


def to_float_list(vector: Any) -> list[float]:
    """A plain list of Python floats from a numpy array (``tolist``) or any sequence of numbers."""
    if hasattr(vector, "tolist"):
        return vector.tolist()
    return [float(x) for x in vector]


# --------------------------------------------------------------------------------- query results

def shape_hits(result: Mapping[str, Any]) -> list[dict]:
    """The hits of a one-query ``collection.query`` result, best first, as recorded in the baseline.

    ``sim`` is ``1 - distance`` exactly as every legacy script computes it - not rounded. A hit
    without metadata, or without a ``category`` / ``subcategory``, records ``null`` for them.
    """
    hits = []
    for record_id, meta, distance in zip(result["ids"][0], result["metadatas"][0], result["distances"][0]):
        meta = meta or {}
        hits.append({
            "id": record_id,
            "sim": float(1 - distance),
            "category": meta.get("category"),
            "subcategory": meta.get("subcategory"),
        })
    return hits


def check_against_legacy(service: str, query_id: str, hits: Sequence[Mapping], legacy_top_hits: Iterable[Sequence]) -> None:
    """Raise ``RuntimeError`` unless the recorded hits are the ones the legacy ``eval_one`` saw.

    ``legacy_top_hits`` is that function's ``top_hits``: ``(id, category-or-subcategory, rounded sim)``
    per hit. Ids, order and metadata must be equal; similarities equal up to the legacy rounding.
    """
    field = LEGACY_META_FIELD[service]
    legacy = [tuple(t) for t in legacy_top_hits]
    problems = []
    if len(legacy) != len(hits):
        problems.append(f"{len(hits)} hits recorded, eval_one saw {len(legacy)}")
    for position, (hit, seen) in enumerate(zip(hits, legacy)):
        legacy_id, legacy_meta, legacy_sim = seen
        if hit["id"] != legacy_id:
            problems.append(f"hit {position}: recorded {hit['id']!r}, eval_one saw {legacy_id!r}")
        elif hit[field] != legacy_meta:
            problems.append(f"hit {position} ({legacy_id}): {field} recorded {hit[field]!r}, eval_one saw {legacy_meta!r}")
        elif abs(hit["sim"] - legacy_sim) > LEGACY_SIM_TOLERANCE:
            problems.append(f"hit {position} ({legacy_id}): sim recorded {hit['sim']!r}, eval_one saw {legacy_sim!r}")
    if problems:
        raise RuntimeError(f"{query_id}: the recorded query and the legacy eval_one disagree: " + "; ".join(problems))


def gold_entry(
    service: str,
    gq: Mapping,
    top_k: int,
    hits: Sequence[Mapping],
    legacy: Mapping,
    subcategory_available: bool | None = None,
) -> dict:
    """One ``gold`` baseline entry: the query, its recorded hits and the legacy verdict.

    ``legacy`` is the dict the legacy ``eval_one`` returned. ``subcategory_available`` is recorded
    only for e-Return (pass ``None`` for the other services).
    """
    entry = {
        "service": service,
        "id": gq["id"],
        "gq": dict(gq),
        "top_k": top_k,
        "top": [dict(h) for h in hits],
        "legacy_verdict": legacy["verdict"],
        "legacy_reasons": list(legacy["reasons"]),
    }
    if subcategory_available is not None:
        entry["subcategory_available"] = subcategory_available
    return entry


def retrieve_entry(
    entry_id: str, source: str, queries: Sequence[str], services: Sequence[str] | None, selected: Iterable[Mapping]
) -> dict:
    """One ``retrieve`` baseline entry: what ``answer.retrieve`` selected, in order, with unrounded sims."""
    return {
        "id": entry_id,
        "source": source,
        "queries": list(queries),
        "services": None if services is None else list(services),
        "hits": [{"id": h["id"], "service": h["service"], "sim": h["sim"]} for h in selected],
    }


def assemble_baseline(commit: str, environment: Mapping, documents: Mapping, gold: list, retrieve: list) -> dict:
    return {
        "commit": commit,
        "environment": dict(environment),
        "documents": dict(documents),
        "gold": gold,
        "retrieve": retrieve,
    }


def _write_atomically(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def write_outputs(out_dir: Path, baseline: Mapping, vectors: Mapping[str, list[float]]) -> None:
    """Write ``chroma_baseline.json`` (UTF-8, indent 1) and ``chroma_vectors.json.gz`` into ``out_dir``.

    Neither file contains a timestamp or a file name, so the same input gives the same bytes.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(baseline, ensure_ascii=False, indent=1) + "\n"
    _write_atomically(out_dir / BASELINE_NAME, text.encode("utf-8"))
    payload = json.dumps(vectors, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _write_atomically(out_dir / VECTORS_NAME, gzip.compress(payload, mtime=0))


# ------------------------------------------------------------------------ retrieve cases (inputs)

def validate_cases(cases: Any, gold_queries: Mapping[str, Sequence[str]]) -> None:
    """Raise ``ValueError`` unless ``cases`` is a list of well-formed retrieve cases.

    A case is ``{"id", "queries", "services"}``: a unique id, 2-3 queries that are copied verbatim from
    some service's gold file, and a non-empty list of distinct services (keys of ``gold_queries``).
    """
    if not isinstance(cases, list) or not cases:
        raise ValueError("retrieve cases must be a non-empty list")
    known_queries = {q for queries in gold_queries.values() for q in queries}
    seen_ids: set[str] = set()
    for position, case in enumerate(cases):
        where = f"case #{position + 1}"
        if not isinstance(case, dict):
            raise ValueError(f"{where}: expected an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"{where}: missing id")
        where = f"case {case_id}"
        if case_id in seen_ids:
            raise ValueError(f"{where}: duplicate id")
        seen_ids.add(case_id)
        queries = case.get("queries")
        if not isinstance(queries, list) or not MIN_CASE_QUERIES <= len(queries) <= MAX_CASE_QUERIES:
            raise ValueError(f"{where}: needs {MIN_CASE_QUERIES}-{MAX_CASE_QUERIES} queries")
        for query in queries:
            if query not in known_queries:
                raise ValueError(f"{where}: query is not copied verbatim from a gold query: {query!r}")
        services = case.get("services")
        if not isinstance(services, list) or not services or len(set(services)) != len(services):
            raise ValueError(f"{where}: needs a non-empty list of distinct services")
        for service in services:
            if service not in gold_queries:
                raise ValueError(f"{where}: unknown service {service!r}")


# --------------------------------------------------------------------------------------- the model

def package_versions(names: Iterable[str], lookup: Callable[[str], str] = importlib.metadata.version) -> dict[str, str]:
    return {name: lookup(name) for name in names}


def _cached_snapshot(repo_id: str, revision: str) -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id, revision=revision, local_files_only=True)


def model_snapshot_dir(model: Any, *, resolver: Callable[[str, str], str] | None = None) -> Path:
    """The directory the loaded sentence-transformers model's files came from.

    Read from the loaded model, not assumed: a model loaded from a local path reports that path; one
    loaded from the Hub reports its repo id and the exact revision (commit hash) it resolved, which is
    looked up in the local cache (``resolver``). Raises ``RuntimeError`` rather than guess.
    """
    config = model[0].auto_model.config
    name = getattr(config, "_name_or_path", None)
    if name and Path(name).is_absolute() and Path(name).is_dir():
        return Path(name)
    revision = getattr(config, "_commit_hash", None)
    if not name or not revision:
        raise RuntimeError(f"cannot tell which model revision was loaded (name {name!r}, revision {revision!r})")
    directory = Path((resolver or _cached_snapshot)(name, revision))
    if not directory.is_dir():
        raise RuntimeError(f"the cached snapshot of {name}@{revision} is not a directory: {directory}")
    return directory


def find_weight_file(snapshot_dir: Path) -> Path:
    """The weight file transformers loads from ``snapshot_dir`` (safetensors before pytorch .bin)."""
    for name in WEIGHT_FILES:
        candidate = snapshot_dir / name
        if candidate.exists():
            return candidate
    raise RuntimeError(f"no weight file ({' or '.join(WEIGHT_FILES)}) in {snapshot_dir}")


def collect_environment(model: Any, commit: str) -> dict:
    """The environment block: versions, the model files actually loaded, and the repository commit."""
    snapshot = model_snapshot_dir(model)
    weights = find_weight_file(snapshot)
    environment: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        **package_versions(VERSION_PACKAGES),
        "device": str(model.device),
        "hf_hub_offline": os.environ.get("HF_HUB_OFFLINE"),
        "model_name": model[0].auto_model.config._name_or_path,
        "model_snapshot_dir": str(snapshot),
        "model_weight_file": str(weights),
        "model_weight_file_bytes": weights.stat().st_size,
        "git_head": commit,
    }
    return environment


# ----------------------------------------------------------------------------------------- git

def _git(root: Path, *args: str) -> bytes:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=env, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def git_head(root: Path) -> str:
    """``git rev-parse HEAD`` (the full 40-hex commit id)."""
    commit = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise RuntimeError(f"unexpected output from git rev-parse HEAD: {commit!r}")
    return commit


def read_committed_corpora(root: Path, commit: str) -> dict[str, dict[str, str]]:
    """``{service: {record id: text}}`` of ``data/chunks/<service>.jsonl`` as committed at ``commit``."""
    corpora = {}
    for service in SERVICES:
        path = f"data/chunks/{service}.jsonl"
        corpora[service] = parse_corpus(_git(root, "show", f"{commit}:{path}"), path)
    return corpora


# ---------------------------------------------------------------- legacy code and ChromaDB (global Python)

def import_legacy(root: Path) -> SimpleNamespace:
    """Import the legacy root modules (from ``root``) and return them as ``answer``, ``eval_nid``, ..."""
    # Never let a changed upstream model revision replace the cached one this baseline records.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    names = ("answer", *(f"eval_{s}" for s in SERVICES))
    return SimpleNamespace(**{name: importlib.import_module(name) for name in names})


@dataclasses.dataclass
class Stored:
    """What is in ChromaDB: the searchable collections, and every document and vector by service."""

    colls: dict
    documents: dict[str, dict[str, str]]
    vectors: dict[str, dict[str, list[float]]]


def load_stored(root: Path) -> Stored:
    """Open the vector store the way ``answer.py`` does and read every document and embedding."""
    legacy = import_legacy(root)    # first: it sets HF_HUB_OFFLINE before any Hugging Face package loads
    import chromadb

    client = chromadb.PersistentClient(path=str(root / legacy.answer.DB_DIR))
    colls = legacy.answer.load_collections(client)
    missing = [s for s in SERVICES if s not in colls]
    if missing:
        raise RuntimeError(f"collections missing from the vector store: {', '.join(missing)}")
    documents: dict[str, dict[str, str]] = {}
    vectors: dict[str, dict[str, list[float]]] = {}
    for service in SERVICES:
        got = colls[service].get(include=["documents", "embeddings"])
        ids, texts, embeddings = got["ids"], got["documents"], got["embeddings"]
        if embeddings is None or not len(ids) == len(texts) == len(embeddings):
            raise RuntimeError(f"{service}: ChromaDB returned inconsistent ids, documents and embeddings")
        documents[service] = dict(zip(ids, texts))
        vectors[service] = {i: to_float_list(e) for i, e in zip(ids, embeddings)}
    return Stored(colls, documents, vectors)


def load_model(root: Path) -> Any:
    """The embedding model, loaded as ``answer.py`` loads it."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(import_legacy(root).answer.MODEL_NAME)


def load_gold_files(root: Path) -> dict[str, dict]:
    return {s: json.loads((root / f"{s}_gold_queries.json").read_text(encoding="utf-8")) for s in SERVICES}


def _query_like_legacy(service: str, module: Any, model: Any, collection: Any, gq: Mapping, top_k: int) -> dict:
    if service == "bdris":
        return collection.query(query_texts=[gq["query"]], n_results=top_k)
    embedding = model.encode([module.as_query(gq["query"])], normalize_embeddings=True)
    return collection.query(
        query_embeddings=[embedding[0].tolist()], n_results=top_k, include=["documents", "metadatas", "distances"]
    )


def _legacy_verdict(service: str, module: Any, model: Any, collection: Any, gq: Mapping, top_k: int, subcategory_ok: bool | None) -> dict:
    if service == "bdris":
        return module.eval_one(collection, gq, top_k)
    if service == "ereturn":
        return module.eval_one(model, collection, gq, top_k, subcategory_ok)
    return module.eval_one(model, collection, gq, top_k)


def run_gold(model: Any, legacy: SimpleNamespace, colls: Mapping, gold_files: Mapping[str, dict]) -> list[dict]:
    entries = []
    for service in SERVICES:
        module = getattr(legacy, f"eval_{service}")
        collection = module.get_collection() if service == "bdris" else colls[service]
        subcategory_ok = module.subcategory_available(collection) if service == "ereturn" else None
        gold = gold_files[service]
        top_k = gold.get("top_k", DEFAULT_TOP_K)
        for gq in gold["queries"]:
            hits = shape_hits(_query_like_legacy(service, module, model, collection, gq, top_k))
            result = _legacy_verdict(service, module, model, collection, gq, top_k, subcategory_ok)
            check_against_legacy(service, gq["id"], hits, result["top_hits"])
            entries.append(gold_entry(service, gq, top_k, hits, result, subcategory_ok))
        print(f"  gold {service}: {len(gold['queries'])} queries", flush=True)
    return entries


def run_retrieve(model: Any, legacy: SimpleNamespace, colls: Mapping, gold: Sequence[Mapping], cases: Sequence[Mapping]) -> list[dict]:
    def run(entry_id: str, source: str, queries: list[str], services: list[str] | None) -> dict:
        _, selected = legacy.answer.retrieve(queries[0], None, model, colls, queries=queries, services=services)
        return retrieve_entry(entry_id, source, queries, services, selected)

    entries = []
    for entry in gold:
        service, query = entry["service"], entry["gq"]["query"]
        entries.append(run(f"{service}/{entry['id']}/all", "gold", [query], None))
        entries.append(run(f"{service}/{entry['id']}/scoped", "gold", [query], [service]))
    for case in cases:
        entries.append(run(case["id"], "case", list(case["queries"]), list(case["services"])))
    print(f"  retrieve: {len(entries)} runs ({len(cases)} cases)", flush=True)
    return entries


# ------------------------------------------------------------------------------------------- main

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the legacy ChromaDB behaviour as the M1 parity baseline.")
    parser.add_argument("--out", required=True, help="directory for chroma_baseline.json and chroma_vectors.json.gz")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = REPO_ROOT

    commit = git_head(root)
    committed = read_committed_corpora(root, commit)
    stored = load_stored(root)
    problems = freshness_problems(stored.documents, committed)
    if problems:
        print(f"ChromaDB is not in sync with the corpus committed at {commit}; nothing was written:")
        for problem in problems:
            print(f"  {problem}")
        return 1

    gold_files = load_gold_files(root)
    try:
        cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
        validate_cases(cases, {s: [gq["query"] for gq in g["queries"]] for s, g in gold_files.items()})
    except (OSError, ValueError) as exc:
        print(f"{CASES_FILE}: {exc}")
        return 2

    print(f"freshness OK at {commit}; loading the model...", flush=True)
    model = load_model(root)
    environment = collect_environment(model, commit)
    legacy = import_legacy(root)
    gold = run_gold(model, legacy, stored.colls, gold_files)
    retrieve = run_retrieve(model, legacy, stored.colls, gold, cases)

    baseline = assemble_baseline(commit, environment, document_hashes(stored.documents), gold, retrieve)
    vectors = merge_vectors(stored.vectors)
    write_outputs(Path(args.out), baseline, vectors)

    verdicts: dict[str, int] = {}
    for entry in gold:
        verdicts[entry["legacy_verdict"]] = verdicts.get(entry["legacy_verdict"], 0) + 1
    print(f"wrote {BASELINE_NAME} and {VECTORS_NAME} to {args.out}")
    print(f"  documents {len(baseline['documents'])}, vectors {len(vectors)}, gold {len(gold)}, retrieve {len(retrieve)}")
    print("  legacy verdicts: " + ", ".join(f"{k} {v}" for k, v in sorted(verdicts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
