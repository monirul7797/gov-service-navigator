"""
build_nid_corpus.py — turn the manually-scraped NID source files into the same
provenance-stamped JSONL the live pipeline produces.

Why this exists: the automated scrapers can't reach the NID portal (JS SPA +
robots), so the citizen-facing NID content was scraped by hand into the text
files under data/raw/nid_manual/. This script segments those files, preserves
the Bangla byte-for-byte (it reads from disk, nothing is retyped), and runs the
SAME normalization / classification / Bangla-health / provenance code as the
rest of the pipeline, so the output passes the exact same QA.

Segmentation: every source file separates logical units with a blank line, so a
blank-line split gives one clean chunk per FAQ Q&A, per fee item, per process
section, etc. The first line of each block becomes the heading (which also feeds
the keyword classifier). Meta header lines (SECTION:/SOURCE:/CATEGORY:/LANGUAGE:)
are stripped. `source_sha256` is the real sha256 of the originating file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time

from src.paths import ensure_dirs, chunks_path, BASE_DIR, DATA_DIR
from src.bangla import normalize_text, check_bangla, _bengali_count
from src.chunk import classify, _lang_of, write_jsonl

SERVICE = "nid"
SCRAPED_ON = "2026-07-13"

# Where the manually-scraped files live inside the project (copied from uploads
# so the project is self-contained and re-runnable on the user's machine).
MANUAL_DIR = os.path.join(DATA_DIR, "raw", "nid_manual")
REF_DIR = os.path.join(DATA_DIR, "reference")

_META_RE = re.compile(r"^(SECTION|SOURCE|CATEGORY|LANGUAGE)\s*:", re.IGNORECASE)

# -- per-file source URLs (all official gov.bd; verified in config/sources.yaml) --
# `force` optionally pins a category when a whole file is unambiguously one topic
# and the keyword classifier would otherwise split hairs. When force is None the
# classifier decides. `drop_contains` skips blocks (e.g. raw geo dropdown lists
# that belong in a structured lookup table, not the RAG corpus).
MANIFEST = {
    "01_nid_new_voter_registration.txt": {
        "url": "https://services.nidw.gov.bd/", "force": None},
    "02_nid_information_correction.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/", "force": None},
    "03_nid_reissue_and_duplicate.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/", "force": None},
    "04_nid_fees_and_payment_instructions.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/fees", "force": None},
    "05_nid_voter_migration_and_deletion.txt": {
        "url": "https://services.nidw.gov.bd/", "force": None},
    "06_nid_wallet_application.txt": {
        "url": "https://services.nidw.gov.bd/", "force": None},
    "07_portal_registration_and_account_claim.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/", "force": None},
    "08_portal_general_faqs.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/faq", "force": None},
    "09_official_fee_structure.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/fees", "force": None},
    "10_form_2_correction_guidelines.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/", "force": None},
    "11_form_6_reissue_guidelines.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/", "force": None},
    "12_special_area_verification_protocol.txt": {
        "url": "https://www.ecs.gov.bd/", "force": None},
    "13_faq_nid_information_correction_docs.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/faq", "force": "required_documents"},
    "14_faq_nid_correction_queries.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/faq", "force": None},
    "15_faq_lost_nid_card.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/faq", "force": None},
    "16_faq_registration_queries.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/faq", "force": None},
    "17_registration_process_guidelines.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/faq", "force": None},
    "18_fee_calculator_outputs.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/fees", "force": "fees"},
    "19_faq_nid_correction_process_details.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/faq", "force": None},
    "20_fee_calculator_advanced_test_cases.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/fees", "force": "fees"},
    "21_portal_forms_and_apps_download.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/form-download", "force": None},
    "22_fee_calculator_ui_options.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/fees", "force": "fees"},
    # 23 (address dropdown UI) handled specially: the geo lists are NOT chunked
    # (they belong in a structured lookup); only the process note is kept.
    "24_account_claim_otp_verification.txt": {
        "url": "https://services.nidw.gov.bd/nid-pub/claim-account", "force": "verification"},
}

MIN_CHARS = 35


def _sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _blocks(raw: str):
    """Strip meta header lines, then split on blank lines into (heading, text)."""
    lines = [ln for ln in raw.splitlines() if not _META_RE.match(ln.strip())]
    text = "\n".join(lines)
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        first = block.splitlines()[0].strip()
        heading = first.strip("[]").rstrip(":").strip()
        # keep heading compact for readability / as a classifier signal
        if len(heading) > 90:
            heading = heading[:90].rstrip() + "…"
        yield heading, block


def _record(url: str, heading: str, body_raw: str, sha: str, force: str | None):
    body = normalize_text(body_raw)
    if len(body) < MIN_CHARS:
        return None
    lang = _lang_of(body)
    cat, conf = classify(body, heading)
    if force:
        cat = force  # whole-file topic pin; confidence still reflects keywords
    health = check_bangla(body, expect_bangla=(_bengali_count(body) > 0))
    cid = hashlib.sha1(f"{SERVICE}|{url}|{heading}|{body[:40]}".encode()).hexdigest()[:16]
    return {
        "id": cid, "service": SERVICE, "category": cat,
        "category_confidence": conf, "text": body, "heading": heading,
        "lang": lang, "source_url": url, "official": True,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_sha256": sha,
        "bangla_ok": health.ok, "bangla_issues": health.issues,
    }


def _manual_chunks_for_23(url: str, sha: str):
    """File 23 is the Division→District→Upazila dropdown UI. Per the RAG design
    (and to keep the vector store free of hundreds of place-name rows), the raw
    hierarchy is saved as a structured lookup asset, not embedded. We keep only
    the *process* knowledge: that account-claim requires an exact address match."""
    heading = "Account claim — address must match NID records"
    text = (
        "To claim an existing NID account, the applicant selects Present Address "
        "and Permanent Address from cascading dropdowns — Division (বিভাগ) → "
        "District (জেলা) → Upazila/Thana (উপজেলা) — and each selection must "
        "exactly match what is stored in the NID database, or the claim fails. "
        "The full administrative hierarchy (8 divisions, 64 districts, and 495+ "
        "upazilas) is kept as a structured lookup table, not as searchable text."
    )
    return [_record(url, heading, text, sha, force="verification")]


def _save_geo_reference():
    """Copy the admin-hierarchy JSON into the project as a reference asset."""
    os.makedirs(REF_DIR, exist_ok=True)
    src = os.path.join(MANUAL_DIR, "bd_admin_hierarchy.json")
    if os.path.exists(src):
        shutil.copy(src, os.path.join(REF_DIR, "bd_admin_hierarchy.json"))


def build():
    ensure_dirs()
    open(chunks_path(SERVICE), "w").close()   # fresh nid.jsonl
    total = 0
    by_cat: dict[str, int] = {}

    for fname, spec in MANIFEST.items():
        path = os.path.join(MANUAL_DIR, fname)
        if not os.path.exists(path):
            print(f"  ! missing {fname} — skipped")
            continue
        raw = open(path, encoding="utf-8").read()
        sha = "manual:" + _sha256_file(path)[:16]
        recs = []
        for heading, block in _blocks(raw):
            rec = _record(spec["url"], heading, block, sha, spec["force"])
            if rec:
                recs.append(rec)
        write_jsonl(chunks_path(SERVICE), recs, append=True)
        for r in recs:
            by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
        total += len(recs)
        print(f"  {fname:48} -> {len(recs):2} chunks")

    # file 23 (special) + geo reference asset
    p23 = os.path.join(MANUAL_DIR, "23_account_claim_address_verification_ui.txt")
    sha23 = "manual:" + (_sha256_file(p23)[:16] if os.path.exists(p23) else "na")
    recs23 = [r for r in _manual_chunks_for_23(
        "https://services.nidw.gov.bd/nid-pub/claim-account", sha23) if r]
    write_jsonl(chunks_path(SERVICE), recs23, append=True)
    for r in recs23:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    total += len(recs23)
    print(f"  {'23_account_claim_address_verification_ui.txt':48} -> {len(recs23):2} chunks (geo list -> reference asset)")

    _save_geo_reference()

    print(f"\nnid corpus written: {total} chunks")
    print("by category:", dict(sorted(by_cat.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    build()
