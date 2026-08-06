"""
qa.py — did we actually collect everything, cleanly and correctly?

Produces a report answering three questions mechanically:
  1. COVERAGE  — for every service, is each *required* taxonomy category filled?
                 Empty required cells are the gaps to go re-collect.
  2. INTEGRITY — any chunk with broken Bangla (mojibake/FFFD/orphan matra/etc.)?
  3. DEDUP     — near-duplicate chunks (same normalized text) across sources.

Run after chunking. Exit status is non-zero if required-category gaps exist,
so this can gate a "Phase 1 complete" check in CI or a Makefile.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Dict, List

from .paths import CHUNKS_DIR, load_sources, load_taxonomy


def _load_all() -> List[dict]:
    rows: List[dict] = []
    if not os.path.isdir(CHUNKS_DIR):
        return rows
    for fn in sorted(os.listdir(CHUNKS_DIR)):
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(CHUNKS_DIR, fn), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def build_report() -> dict:
    rows = _load_all()
    services = list(load_sources()["services"].keys())
    tax = load_taxonomy()["categories"]
    required = [c for c, s in tax.items() if s.get("required")]

    # coverage[service][category] = count
    coverage: Dict[str, Dict[str, int]] = {
        s: {c: 0 for c in tax} for s in services
    }
    per_service_total = defaultdict(int)
    broken: List[dict] = []
    seen_text: Dict[str, str] = {}
    dupes: List[tuple] = []

    for r in rows:
        s, c = r["service"], r["category"]
        if s in coverage:
            coverage[s][c] = coverage[s].get(c, 0) + 1
        per_service_total[s] += 1
        if not r.get("bangla_ok", True):
            broken.append({"id": r["id"], "service": s,
                           "issues": r.get("bangla_issues", []),
                           "source_url": r.get("source_url")})
        key = r["text"].strip()
        if key in seen_text and seen_text[key] != r["id"]:
            dupes.append((seen_text[key], r["id"]))
        else:
            seen_text[key] = r["id"]

    gaps = []
    for s in services:
        for c in required:
            if coverage[s].get(c, 0) == 0:
                gaps.append((s, c))

    return {
        "services": services,
        "required_categories": required,
        "coverage": coverage,
        "per_service_total": dict(per_service_total),
        "gaps": gaps,
        "broken_bangla": broken,
        "duplicates": dupes,
        "total_chunks": len(rows),
    }


def print_report(rep: dict) -> None:
    tax = load_taxonomy()["categories"]
    cats = list(tax.keys())
    print("=" * 78)
    print(f"PHASE 1 QA REPORT  —  {rep['total_chunks']} chunks total")
    print("=" * 78)

    # Coverage matrix
    head = "service".ljust(12) + "".join(c[:9].ljust(10) for c in cats)
    print("\nCOVERAGE MATRIX (chunks per category; '·' = required gap)\n")
    print(head)
    for s in rep["services"]:
        row = s.ljust(12)
        for c in cats:
            n = rep["coverage"][s].get(c, 0)
            req = tax[c].get("required")
            cell = str(n) if n else ("·" if req else "-")
            row += cell.ljust(10)
        print(row)
    print(f"\n  total per service: {rep['per_service_total']}")

    # Gaps
    if rep["gaps"]:
        print(f"\n⚠  REQUIRED-CATEGORY GAPS ({len(rep['gaps'])}):")
        for s, c in rep["gaps"]:
            print(f"     - {s}: '{c}' has 0 chunks — re-collect")
    else:
        print("\n✓  No required-category gaps. Every service covers all required categories.")

    # Bangla integrity
    if rep["broken_bangla"]:
        print(f"\n⚠  BANGLA ENCODING ISSUES ({len(rep['broken_bangla'])} chunks):")
        for b in rep["broken_bangla"][:20]:
            print(f"     - {b['service']} {b['id']}: {', '.join(b['issues'])}")
        if len(rep["broken_bangla"]) > 20:
            print(f"     ... and {len(rep['broken_bangla']) - 20} more")
    else:
        print("\n✓  No broken-Bangla chunks detected.")

    # Dupes
    if rep["duplicates"]:
        print(f"\n⚠  {len(rep['duplicates'])} duplicate chunk pair(s) — consider dedup.")
    else:
        print("\n✓  No exact-duplicate chunks.")
    print()


if __name__ == "__main__":
    import sys
    rep = build_report()
    print_report(rep)
    sys.exit(1 if rep["gaps"] else 0)
