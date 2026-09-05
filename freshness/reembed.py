"""
freshness/reembed.py — verified record-level vector upsert for human-approved
corpus fixes (Freshness V2 §10/§11/§12/§20).

Task records (created ONLY by review_actions.apply_source_action 'update')
carry the pre-edit corpus text hash. On --execute, per task:

  1. read the record from its corpus  → missing/changed service = loud abort
     of that task (task stays pending)
  2. if sha256(current text) == pre_edit hash → the manual corpus edit has not
     been detected → ABORT that record (task stays pending)
  3. metadata via freshness.metadata (parity with the full embed path, §11)
  4. Chroma upsert of that single chunk by stable id (never a corpus rebuild)
  5. read-back verification
  6. atomically clear ONLY that task — earlier successes stay completed if a
     later task fails (§12); reruns are idempotent

No tasks → no vector-write path at all (§20).
Stable-ID upsert covers content updates; ID renames / record deletion /
service moves are deferred maintenance (§12/§30) — not automated here.

Usage:
    python freshness/reembed.py --list
    python freshness/reembed.py              # dry run
    python freshness/reembed.py --execute    # actually upsert (requires tasks)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from freshness.metadata import build_metadata, SUPPORTED  # noqa: E402
from freshness.paths import (atomic_read_json, atomic_write_json,  # noqa: E402
                             sha256_text, REPO_ROOT)

TASKS_PATH = Path(__file__).resolve().parent / "approved_for_reembed.json"
DB_DIR = REPO_ROOT / "data" / "vectordb"
MODEL_NAME = "BAAI/bge-m3"   # same model as every embed_<svc>.py — no exceptions


class ReembedService:
    """Vector mutation behind injectable ports: hermetic tests use a fake
    embedder + fake collection; production uses bge-m3 + real Chroma."""

    def __init__(self, *, corpus_paths: dict[str, Path] | None = None,
                 tasks_path: Path = TASKS_PATH, db_dir: Path = DB_DIR,
                 embedder=None, collection_factory=None,
                 source_store_path: Path | None = None):
        self.tasks_path = Path(tasks_path)
        self.db_dir = Path(db_dir)
        # 13B: when provided, a task whose accepted source version is no longer
        # the trusted version fails as source_advanced_since_approval
        self.source_store_path = source_store_path
        self.corpus_paths = corpus_paths or {
            svc: REPO_ROOT / "data" / "chunks" / f"{svc}.jsonl" for svc in SUPPORTED
        }
        self._embedder = embedder
        self._collection_factory = collection_factory

    # -- injected ports --------------------------------------------------------

    def embedder(self, texts: list[str]) -> list[list[float]]:
        if self._embedder is not None:
            return self._embedder(texts)
        from sentence_transformers import SentenceTransformer
        if getattr(self, "_model", None) is None:
            print(f"loading model: {MODEL_NAME} …")
            self._model = SentenceTransformer(MODEL_NAME)
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in vecs]

    def collection(self, service: str):
        if self._collection_factory is not None:
            return self._collection_factory(service)
        import chromadb
        client = chromadb.PersistentClient(path=str(self.db_dir))
        return client.get_collection(service)

    # -- task processing ---------------------------------------------------------

    def _read_corpus(self, service: str) -> dict[str, dict]:
        records = {}
        with open(self.corpus_paths[service], encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    records[r["id"]] = r
        return records

    def _clear_task(self, tasks: list[dict], task_id: str) -> None:
        """Atomically remove exactly one completed task (§12)."""
        remaining = [t for t in tasks if t["task_id"] != task_id]
        atomic_write_json(self.tasks_path, remaining)
        tasks[:] = remaining

    def run(self, execute: bool = False) -> dict:
        tasks = atomic_read_json(self.tasks_path, default=[]) or []
        result = {"tasks_total": len(tasks), "upserted": [], "aborted_not_edited": [],
                  "failed": [], "note": None}

        if not tasks:
            result["note"] = ("No approved record tasks. A vector write only ever "
                              "follows review_actions 'update' + a manual corpus edit.")
            return result

        if not execute:
            result["note"] = "DRY RUN — pass --execute to upsert. Would re-embed:"
            result["would_upsert"] = [{k: t[k] for k in ("record_id", "service", "source_url")}
                                      for t in tasks]
            return result

        # process incrementally; tasks list shrinks atomically per success (§12).
        # B11: EVERY per-task failure — including a malformed task dict — is
        # contained inside this loop; a bad task can never abort later tasks.
        for task in list(tasks):
            rid = service = None
            try:
                rid = task.get("record_id")
                service = task.get("service")
                missing = [k for k in ("task_id", "record_id", "service",
                                       "pre_edit_text_sha256") if not task.get(k)]
                if missing:
                    raise RuntimeError(f"malformed task, missing fields: {missing}")
                if service not in self.corpus_paths:
                    raise RuntimeError(f"invalid collection/service mapping {service!r}")
                # 13B: if the source has advanced to a newer trusted version
                # since this task was approved, require fresh review
                if self.source_store_path and task.get("accepted_source_sha256"):
                    store = atomic_read_json(self.source_store_path, default={}) or {}
                    entry = store.get(task.get("source_url")
                                      or task.get("normalized_url") or "")
                    if entry and entry.get("trusted_hash")                             and entry["trusted_hash"] != task["accepted_source_sha256"]:
                        raise RuntimeError(
                            f"source_advanced_since_approval: trusted source hash "
                            f"{entry['trusted_hash'][:12]} != accepted "
                            f"{task['accepted_source_sha256'][:12]} — fresh review required")
                records = self._read_corpus(service)
                rec = records.get(rid)
                if rec is None:
                    raise RuntimeError(f"record {rid!r} no longer exists in "
                                       f"{self.corpus_paths[service]}")
                current_hash = sha256_text(rec.get("text", ""))
                if current_hash == task.get("pre_edit_text_sha256"):
                    # §10: the human has not edited the corpus yet
                    result["aborted_not_edited"].append(
                        {"task_id": task["task_id"], "record_id": rid,
                         "reason": "manual corpus edit has not been detected"})
                    continue
                text = rec["text"]
                vector = self.embedder([text])[0]
                coll = self.collection(service)
                coll.upsert(ids=[rid], documents=[text], embeddings=[vector],
                            metadatas=[build_metadata(service, rec)])
                got = coll.get(ids=[rid], include=["documents"])
                if not got["documents"] or got["documents"][0] != text:
                    raise RuntimeError(f"upsert verify failed for {rid}")
                result["upserted"].append({"task_id": task["task_id"], "record_id": rid,
                                           "service": service})
                self._clear_task(tasks, task["task_id"])
            except Exception as exc:  # noqa: BLE001 — one failed task must not block others
                result["failed"].append({"task_id": task["task_id"], "record_id": rid,
                                         "error": str(exc)[:200]})
        return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Chroma upsert of human-approved corpus fixes")
    ap.add_argument("--execute", action="store_true",
                    help="actually upsert into data/vectordb (default: dry run)")
    ap.add_argument("--list", action="store_true", help="show the task queue and exit")
    args = ap.parse_args()

    tasks = atomic_read_json(TASKS_PATH, default=[]) or []
    if args.list:
        print(json.dumps(tasks, indent=2, ensure_ascii=False))
        return

    svc = ReembedService()
    out = svc.run(execute=args.execute)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if out.get("failed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
