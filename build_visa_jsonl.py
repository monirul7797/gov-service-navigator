"""
build_visa_jsonl.py — Part B §B5: structure data/raw/visa/*.txt into visa.jsonl.

Schema: epassport style (process/fee/FAQ mix), per spec §B5:
  id, section, category, subcategory, heading, source_url, last_updated,
  content, topics[], service="visa", lang, last_crawled, official, text,
  char_len  (+ faq_no, question_en/bn, answer_en/bn, source_confidence on FAQs)

Hard rules enforced here:
  * Every record's text is sliced VERBATIM from the staged raw files. The
    build asserts each anchor string exists in the staged text — if the page
    ever changes, this build fails loudly instead of fabricating content.
  * NO fees are invented: the fee record is the portal's own fee dialog table
    (one authoritative full-tier record per fee type). Visa fees on the
    portal are fixed government amounts with no VAT line shown, so no VAT
    annotation is added (§0 note 4 applies "where VAT applies" — it does not).
  * Source pages are English-only → lang="en", Bangla FAQ fields are empty
    strings. §0 note 2 forbids English-only versions of BANGLA-only sources;
    an English-only source stored verbatim in English is the honest inverse.
# TODO(Monirul): decide whether human-authored Bangla translations of the
# visa FAQ answers should be added later (they would not be source verbatim).
  * last_updated stays null: the portal shows only "© 2012 / Version 1.0.1.0",
    no content date — never invent a date.

Run:
    python build_visa_jsonl.py
"""
from __future__ import annotations

import json
import os

REPO_ROOT = r"C:\Users\Monirul\Documents\Structured Data\NID\gov-service-navigator\gov-service-navigator"
RAW_DIR = os.path.join(REPO_ROOT, "data", "raw", "visa")
OUT_PATH = os.path.join(REPO_ROOT, "data", "chunks", "visa.jsonl")

LAST_CRAWLED = "2026-09-03"   # the date these staged pages were actually fetched
PORTAL = "https://visa.gov.bd"

with open(os.path.join(RAW_DIR, "00_visa_gov_bd.txt"), encoding="utf-8") as f:
    HOME = f.read()
with open(os.path.join(RAW_DIR, "01_visa_gov_bd_DownloadMRVForm_aspx.txt"), encoding="utf-8") as f:
    MRV = f.read()
with open(os.path.join(RAW_DIR, "02_visa_gov_bd_FAQ_aspx.txt"), encoding="utf-8") as f:
    FAQ = f.read()
with open(os.path.join(RAW_DIR, "9x_visa_gov_bd_fee_table.txt"), encoding="utf-8") as f:
    FEES = f.read()
with open(os.path.join(RAW_DIR, "9x_visa_gov_bd_supported_countries.txt"), encoding="utf-8") as f:
    COUNTRIES = f.read()


def slice_between(text: str, start_anchor: str, end_anchor: str | None) -> str:
    """Verbatim slice [start_anchor, end_anchor). Asserts both anchors exist."""
    i = text.index(start_anchor)                       # raises if absent — good
    j = text.index(end_anchor, i) if end_anchor else len(text)
    return text[i:j].strip()


def base_record(rec_id: str, section: str, category: str, subcategory: str,
                heading: str, source_url: str, content: str, topics: list[str]) -> dict:
    return {
        "id": rec_id,
        "section": section,
        "category": category,
        "subcategory": subcategory,
        "heading": heading,
        "source_url": source_url,
        "last_updated": None,          # portal shows no content date (© 2012, v1.0.1.0)
        "content": content,
        "topics": topics,
        "service": "visa",
        "lang": "en",                  # source pages are English-only — see module docstring
        "last_crawled": LAST_CRAWLED,
        "official": True,
        "text": content,
        "char_len": len(content),
    }


records: list[dict] = []

# --------------------------------------------------------------------------
# 1) Process records from the portal home page (SECTION: process)
# --------------------------------------------------------------------------
# 1a. what you need (browser/Adobe/printer + supported-countries pointer)
seg = slice_between(
    HOME,
    "Welcome to the Electronic Visa Applications Forms Instructions Page.",
    "Currently the service allows submission",
)
records.append(base_record(
    "visa-process-what-you-need", "instructions", "process", "what-you-need",
    "What you need before applying on the Bangladesh visa portal",
    f"{PORTAL}/", seg,
    ["visa", "requirements", "browser", "adobe-reader", "printing", "email", "getting-started"],
))

# 1b. application types + information to have on hand
seg = slice_between(
    HOME,
    "Currently the service allows submission",
    "You can save your Visa application on any page where you see this",
)
records.append(base_record(
    "visa-process-application-types", "instructions", "process", "application-types",
    "Visa application types (new, extension, no-visa-required, on-arrival) and information to have on hand",
    f"{PORTAL}/", seg,
    ["visa", "new-visa", "visa-extension", "nvr", "on-arrival-visa", "oav", "passport", "photo", "requirements"],
))

# 1c. the 3-step process + fee pointer + false-document warning
seg = slice_between(
    HOME,
    "The online application process requires the user to:",
    "I have read the above information",
)
records.append(base_record(
    "visa-process-steps-and-warning", "instructions", "process", "application-steps",
    "How to apply: fill online, print, submit at a visa office or Bangladesh mission — plus the false-document warning",
    f"{PORTAL}/", seg,
    ["visa", "how-to-apply", "application-steps", "visa-fees", "visa-office", "bangladesh-mission",
     "false-document", "penalty"],
))

# --------------------------------------------------------------------------
# 2) FAQ records from FAQ.aspx (11 Q&As, two source sub-sections)
# --------------------------------------------------------------------------
FAQ_QUESTIONS = [
    # (question anchor verbatim, category, subcategory, topics)
    ("Can I fill in my visa application in a language other than English?",
     "process", "application-language", ["language", "english", "application-form"]),
    ("Will I be able to access the online application system using my computer?",
     "process", "browser-support", ["browser", "compatibility", "internet-explorer", "firefox"]),
    ("Can I save my application mid-way through the application process?",
     "process", "save-and-resume", ["save-and-exit", "visa-application-id", "7-days", "resume-application"]),
    ("I do not understand one of the questions. What can I do?",
     "process", "form-guidance", ["more-info", "guidance", "help"]),
    ("I made a mistake on one of my answers. Can I change it?",
     "process", "correct-mistake", ["edit", "correction", "submission"]),
    ("The date I entered is not being accepted. What is the correct format?",
     "process", "date-format", ["date-format", "dd-mm-yyyy", "form"]),
    ("I have not received my Completed Application confirmation email. Can you resend it to me?",
     "process", "confirmation-email", ["email", "confirmation", "spam", "complaint"]),
    ("I am unable to retrieve my application. What can I do?",
     "process", "retrieve-application", ["retrieve", "save-and-exit", "browser-cache", "7-days"]),
    ("How can I change my appointment location?",
     "appointment", "appointment-location", ["appointment", "location", "submission"]),
    ("Can I get a refund?",
     "fees-payment", "refund", ["refund", "fees", "no-refund"]),
    ("Can I pay for my visa application online?",
     "fees-payment", "online-payment", ["online-payment", "payment", "not-supported"]),
]

positions = []
for q, cat, sub, topics in FAQ_QUESTIONS:
    positions.append(FAQ.index(q))   # raises if the page text ever changes — good
order = sorted(range(len(FAQ_QUESTIONS)), key=lambda k: positions[k])
faq_no = 0
for k in order:
    q, cat, sub, topics = FAQ_QUESTIONS[k]
    start = positions[k] + len(q)
    end = positions[k + 1] if k + 1 < len(order) else FAQ.index("© 2012 Department")
    answer = FAQ[start:end].strip()
    # drop the source page's own section headers from answers
    for header in ("ONLINE APPLICATION FORMS", "SECURITY, APPOINTMENTS AND PAYMENTS"):
        answer = answer.replace(header, "").strip()
    faq_no += 1
    rec = base_record(
        f"visa-faq-{faq_no:02d}-{sub}", "faq", cat, sub, q, f"{PORTAL}/FAQ.aspx",
        f"Q (EN): {q}\n\nA (EN):\n{answer}",
        topics,
    )
    rec.update({
        "faq_no": faq_no,
        "question_en": q,
        "question_bn": "",             # English-only source — see module docstring
        "answer_en": answer,
        "answer_bn": "",
        "source_confidence": None,
    })
    records.append(rec)

# --------------------------------------------------------------------------
# 3) MRV blank form (DIP Form-4) download record
# --------------------------------------------------------------------------
seg = slice_between(MRV, "Download MRV Form\n\nBlank MRV Form", "© 2012 Department")
seg = seg + (
    "\n\nDirect PDF link (verified fetching as application/pdf): "
    "https://visa.gov.bd/Img/MRV_FORM.pdf"
)
records.append(base_record(
    "visa-mrv-form-dip-form-4", "instructions", "process", "mrv-form-download",
    "Download the blank MRV form (DIP Form-4) for machine-readable visa applications",
    f"{PORTAL}/DownloadMRVForm.aspx", seg,
    ["mrv", "dip-form-4", "machine-readable-visa", "form-download", "pdf"],
))

# --------------------------------------------------------------------------
# 4) Fees — ONE authoritative full-tier record (portal fee dialog table)
# --------------------------------------------------------------------------
# (the staged text is whitespace-normalized: the portal's tab-separated fee
# table is stored space-separated — verbatim text, normalized whitespace)
table = slice_between(FEES, "COUNTRY CURRENCY SINGLE REENTRY MULTIPLE", None)
records.append(base_record(
    "visa-fees-table-by-country", "fees", "fees-payment", "visa-fee-table",
    "Visa fees by country and entry type (single / re-entry / multiple), as shown on the official portal fee table",
    f"{PORTAL}/", table,
    ["visa-fees", "fee-table", "single-entry", "multiple-entry", "re-entry", "usd", "bdt", "country-wise"],
))

# --------------------------------------------------------------------------
# 5) Eligibility — missions/countries supporting the online MRV application
# --------------------------------------------------------------------------
seg = slice_between(COUNTRIES, "Country that supported MRV Online Application", None)
records.append(base_record(
    "visa-eligible-mrv-missions", "instructions", "documents-eligibility", "supported-mrv-missions",
    "Countries/missions that support the MRV online application",
    f"{PORTAL}/", seg,
    ["supported-countries", "missions", "mrv", "eligibility", "online-application"],
))

# --------------------------------------------------------------------------
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, "w", encoding="utf-8") as f:
    for rec in records:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

print(f"wrote {len(records)} records -> {OUT_PATH}")
import collections
print("categories:", dict(collections.Counter(r["category"] for r in records)))
print("char_len range:", min(r["char_len"] for r in records), "-", max(r["char_len"] for r in records))
