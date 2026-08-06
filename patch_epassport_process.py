"""
patch_epassport_process.py — close the e-Passport 'front door' coverage gap.

Three things, in one pass, so every layer stays in sync (convention #6):

  1. FIX provenance on three notice records. Verified from screenshots
     (convention #5): notices/33 = functional offices, notices/34 = documents
     to carry, notices/160 = documents checklist. The corpus had all three
     pointing one slot off. Content/dates were right; only source_url was wrong.

  2. RECOMPUTE char_len. Six notice records were off by exactly +1 (trailing
     newline added after the field was computed). Derived field, no assertion
     behind it -> recompute all, then assert.

  3. APPEND two records under a new retrieval category, `process`:
       - epassport-instructions-five-step-overview  (the missing hub)
       - epassport-onboarding-region-check          (which RPO is mine?)

Record shape matches the existing 60 exactly, including the text/content
duplication (NOT unilaterally changed here — flagged separately).
"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
CHUNKS = BASE / "epassport.jsonl"
OUT = BASE / "epassport.jsonl"
CRAWLED = "2026-07-16"

# ---------------------------------------------------------------- 1. URL fixes
# id -> (wrong_url_currently_in_corpus, correct_url_verified_from_screenshot)
URL_FIXES = {
    "epassport-notice-functional-epassport-offices": (
        "https://www.epassport.gov.bd/landing/notices/34",
        "https://www.epassport.gov.bd/landing/notices/33",
    ),
    "epassport-notice-documents-carried-enrolment": (
        "https://www.epassport.gov.bd/landing/notices/160",
        "https://www.epassport.gov.bd/landing/notices/34",
    ),
    "epassport-notice-documents-checklist-enrollment": (
        "https://www.epassport.gov.bd/landing/notices/161",
        "https://www.epassport.gov.bd/landing/notices/160",
    ),
}

# This record claimed notices/33, which the screenshots prove belongs to the
# functional-offices notice. Its real URL is therefore UNKNOWN. Convention #5
# says verify from a screenshot, never guess — so it is pointed at the All
# Notices index (a real page that does list it) and flagged, rather than
# assigned a plausible-looking ID. 161 is the obvious candidate now that the
# checklist has vacated it, but "obvious candidate" is exactly the guess that
# produced this bug in the first place.
UNVERIFIED_URL = {
    "epassport-notice-dhaka-cantonment-jurisdiction":
        "https://www.epassport.gov.bd/landing/notices",
}

# ---------------------------------------------------------------- 3. new records

FIVE_STEP_TEXT = """e-Passport Application — The 5 Steps, Start to Finish
ই-পাসপোর্ট আবেদনের ৫(পাঁচ) টি ধাপ

--- মূল বাংলা / Original Bangla (verbatim, epassport.gov.bd/instructions/five-step-to-your-epassport, বাংলা toggle) ---
ই-পাসপোর্ট আবেদনের ৫(পাঁচ) টি ধাপ
ই-পাসপোর্ট আবেদনের জন্য ৫টি সহজ ধাপ রয়েছে
ধাপগুলো হলো:
ধাপ-১ : বর্তমানে বসবাসরত এলাকায় ই-পাসপোর্ট কার্যক্রম শুরু হয়েছে কি না দেখুন ।
- চালুকৃত ই-পাসপোর্ট অফিসগুলির তালিকা
ধাপ-২ : অনলাইনে ই-পাসপোর্ট আবেদন ফরম পূরণ করুন।
- অনলাইন আবেদন : আবেদন প্রক্রিয়ার জন্য ক্লিক করুন 'এখানে' ।
ধাপ-৩ : পাসপোর্ট ফি পরিশোধ ।
- পাসপোর্ট ফি ও ব্যাংক পেমেন্ট এর জন্য ক্লিক করুন 'এখানে' ।
ধাপ-৪ : ছবি ও ফিঙ্গার প্রিন্টের জন্য পাসপোর্ট অফিসে যোগাযোগ ।
- ছবি ও ফিঙ্গার প্রিন্টের জন্য পাসপোর্ট অফিসে যাওয়ার আগে প্রয়োজনীয় কাগজপত্র সঙ্গে নিয়েছেন কি না নিশ্চিত হোন।
ধাপ-৫ : পাসপোর্ট অফিস থেকে ই-পাসপোর্ট সংগ্রহ ।
- ডেলিভারী স্লিপ/রশিদ: এনরোলমেন্ট সম্পন্ন হওয়ার পর প্রদেয় স্লিপ ।
- অনুমোদিত প্রতিনিধির(নিজ জাতীয় পরিচয়পত্র সাথে আনতে হবে) কাছে পাসপোর্ট প্রদান করা যাবে।

--- English version (verbatim, same page, English toggle) — also published by the portal. Bangla is the portal's primary language and governs where the two diverge (see step 4 and step 5). ---
5 Steps to your e-Passport
You can apply for the new e-Passport in 5 easy steps
Step 1: Check if the new e-Passport is already available in your area
- List of functional e-Passport Offices
Step 2: Fill in your e-Passport application online
- For Online Application Click Here
Step 3: Pay passport fees
- For Passport Fees and Bank List Click Here
Step 4: Visit your Passport Office for biometric enrolment
- Make sure you have all required documents with you when you visit the passport office.
Step 5: Collect your e-Passport at the passport office
- Delivery slip you received during passport enrolment
- Authorized representatives(has to bring his/her NID card) can collect the applicant's new passport.

--- Where each step is documented in detail ---
Step 1 (area coverage / which office): List of functional e-Passport Offices — 72 offices as of 20 September 2023.
Step 2 (filling the form): e-Passport Form Fill-up Instructions — documents by age, NID/BRC rules, mandatory fields.
Step 3 (fees and payment): e-Passport Fees and Payment Options — A-Challan / DGePay / ShurjoPay online, or offline at any government or private bank; Regular, Express and Super Express tiers.
Step 4 (biometric enrolment visit): Documents need to be carried while enrolment at Passport offices; Documents Checklist for e-Passport Enrollment.
Step 5 (collection): delivery slip is required; an authorised representative may collect on the applicant's behalf if they bring their own NID."""

ONBOARDING_TEXT = """Step 1 in practice — Which Regional Passport Office is mine?
ধাপ ১: আপনার অঞ্চলে ই-পাসপোর্ট সেবার প্রাপ্যতা যাচাই করুন

--- মূল বাংলা / Original Bangla (verbatim, epassport.gov.bd/onboarding, বাংলা toggle) ---
ধাপ ১: আপনার অঞ্চলে ই-পাসপোর্ট সেবার প্রাপ্যতা যাচাই করুন
আপনি কি বাংলাদেশ থেকে আবেদন করছেন?  হ্যাঁ / না
আপনার বর্তমান ঠিকানার জেলা নির্বাচন করুন
আপনার বর্তমান ঠিকানার নিকটস্থ থানা নির্বাচন করুন
নিম্নোক্ত আঞ্চলিক পাসপোর্ট অফিসে আপনার নতুন ই-পাসপোর্টের জন্য নিবন্ধন করুন
চালিয়ে যান

--- English version (verbatim, same page, English toggle) — also published by the portal; Bangla is primary and governs. ---
Step 1: Check availability of e-Passport in your region
Are you applying from Bangladesh?  Yes / No
Select District of your present address
Select the police station nearest to your present address
Enroll for your new e-Passport in the following Regional Passport Office
Continue

--- What this means for the applicant ---
Before an application is started, the portal decides which office will handle it. The applicant first states whether they are applying from inside Bangladesh or from abroad — that fork determines whether the application is handled by a Regional Passport Office or by a Bangladesh mission overseas. If applying from inside Bangladesh, the applicant selects the District (জেলা) of their PRESENT address and then the police station (থানা) nearest to that present address; the portal then names the single Regional Passport Office where biometric enrolment must be done. The office is therefore assigned by present address, not by permanent address and not by the applicant's choice. The dropdown lists themselves are an interactive lookup and are not part of this knowledge base; the list of offices currently operating e-Passport services is published separately as a notice."""

NEW_RECORDS = [
    {
        "id": "epassport-instructions-five-step-overview",
        "section": "instructions",
        "category": "process",
        "subcategory": "five-step-overview",
        "heading": "e-Passport Application Process — The 5 Steps from Start to Collection",
        "source_url": "https://www.epassport.gov.bd/instructions/five-step-to-your-epassport",
        "last_updated": "2026-06-03",
        "content": FIVE_STEP_TEXT,
        "topics": ["process", "overview", "how-to-apply", "five-steps", "getting-started",
                   "application", "enrolment", "collection", "workflow"],
        "service": "epassport",
        "lang": "bn+en",
        "last_crawled": CRAWLED,
        "official": True,
        "text": FIVE_STEP_TEXT,
    },
    {
        "id": "epassport-onboarding-region-check",
        "section": "onboarding",
        "category": "process",
        "subcategory": "office-assignment",
        "heading": "Step 1 — Checking e-Passport Availability and Finding Your Regional Passport Office",
        "source_url": "https://www.epassport.gov.bd/onboarding",
        "last_updated": None,
        "content": ONBOARDING_TEXT,
        "topics": ["process", "office-assignment", "regional-passport-office", "district",
                   "police-station", "present-address", "availability", "abroad"],
        "service": "epassport",
        "lang": "bn+en",
        "last_crawled": CRAWLED,
        "official": True,
        "text": ONBOARDING_TEXT,
    },
]

# Field order the existing 60 records use.
KEY_ORDER = ["id", "section", "category", "subcategory", "heading", "source_url",
             "last_updated", "content", "topics", "service", "lang", "last_crawled",
             "official", "text", "char_len"]


def ordered(rec: dict) -> dict:
    out = {k: rec[k] for k in KEY_ORDER if k in rec and rec[k] is not None}
    for k, v in rec.items():          # any section-specific extras keep their place
        if k not in out and v is not None:
            out[k] = v
    return out


def main() -> None:
    rows = [json.loads(l) for l in CHUNKS.open(encoding="utf-8") if l.strip()]
    print(f"loaded {len(rows)} records")

    # -- 1. URL fixes ------------------------------------------------------
    print("\n[1] source_url fixes (verified from screenshots):")
    for r in rows:
        if r["id"] in URL_FIXES:
            wrong, right = URL_FIXES[r["id"]]
            assert r["source_url"] == wrong, (
                f"{r['id']}: expected {wrong}, found {r['source_url']} — corpus moved, stop.")
            r["source_url"] = right
            print(f"    {r['id']}")
            print(f"      {wrong.split('/')[-1]:>4}  ->  {right.split('/')[-1]}")
        if r["id"] in UNVERIFIED_URL:
            r["source_url"] = UNVERIFIED_URL[r["id"]]
            r["source_url_unverified"] = True
            print(f"    {r['id']}")
            print(f"        33  ->  /landing/notices  [FLAGGED source_url_unverified]")

    # -- 2. char_len ------------------------------------------------------
    print("\n[2] char_len recompute:")
    fixed = 0
    for r in rows:
        actual = len(r["text"])
        if r.get("char_len") != actual:
            print(f"    {r['id'][:52]:54} {r.get('char_len')} -> {actual}")
            r["char_len"] = actual
            fixed += 1
    print(f"    {fixed} corrected")

    # -- 3. append --------------------------------------------------------
    existing = {r["id"] for r in rows}
    for rec in NEW_RECORDS:
        assert rec["id"] not in existing, f"duplicate id {rec['id']}"
        rec["char_len"] = len(rec["text"])
        rows.append(rec)
    print(f"\n[3] appended {len(NEW_RECORDS)} records -> {len(rows)} total")

    # -- validate ---------------------------------------------------------
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "duplicate ids"
    required = {"id", "text", "category", "subcategory", "heading", "lang",
                "source_url", "official", "content", "char_len"}
    for r in rows:
        missing = required - set(r)
        assert not missing, f"{r['id']} missing {missing}"
        assert r["text"] == r["content"], f"{r['id']}: text != content"
        assert r["char_len"] == len(r["text"]), f"{r['id']}: char_len wrong"
    urls = {}
    for r in rows:
        if r["section"] == "notice" and not r.get("source_url_unverified"):
            assert r["source_url"] not in urls, (
                f"two notices share {r['source_url']}: {urls[r['source_url']]} / {r['id']}")
            urls[r["source_url"]] = r["id"]
    unver = [r["id"] for r in rows if r.get("source_url_unverified")]
    if unver:
        print(f"[!] {len(unver)} record(s) flagged source_url_unverified: {unver}")
    print("[✓] validation passed: unique ids, required fields, text==content, "
          "char_len exact, no notice URL collisions")

    with OUT.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(ordered(r), ensure_ascii=False) + "\n")

    from collections import Counter
    print("\ncategories:", dict(sorted(Counter(r["category"] for r in rows).items())))
    print("sections  :", dict(sorted(Counter(r["section"] for r in rows).items())))


if __name__ == "__main__":
    main()
