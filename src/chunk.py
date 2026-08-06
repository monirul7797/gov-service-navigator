"""
chunk.py — text blocks -> classified, provenance-stamped JSONL chunks.

Each output record (one JSON object per line):
  {
    "id":        stable sha1 of (service|url|order),
    "service":   "epassport",
    "category":  "fees",          # from taxonomy classifier
    "category_confidence": 0.42,
    "text":      "<normalized chunk text>",
    "heading":   "<nearest heading>",
    "lang":      "bn" | "en" | "mixed",
    "source_url": "...gov.bd/...",
    "official":  true,
    "fetched_at": "...",
    "source_sha256": "...",        # integrity of the page it came from
    "bangla_ok": true,             # false -> flagged for human review
    "bangla_issues": [...]
  }

Chunking: greedy pack of consecutive blocks up to TARGET_CHARS, never splitting
mid-block, always prefixing the heading so a retrieved chunk is self-describing.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Tuple

from .bangla import check_bangla, normalize_text, _bengali_count
from .extract import Block
from .fetch import Fetched
from .paths import load_taxonomy

# ---- tunables (top-of-file config, per the established pattern) --------------
TARGET_CHARS = 600      # aim per chunk
MAX_CHARS = 900         # hard ceiling
MIN_CHARS = 40          # drop trivially short chunks
HEADING_WEIGHT = 3      # a keyword hit in the heading counts this many body hits

# ASCII keywords are matched on word boundaries so "process" does not fire on
# "microprocessor" and "submit" does not fire on "submitted". Bangla keywords
# stay as substring tests: Bengali has no reliable \b, and the curated Bangla
# keyword list is already whole-word phrases, so substring matching is safe.
_ASCII_RE = re.compile(r"[a-z]")


@lru_cache(maxsize=4096)
def _kw_matcher(kw: str):
    """Compiled matcher for one keyword. Word-boundary for ASCII, plain 'in'
    for anything containing Bengali (returned as None -> substring path)."""
    if _ASCII_RE.search(kw):
        return re.compile(r"\b" + re.escape(kw) + r"\b")
    return None


def _kw_hit(kw: str, hay: str) -> bool:
    m = _kw_matcher(kw.lower())
    if m is None:                 # Bangla / non-ascii -> substring
        return kw.lower() in hay
    return m.search(hay) is not None


def _lang_of(s: str) -> str:
    bn = _bengali_count(s)
    latin = sum(1 for ch in s if "a" <= ch.lower() <= "z")
    if bn and latin and min(bn, latin) / max(bn, latin) > 0.25:
        return "mixed"
    return "bn" if bn >= latin else "en"


def classify(text: str, heading: str) -> Tuple[str, float]:
    """Keyword-vote classifier over taxonomy.

    A hit in the heading is worth HEADING_WEIGHT body hits, because a section
    title ("Processing time", "What BDRIS is") is a far stronger signal of the
    chunk's topic than an incidental keyword in the body. Returns (category,
    confidence) where confidence is the winning category's share of total votes.
    """
    tax = load_taxonomy()["categories"]
    head = (heading or "").lower()
    body = (text or "").lower()
    scores = {}
    for cat, spec in tax.items():
        kws = (spec.get("keywords_en") or []) + (spec.get("keywords_bn") or [])
        votes = 0
        for kw in kws:
            if _kw_hit(kw, head):
                votes += HEADING_WEIGHT
            elif _kw_hit(kw, body):
                votes += 1
        if votes:
            scores[cat] = votes
    if not scores:
        return ("overview", 0.0)
    total = sum(scores.values())
    best = max(scores, key=scores.get)
    return (best, round(scores[best] / total, 3))


def _pack(blocks: List[Block]) -> List[Block]:
    """Greedily merge consecutive blocks sharing a heading into ~TARGET_CHARS."""
    packed: List[Block] = []
    buf, buf_head, buf_order = [], None, None
    cur = 0
    for b in blocks:
        if buf and (b.heading != buf_head or cur + len(b.text) > MAX_CHARS):
            packed.append(Block("\n".join(buf), buf_head or "", buf_order))
            buf, cur = [], 0
        if not buf:
            buf_head, buf_order = b.heading, b.order
        buf.append(b.text)
        cur += len(b.text) + 1
        if cur >= TARGET_CHARS:
            packed.append(Block("\n".join(buf), buf_head or "", buf_order))
            buf, cur = [], 0
    if buf:
        packed.append(Block("\n".join(buf), buf_head or "", buf_order))
    return packed


def make_chunks(fetched: Fetched, blocks: List[Block],
                *, expect_bangla: bool) -> List[dict]:
    records: List[dict] = []
    for b in _pack(blocks):
        body = normalize_text(
            (b.heading + "\n" + b.text) if b.heading and b.heading not in b.text
            else b.text
        )
        if len(body) < MIN_CHARS:
            continue
        cat, conf = classify(b.text, b.heading)
        lang = _lang_of(body)
        health = check_bangla(body, expect_bangla=expect_bangla and lang != "en")
        cid = hashlib.sha1(
            f"{fetched.service}|{fetched.url}|{b.order}".encode("utf-8")
        ).hexdigest()[:16]
        records.append({
            "id": cid,
            "service": fetched.service,
            "category": cat,
            "category_confidence": conf,
            "text": body,
            "heading": b.heading,
            "lang": lang,
            "source_url": fetched.url,
            "official": True,
            "fetched_at": fetched.fetched_at,
            "source_sha256": fetched.sha256,
            "bangla_ok": health.ok,
            "bangla_issues": health.issues,
        })
    return records


def write_jsonl(path: str, records: List[dict], *, append: bool = True) -> int:
    mode = "a" if append else "w"
    with open(path, mode, encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(records)
