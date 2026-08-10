"""
build_bdris_corpus.py — Phase 1.5: normalize the 6 BDRIS category files (each with
its own record shape) into one consistent data/chunks/bdris.jsonl, matching the
schema embed_bdris.py expects (same shape as ereturn.jsonl: text, category,
subcategory, heading, lang, source_url, official, plus question_bn/question_en
where available).

WHY THIS EXISTS:
  Unlike e-Return (one FAQ shape throughout), BDRIS was built across 6 separate
  sessions/sources with 6 different record shapes:
    - bdris_faq.jsonl            : question_bn/question_en + answer_bn/answer_en
                                    (standalone) OR qa_pairs[] (grouped records)
    - bdris_application_process  : title_bn/title_en + content_bn/content_en
    - bdris_manual_walkthrough   : same, plus stage (int) + source_file
    - bdris_correction           : same, some records language:"en-only"
    - bdris_login_instructions   : same, language:"bn-only"
    - bdris_rules2018            : same, plus rule_no; source_url uses a
                                    "gazette:" prefix instead of a live URL
                                    (static uploaded document, not a webpage)

  This mirrors what build_nid_corpus.py already does for NID's manually-scraped
  files: normalize disparate sources into one consistent shape BEFORE embedding,
  rather than making the embed script itself schema-aware per category.

  Grouped bdris_faq records (qa_pairs[]) are unbundled here into one chunk per
  sub-question, so retrieval can match a specific sub-question directly instead
  of only ever surfacing the whole bundle.

Run:
    python build_bdris_corpus.py
Output:
    data/chunks/bdris.jsonl
"""
from __future__ import annotations

import hashlib
import json
import os

# ---------------------------------------------------------------- config
CHUNKS_DIR = "data/chunks"
OUTPUT_PATH = os.path.join(CHUNKS_DIR, "bdris.jsonl")

# Each source file + its category name (source files are wherever this session's
# outputs were saved — adjust paths if you filed them elsewhere).
SOURCES = [
    ("data/chunks/bdris_faq.jsonl", "bdris_faq"),
    ("data/chunks/bdris_application_process.jsonl", "bdris_application_process"),
    ("data/chunks/bdris_manual_walkthrough.jsonl", "bdris_manual_walkthrough"),
    ("data/chunks/bdris_correction.jsonl", "bdris_correction"),
    ("data/chunks/bdris_payment.jsonl", "bdris_payment"),
    ("data/chunks/bdris_login_instructions.jsonl", "bdris_login_instructions"),
    ("data/chunks/bdris_rules2018.jsonl", "bdris_rules2018"),
]
# ------------------------------------------------------------------------


def load_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        print(f"  ! missing {path} — skipped")
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _lang_of(record: dict) -> str:
    """Infer lang from whichever language marker the record carries."""
    tag = record.get("language", "")
    if tag == "bn-only":
        return "bn"
    if tag == "en-only":
        return "en"
    return "bn+en"  # bilingual, the default across BDRIS


def _combine_text(bn: str, en: str) -> str:
    """
    Store BOTH languages in the embedded text (matches how e-Return / NID chunks
    keep the original bilingual text together) — this is what makes cross-lingual
    retrieval work: a Bangla query can match the Bangla half, an English query the
    English half, of the SAME stored chunk.
    """
    parts = [p for p in (bn, en) if p]
    return "\n\n".join(parts)


def _record_id(category: str, seed: str) -> str:
    return f"{category}_{hashlib.sha1(seed.encode()).hexdigest()[:12]}"


def normalize_standalone(raw: dict, category: str) -> dict | None:
    """Handle every BDRIS record shape EXCEPT grouped qa_pairs."""
    # question/heading fields vary by source: question_bn/en (faq) or title_bn/en (rest)
    heading_bn = raw.get("question_bn") or raw.get("title_bn") or ""
    heading_en = raw.get("question_en") or raw.get("title_en") or ""
    body_bn = raw.get("answer_bn") or raw.get("content_bn") or ""
    body_en = raw.get("answer_en") or raw.get("content_en") or ""

    if not body_bn and not body_en:
        return None  # nothing to embed (shouldn't happen, but fail safe not loud here)

    text = _combine_text(
        f"{heading_bn}\n{body_bn}".strip() if heading_bn else body_bn,
        f"{heading_en}\n{body_en}".strip() if heading_en else body_en,
    )

    rec = {
        "id": raw.get("id") or _record_id(category, heading_bn or heading_en or body_bn[:40]),
        "text": text,
        "category": category,
        "subcategory": category,  # BDRIS has no finer subcategory split than the 6 files
        "heading": heading_en or heading_bn or "(untitled)",
        "lang": _lang_of(raw),
        "source_url": raw.get("source_url", ""),
        "official": True,
        "question_bn": heading_bn,
        "question_en": heading_en,
    }
    # preserve category-specific extras without forcing every category to have them
    if "rule_no" in raw:
        rec["rule_no"] = raw["rule_no"]
    if "stage" in raw:
        rec["stage"] = raw["stage"]
    if "source_file" in raw:
        rec["source_file"] = raw["source_file"]
    if raw.get("source_url_unverified"):
        rec["source_url_unverified"] = True
    return rec


def normalize_grouped_faq(raw: dict, category: str) -> list[dict]:
    """
    Unbundle a grouped bdris_faq record (qa_pairs[]) into one chunk per
    sub-question, so retrieval can match a specific sub-question directly.
    """
    out = []
    group_id = raw.get("id", "group")
    topic = raw.get("topic", "")
    for i, pair in enumerate(raw.get("qa_pairs", []), 1):
        q_bn, q_en = pair.get("q_bn", ""), pair.get("q_en", "")
        a_bn, a_en = pair.get("a_bn", ""), pair.get("a_en", "")
        text = _combine_text(
            f"{q_bn}\n{a_bn}".strip() if q_bn else a_bn,
            f"{q_en}\n{a_en}".strip() if q_en else a_en,
        )
        out.append({
            "id": f"{group_id}_{i}",
            "text": text,
            "category": category,
            "subcategory": category,
            "heading": q_en or q_bn or topic or "(untitled)",
            "lang": "bn+en",
            "source_url": raw.get("source_url", ""),
            "official": True,
            "question_bn": q_bn,
            "question_en": q_en,
            "group_topic": topic,
        })
    return out


def build() -> None:
    os.makedirs(CHUNKS_DIR, exist_ok=True)
    all_records: list[dict] = []
    by_category: dict[str, int] = {}

    for path, category in SOURCES:
        raw_records = load_jsonl(path)
        count_before = len(all_records)
        for raw in raw_records:
            if "qa_pairs" in raw:
                all_records.extend(normalize_grouped_faq(raw, category))
            else:
                rec = normalize_standalone(raw, category)
                if rec:
                    all_records.append(rec)
        added = len(all_records) - count_before
        by_category[category] = by_category.get(category, 0) + added
        print(f"  {os.path.basename(path):40} -> {added:2} chunks")

    # fail loudly on duplicate ids BEFORE writing anything — a silent id
    # collision would let one chunk overwrite another at embed time
    ids = [r["id"] for r in all_records]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"Duplicate ids found during normalization: {sorted(dupes)}")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for r in all_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nbdris corpus written: {len(all_records)} chunks -> {OUTPUT_PATH}")
    print("by category:", dict(sorted(by_category.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    build()
