# Freshness report — 2026-09-02

- Services checked: nid
- Records targeted: 5
- Fetched: 5  (new baselines: 0, changed: 0, unchanged: 5)
- Needs manual screenshot: 0
- Skipped (no usable source_url): 0
- Pending review queue: 0 → `freshness/pending_review.json` (approve via `python freshness/review.py`)

## Notes

- robots.txt was unreachable on at least one domain (robots_unreachable_http_503); pages were fetched because no robots rules were actually served. Explicit robots directives are always honored.

This run detects drift only — no corpus .jsonl or data/vectordb file was written by this script.
