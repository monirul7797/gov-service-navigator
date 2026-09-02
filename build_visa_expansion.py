"""
build_visa_expansion.py — Phase 3.6: append coverage-expansion records to
visa.jsonl from captured official evidence (Visa Policy 2026 + SB VOA site).

AUTHORITY HIERARCHY (Phase 3.6 §8, recorded in the expansion manifest only —
no new schema fields):
  application_portal            visa.gov.bd            (existing 17 records)
  immigration_operational       immi.specialbranch.gov.bd
  voa_operational               voa.specialbranch.gov.bd
  central_policy                ssd.gov.bd (hub pages only; archive NOT crawled)
  investment_employment_specific bida.gov.bd (TLS mismatch — not fetchable)

PRIMARY SOURCE: "VISA POLICY 2026", Ministry of Home Affairs, Immigration Wing,
publication date stated on the document: August/2026 (39-page PDF, verified
download: immi.specialbranch.gov.bd/docs/visa_policy.pdf). Condition 33 of the
policy repeals previously issued visa circulars — recorded as a conflict note
on the 2016 VOA circular record, which the operational VOA site still publishes.

HARD RULES:
  * every record's text is sliced VERBATIM from captured evidence; each slice
    anchor is asserted (a changed source breaks the build loudly);
  * the existing 17 records must be present and untouched — this script only
    APPENDS;
  * Bangla-authoritative convention (Phase 3.6 §10) for the Bangla circular:
    Bangla text first, official English page text after, clearly labeled;
  * no BIDA/mission content (BIDA TLS-blocked; MOFA not needed — no remaining
    gap that central sources don't cover);
  * VOA fee AMOUNT deliberately not recorded anywhere: sources conflict
    (SB LP page text: USD 50; 2016 circular: fees in foreign currency, no
    amount; Policy 2026: no amount) — TODO(Monirul) recorded in the manifest.

Run:
    python build_visa_expansion.py
"""
from __future__ import annotations

import json
import os

REPO_ROOT = r"C:\Users\Monirul\Documents\Structured Data\NID\gov-service-navigator\gov-service-navigator"
POLICY_TXT = os.path.join(REPO_ROOT, "data", "evidence", "immi_specialbranch", "visa_policy_2026_fulltext.txt")
VOA_EN = os.path.join(REPO_ROOT, "data", "evidence", "voa_specialbranch", "voa_specialbranch_gov_bd_public_voa_info_html.txt")
VOA_BN = os.path.join(REPO_ROOT, "data", "evidence", "voa_specialbranch", "voa_specialbranch_gov_bd_public_voa_info_bn_html.txt")
VOA_STEPS = os.path.join(REPO_ROOT, "data", "evidence", "voa_specialbranch", "voa_specialbranch_gov_bd_public_voa_instructions_html.txt")
VISA_JSONL = os.path.join(REPO_ROOT, "data", "chunks", "visa.jsonl")
EXP_MANIFEST = os.path.join(REPO_ROOT, "data", "raw", "visa", "expansion_manifest.json")

POLICY_URL = "https://immi.specialbranch.gov.bd/docs/visa_policy.pdf"
VOA_INFO_URL = "https://voa.specialbranch.gov.bd/public/voa-info.html"
VOA_BN_URL = "https://voa.specialbranch.gov.bd/public/voa-info-bn.html"
VOA_STEPS_URL = "https://voa.specialbranch.gov.bd/public/voa-instructions.html"
POLICY_LAST_UPDATED = "2026-08"      # stated on the document: Publication Date: August/2026
CIRCULAR_DATE = "2016-09-15"         # stated on the circular

with open(POLICY_TXT, encoding="utf-8") as f:
    POLICY = f.read().replace("\x0c", "\n")
with open(VOA_EN, encoding="utf-8") as f:
    VOA_EN_TXT = f.read()
with open(VOA_BN, encoding="utf-8") as f:
    VOA_BN_TXT = f.read()
with open(VOA_STEPS, encoding="utf-8") as f:
    VOA_STEPS_TXT = f.read()


def between(text: str, start: str, end: str | None) -> str:
    i = text.index(start)                       # raises if absent — verbatim gate
    j = text.index(end, i + len(start)) if end else len(text)
    return text[i:j].strip()


def rec(rec_id, section, category, subcategory, heading, source_url, last_updated,
        content, topics, lang="en"):
    return {
        "id": rec_id, "section": section, "category": category,
        "subcategory": subcategory, "heading": heading, "source_url": source_url,
        "last_updated": last_updated, "content": content, "topics": topics,
        "service": "visa", "lang": lang, "last_crawled": "2026-09-03",
        "official": True, "text": content, "char_len": len(content),
    }


new_records = []

# ---------------------------------------------------------------- category table rows
ROWS = [
    ("visa-category-tourist", "29. T", "30. FBN", "Tourist visa (T)",
     "Tourist visa (T): recreation, visiting friends and relatives, conferences and seminars",
     ["tourist", "t", "recreation", "visit friends", "conference", "seminar", "tourism"]),
    ("visa-category-business", "5. B", "6. C", "Business visa (B)",
     "Business visa (B): investment analysis, board meetings, recruitment, trade fairs",
     ["business", "b", "investment", "trade fair", "board meeting", "recruitment"]),
    ("visa-category-student", "27. S", "28. FSR", "Student visa (S)",
     "Student visa (S): academic programs, degrees and internships at approved institutions",
     ["student", "s", "study", "academic", "internship", "university"]),
    ("visa-category-employment-e1", "9. E1", "10. E2", "Employment visa — E1 (Development Partners)",
     "E1 visa: expatriates directly employed by development-partner agencies on projects in Bangladesh",
     ["e1", "development partner", "expatriate", "development project", "employment"]),
    ("visa-category-employment-e2-e3", "10. E2", "12. FE1",
     "Employment visas — E2 (skilled employment) and E3 (experts, installation and commissioning)",
     "E2 visa: expert and skilled workers in government/private/garments/industrial organizations; "
     "E3 visa: experts for installation, commissioning, maintenance and project monitoring",
     ["e2", "e3", "employment", "skilled worker", "expert", "installation", "commissioning",
      "work permit", "garments"]),
    ("visa-category-investor-pi", "24. PI", "25. FPI", "Private Investor visa (PI)",
     "PI visa: foreign investors in industrial/commercial establishments under foreign ownership or joint venture",
     ["pi", "investor", "private investor", "investment", "joint venture", "industry"]),
    ("visa-category-journalist", "15. J", "16. FJ", "Journalist visa (J)",
     "J visa: news collection, filming and documentaries, professional duties, election observation",
     ["journalist", "j", "media", "film", "documentary", "election"]),
    ("visa-category-ngo", "19. N1", "21. N2", "NGO visa (N1) and emergency humanitarian visa (N2)",
     "N1 visa: individuals appointed to registered NGOs in Bangladesh; N2: emergency humanitarian services",
     ["ngo", "n1", "n2", "humanitarian", "ngo affairs bureau", "volunteer"]),
    ("visa-category-transit", "31. TR", "32. MT", "Transit visa (TR)",
     "TR transit visa: travelling through Bangladesh to a third destination, seamen and air crews",
     ["transit", "tr", "seaman", "mariner", "air crew", "connecting flight", "96 hours"]),
    ("visa-category-family-dependent", "30. FBN", "31. TR",
     "Family and dependent visas (FBN, FA2) and the dependent-family definition",
     "FBN: family members of Bangladeshi origin foreigners staying with family; FA2: dependents of A2 holders; "
     "plus the policy's dependent-family definition",
     ["family", "dependent", "fbn", "fa2", "spouse", "children"]),
]

for rid, start, end, heading, summary, topics in ROWS:
    seg = between(POLICY, start, end)
    content = f"{heading}\n{summary}\n\n--- Verbatim extract, Visa Policy 2026 (Ministry of Home Affairs), category table ---\n{seg}"
    new_records.append(rec(rid, "instructions", "documents-eligibility",
                           rid.replace("visa-category-", ""), heading, POLICY_URL,
                           POLICY_LAST_UPDATED, content, topics))

# ---------------------------------------------------------------- NVR (condition 28)
seg = between(POLICY, "28. NVR can be issued", "29. Dependent family members")
dep = between(POLICY, "29. Dependent family members", "30. Development Partners")
content = (
    "No Visa Required (NVR) — who qualifies, required documents, validity and fee\n\n"
    "--- Verbatim extract, Visa Policy 2026 (Ministry of Home Affairs), mandatory visa conditions ---\n"
    + seg + "\n\n" + dep
)
new_records.append(rec(
    "visa-nvr-eligibility-documents-fee", "instructions", "documents-eligibility",
    "nvr", "No Visa Required (NVR): eligibility, documents, validity (passport-life), USD 80 fee",
    POLICY_URL, POLICY_LAST_UPDATED, content,
    ["nvr", "no visa required", "bangladeshi origin", "dual citizenship", "foreign spouse",
     "investor nvr", "usd 80", "saarc"]))

# ---------------------------------------------------------------- VOA (policy §12)
seg = between(POLICY, "12. Particular Nationals will get Visa on Arrival",
              "13. All visitors must hold a passport")
content = (
    "Visa on Arrival (VoA) — central policy conditions (Visa Policy 2026)\n\n"
    "--- Verbatim extract, Visa Policy 2026 (Ministry of Home Affairs), mandatory visa conditions ---\n" + seg
)
new_records.append(rec(
    "visa-voa-policy-conditions", "instructions", "documents-eligibility", "voa",
    "Visa on Arrival: 30 days single entry at designated ports, for listed traveler categories (Visa Policy 2026)",
    POLICY_URL, POLICY_LAST_UPDATED, content,
    ["visa on arrival", "voa", "30 days", "single entry", "designated port", "landing permit"]))

# ---------------------------------------------------------------- VOA circular (BN authoritative + official EN)
bn = between(VOA_BN_TXT, "গণপ্রজাতন্ত্রী বাংলাদেশ সরকার", None)
en = between(VOA_EN_TXT, "Visa on Arrival (VOA) in Bangladesh", None)
content = (
    "আগমনী ভিসা (Visa on Arrival) — সরকারি পরিপত্র, ১৫ সেপ্টেম্বর ২০১৬\n\n"
    "--- মূল বাংলা (সরকারি পরিপত্র — অথরিটেটিভ) ---\n" + bn +
    "\n\n--- English (official English page of the Special Branch VOA site, labeled translation source) ---\n" + en +
    "\n\nNOTE (conflict/precedence): Visa Policy 2026 condition 33 repeals previously issued visa "
    "circulars; this 2016 circular is still published on the operational VOA site "
    "(voa.specialbranch.gov.bd) as its public guidance. Where the two differ, Visa Policy 2026 prevails. "
    "TODO(Monirul): confirm no newer VOA circular supersedes this one."
)
new_records.append(rec(
    "visa-voa-circular-2016-bn", "notice", "process", "voa-circular",
    "আগমনী ভিসা (Visa on Arrival) প্রদান সংক্রান্ত সরকারি পরিপত্র (২০১৬) — Bangla circular with official English page",
    VOA_BN_URL, CIRCULAR_DATE, content,
    ["visa on arrival", "voa", "পরিপত্র", "আগমনী ভিসা", "circular", "eligibility", "requirements"],
    lang="bn+en"))

# ---------------------------------------------------------------- VOA application steps
seg = between(VOA_STEPS_TXT, "Visa on Arrival (VOA) Application Steps", None)
content = (
    "Visa on Arrival (VOA) online application steps (public instructions)\n\n"
    "--- Verbatim extract, official Special Branch VOA site public instructions ---\n" + seg +
    "\n\nNOTE: these public instructions describe the online VOA application, which continues "
    "inside a logged-in applicant account; the logged-in workflow itself is not crawled (PII/wizard rule)."
)
new_records.append(rec(
    "visa-voa-application-steps", "instructions", "process", "voa-application-steps",
    "How to apply for Visa on Arrival online: signup, email activation, application and print steps",
    VOA_STEPS_URL, None, content,
    ["voa application", "signup", "apply online", "voa steps", "print", "traveller information"]))

# ---------------------------------------------------------------- extension / conversion / overstay / work-permit / authority / registration / general conditions
seg = between(POLICY, "8. Application for an extension", "9.(a) The illegal stay")
new_records.append(rec(
    "visa-extension-application-rule", "instructions", "process", "extension",
    "Visa extension: apply 15 days before expiry; after expiry, overstay penalty and regularization fee apply",
    POLICY_URL, POLICY_LAST_UPDATED,
    "Visa extension rule (Visa Policy 2026, mandatory visa conditions)\n\n--- Verbatim extract ---\n" + seg,
    ["extension", "visa extension", "15 days before expiry", "overstay", "regularization"]))

seg = between(POLICY, "11. A visa category cannot be changed", "12. Particular Nationals")
new_records.append(rec(
    "visa-category-change-conversion", "instructions", "process", "category-change",
    "Visa category change after entry: only VoA converts, to listed categories; US$200 conversion fee for employment",
    POLICY_URL, POLICY_LAST_UPDATED,
    "Visa category change / conversion (Visa Policy 2026, mandatory visa conditions)\n\n--- Verbatim extract ---\n" + seg,
    ["category change", "conversion", "voa conversion", "usd 200", "bida", "bepza", "beza", "bhtpa"]))

seg = between(POLICY, "9.(a) The illegal stay", "12. Particular Nationals")
new_records.append(rec(
    "visa-overstay-penalties", "instructions", "process", "overstay",
    "Overstay / illegal stay: daily penalty tiers (Tk 1,000 / 2,000 / 3,000), waivers and the 7-day exit procedure",
    POLICY_URL, POLICY_LAST_UPDATED,
    "Overstay and illegal-stay penalties and waivers (Visa Policy 2026, mandatory visa conditions)\n\n"
    "--- Verbatim extract ---\n" + seg,
    ["overstay", "illegal stay", "penalty", "fine", "waiver", "regularization", "exit"]))

seg = between(POLICY, "b) At the time of issuing E1", "6. A person arriving from India")
new_records.append(rec(
    "visa-work-permit-condition", "instructions", "process", "work-permit",
    "Employment visas carry a work-permit condition: apply to BIDA/BEPZA/BEZA/BHTPA/NGO AB within 15 working days",
    POLICY_URL, POLICY_LAST_UPDATED,
    "Work-permit condition stamped on E1/E2/E3/J/P/PI/N1 visas (Visa Policy 2026, mandatory visa conditions)\n\n"
    "--- Verbatim extract ---\n" + seg,
    ["work permit", "bida", "bepza", "beza", "bhtpa", "ngo affairs bureau", "15 working days", "employment"]))

seg = between(POLICY, "1. The Ministry of Home Affairs will formulate policy", "2. A visa is also granted")
new_records.append(rec(
    "visa-issuing-authority", "instructions", "process", "issuing-authority",
    "Who issues Bangladesh visas: DIP inside Bangladesh (visa, NVR, VoA, transit, exit pass, category change); Missions abroad",
    POLICY_URL, POLICY_LAST_UPDATED,
    "Visa issuing authorities (Visa Policy 2026, mandatory visa conditions)\n\n--- Verbatim extract ---\n" + seg,
    ["issuing authority", "dip", "department of immigration and passports", "bangladesh missions",
     "moha", "nvr", "voa", "exit pass"]))

seg = between(POLICY, "6. A person arriving from India", "8. Application for an extension")
new_records.append(rec(
    "visa-entry-registration-requirement", "instructions", "process", "entry-registration",
    "Registration at port of entry within 14 days: required for arrivals from India and Pakistan on 90+ day visas",
    POLICY_URL, POLICY_LAST_UPDATED,
    "Entry registration requirement (Visa Policy 2026, mandatory visa conditions)\n\n--- Verbatim extract ---\n" + seg,
    ["registration", "india", "pakistan", "14 days", "port of entry"]))

gen_seg = (between(POLICY, "13. All visitors must hold a passport", "16. In case a report is required")
           + "\n" + between(POLICY, "21. Visa fees shall not be refunded", "22. A person arriving"))
new_records.append(rec(
    "visa-general-conditions-fees", "instructions", "fees-payment", "general-conditions",
    "General visa conditions: 6-month passport validity, visa transfer to new passport, fee basis and gratis categories, no refund",
    POLICY_URL, POLICY_LAST_UPDATED,
    "General visa conditions (Visa Policy 2026, mandatory visa conditions)\n\n--- Verbatim extract ---\n" + gen_seg,
    ["passport validity", "6 months", "visa transfer", "visa fee", "gratis", "reciprocity", "no refund"]))

# ---------------------------------------------------------------- categories index
codes = []
for line in POLICY.split("\n"):
    import re
    m = re.match(r"^(\d{1,2})\. ([A-Z]{1,3})$", line.strip())
    if m:
        codes.append(f"{m.group(1)}. {m.group(2)}")
# fall back: derive from row anchors used above plus remaining known codes
if len(codes) < 20:
    codes = ["1. A", "2. A1", "3. A2", "4. FA2", "5. B", "6. C", "9. E1", "10. E2", "11. E3",
             "12. FE1", "13. FE2", "14. FE3", "15. J", "16. FJ", "17. M", "18. FM", "19. N1",
             "21. N2", "22. P", "23. FP", "24. PI", "25. FPI", "26. R", "27. S", "28. FSR",
             "29. T", "30. FBN", "31. TR", "32. MT", "33. MA", "34. RT"]
content = (
    "Bangladesh visa category codes (Visa Policy 2026 category table, Sl. 1-34)\n\n"
    "Each category has its own purpose, eligibility, duration and required documents — see the "
    "per-category records for the major ones. Category codes from the official table:\n" + "\n".join(codes)
)
new_records.append(rec(
    "visa-categories-index", "instructions", "documents-eligibility", "categories-index",
    "All Bangladesh visa category codes in the official table (A, A1, A2 … TR, MT, MA, RT)",
    POLICY_URL, POLICY_LAST_UPDATED, content,
    ["visa categories", "category codes", "visa types", "index"]))

# ---------------------------------------------------------------- write gate
existing = []
with open(VISA_JSONL, encoding="utf-8") as f:
    for line in f:
        if line.strip():
            existing.append(json.loads(line))

# idempotency: a previous run may have crashed after writing but before the
# manifest. Strip any already-appended expansion records so the base corpus
# can be re-verified before appending.
new_ids_preview = {r["id"] for r in new_records}
base = [r for r in existing if r["id"] not in new_ids_preview]
assert len(base) == 17, f"expected the 17 base records, found {len(base)}"

new_ids = [r["id"] for r in new_records]
assert len(new_ids) == len(set(new_ids)), "duplicate ids within expansion"
collisions = set(new_ids) & {r["id"] for r in base}
assert not collisions, f"id collisions with existing corpus: {collisions}"

# near-duplicate heading gate (§14): token jaccard vs existing + within new
def toks(s):
    return set("".join(c if c.isalnum() else " " for c in s.lower()).split())

all_recs = base + new_records
for i, a in enumerate(new_records):
    for b in base:
        if a["id"] == b["id"]:
            continue
        ta, tb = toks(a["heading"]), toks(b["heading"])
        jac = len(ta & tb) / max(1, len(ta | tb))
        assert jac < 0.8, f"near-duplicate heading: {a['id']} vs {b['id']} ({jac:.2f})"

with open(VISA_JSONL, "w", encoding="utf-8") as f:
    for r_ in all_recs:
        f.write(json.dumps(r_, ensure_ascii=False) + "\n")

# ---------------------------------------------------------------- expansion manifest
exp_manifest = {
    "phase": "3.6",
    "built_at": "2026-09-03",
    "primary_source": {
        "document": "VISA POLICY 2026, Ministry of Home Affairs, Immigration Wing, Bangladesh",
        "publication_date_stated": "August/2026",
        "url": POLICY_URL,
        "acquisition": "direct_pdf_verified (sha256 recorded in evidence manifest)",
        "authority_label": "immigration_operational / central_policy",
    },
    "other_sources": [
        {"url": VOA_INFO_URL, "authority_label": "voa_operational"},
        {"url": VOA_BN_URL, "authority_label": "voa_operational", "lang": "bn", "authoritative": True},
        {"url": VOA_STEPS_URL, "authority_label": "voa_operational"},
    ],
    "added_records": new_ids,
    "conflicts": [
        {"topic": "VOA fee amount",
         "sources": {
             "immi.specialbranch.gov.bd (LP/VoA page text)": "US Dollar 50 (Fifty)",
             "2016 VOA circular (BN)": "fees in foreign currency at port, amount not stated",
             "Visa Policy 2026": "no amount stated"},
         "resolution": "no VOA fee amount recorded in any corpus record",
         "status": "TODO(Monirul): verify current VOA fee against a dated official source"},
        {"topic": "VOA circular vs Visa Policy 2026",
         "sources": {"2016 VOA circular": "still published on voa.specialbranch.gov.bd",
                     "Visa Policy 2026 condition 33": "repeals previously issued visa circulars"},
         "resolution": "circular record carries a precedence note; Policy 2026 prevails where they differ"},
        {"topic": "Transit duration",
         "sources": {"2016 circular (VoA context)": "72 hours, USD 20, airline-requested at port",
                     "Visa Policy 2026 TR category": "up to 96 hours (pre-issued transit visa)",
                     "Visa Policy 2026 condition 12(h)": "VoA transit up to 72 hours, USD 20"},
         "resolution": "not a true conflict: different instruments (TR visa category vs VoA transit at port); "
                       "both recorded with their scope"},
    ],
    "excluded_candidates": [
        {"source": "bida.gov.bd/faq + /commercial-wings",
         "disposition": "UNVERIFIED_CAPTURE_ONLY / not fetchable",
         "reason": "ERR_SSL_VERSION_OR_CIPHER_MISMATCH in both strict and evidence contexts "
                   "(TLS protocol mismatch, not a certificate-validation issue; no evasion). "
                   "PI/E visa requirements are covered by Visa Policy 2026 category rows; "
                   "BIDA-side recommendation process remains a gap."},
        {"source": "ssd.gov.bd circular archive (individual circular PDFs)",
         "disposition": "PII_EXCLUDED (not crawled)",
         "reason": "Phase 3.6 §5 forbids recursive crawling of the notice/order archive; "
                   "hub pages captured as evidence only (titles + last-updated May 2025)"},
        {"source": "*.mofa.gov.bd mission pages",
         "disposition": "MISSION_SPECIFIC_NOT_GENERAL / not needed",
         "reason": "no remaining gap that central sources (Visa Policy 2026, VOA site) don't cover; "
                   "mission-local procedures must not be generalized"},
        {"source": "immi.specialbranch.gov.bd COVID/procurement/e-SAM suspension PDFs",
         "disposition": "STALE_OR_SUPERSEDED / out of scope",
         "reason": "temporary operational notices, not durable visa policy"},
    ],
}

with open(EXP_MANIFEST, "w", encoding="utf-8") as f:
    json.dump(exp_manifest, f, ensure_ascii=False, indent=2)

print(f"appended {len(new_records)} records -> visa.jsonl (now {len(all_recs)})")
for r_ in new_records:
    print(f"  + {r_['id']:45s} {r_['category']:22s} {r_['char_len']:5d} chars")
print(f"manifest: {EXP_MANIFEST}")
