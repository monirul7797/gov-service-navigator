"""
pipeline.py — run the whole Phase 1 collection from the verified registry.

    python -m src.pipeline            # all services
    python -m src.pipeline nid etax   # selected services
    python -m src.pipeline --qa       # (re)build QA report only

Fetch is cache-first, so re-running is cheap and reproducible. Each service's
chunks are rewritten fresh (not appended) to keep the JSONL deterministic.
"""
from __future__ import annotations

import sys
from typing import List

from .paths import ensure_dirs, load_sources, chunks_path
from .fetch import fetch
from .extract import extract
from .chunk import make_chunks, write_jsonl
from . import qa


def _expects_bangla(lang) -> bool:
    if isinstance(lang, list):
        return "bn" in lang
    return lang == "bn"


def run_service(service: str) -> int:
    reg = load_sources()["services"][service]
    path = chunks_path(service)
    open(path, "w").close()  # truncate: deterministic rebuild
    total = 0
    for seed in reg["seeds"]:
        url, render, lang = seed["url"], seed.get("render", "static"), seed.get("lang", "en")
        try:
            fetched = fetch(url, service, render=render)
        except Exception as e:  # noqa: BLE001 — log and continue collecting
            print(f"  [skip] {url}\n         {e}")
            continue
        blocks = extract(fetched)
        recs = make_chunks(fetched, blocks, expect_bangla=_expects_bangla(lang))
        n = write_jsonl(path, recs, append=True)
        src = "cache" if fetched.from_cache else "live"
        print(f"  [{src}] {url}  ->  {len(blocks)} blocks, {n} chunks")
        total += n
    print(f"  = {service}: {total} chunks written to {path}")
    return total


def main(argv: List[str]) -> int:
    ensure_dirs()
    if "--qa" in argv:
        rep = qa.build_report()
        qa.print_report(rep)
        return 1 if rep["gaps"] else 0

    services = [a for a in argv if not a.startswith("-")] or \
        list(load_sources()["services"].keys())
    grand = 0
    for s in services:
        print(f"\n### {s} ###")
        grand += run_service(s)
    print(f"\nTOTAL: {grand} chunks across {len(services)} service(s)")

    print("\n" + "#" * 78)
    rep = qa.build_report()
    qa.print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
