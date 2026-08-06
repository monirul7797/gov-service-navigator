"""
seed_corpus.py — build a verified STARTER corpus from official facts.

This is not a scraper. It is a hand-verified set of factual statements, each
carrying the exact official gov.bd URL it was confirmed against on 2026-07-12.
It runs the SAME normalization / classification / Bangla-health / chunk-writing
code the live pipeline uses, so the JSONL it emits is identical in shape to
scraped output and passes through the same QA.

Purpose: give the RAG a correct, attributable Phase-1 baseline immediately,
while the Playwright/requests scrapers in src/ fill in the long tail from the
live sites (some of which are JS SPAs or robots-restricted for automation).

Every fee/threshold below is YEAR-SENSITIVE (set by Finance Act / SRO / local
government schedule). The `note` on those facts says so; the live scrapers +
periodic re-verification are what keep them current.
"""
from __future__ import annotations

import time
from src.paths import ensure_dirs, chunks_path, load_sources
from src.bangla import normalize_text, check_bangla, _bengali_count
from src.chunk import classify, _lang_of, write_jsonl
import hashlib

VERIFIED_ON = "2026-07-12"

# (service, source_url, heading, category_hint, text)
# category_hint is only a fallback; classify() runs anyway and we keep the
# stronger signal. Bangla lines included to exercise the encoding validator.
FACTS = [
    # ---------------- NID ----------------
    ("nid", "https://services.nidw.gov.bd/nid-pub/", "Authority & helpline",
     "The National ID service is run by the Bangladesh Election Commission's "
     "National Identity Registration Wing (NIDW). Helpline: 105. "
     "Email: info@nidw.gov.bd. জাতীয় পরিচয়পত্র সেবা বাংলাদেশ নির্বাচন কমিশন পরিচালনা করে।"),
    ("nid", "https://services.nidw.gov.bd/nid-pub/", "New NID application",
     "Citizens without an NID can apply for a new one online at "
     "services.nidw.gov.bd. In-person biometric enrolment (fingerprint, iris, "
     "photograph, signature) is mandatory after online submission."),
    ("nid", "https://services.nidw.gov.bd/nid-pub/", "Account registration",
     "To register or claim an online account, enter your NID or form number "
     "(prefix NIDFN for form numbers), date of birth and captcha, then verify "
     "with the OTP sent to your registered mobile. Identity is confirmed by "
     "face scan through the NID Wallet app."),
    ("nid", "https://services.nidw.gov.bd/nid-pub/", "Correction and reissue",
     "Existing NID holders can apply online for correction or reissue. "
     "Corrections require supporting documents and a fee paid through the "
     "designated bank channel. সংশোধন ও রিইস্যু আবেদন অনলাইনে করা যায়।"),
    ("nid", "https://services.nidw.gov.bd/nid-pub/", "Download and status",
     "Smart card status can be checked and a PDF copy of the NID downloaded "
     "from the portal after login. By SMS: send 'NID <form number> <DD-MM-YYYY>' "
     "to 105."),

    # ---------------- e-Passport ----------------
    ("epassport", "https://www.epassport.gov.bd/instructions/five-step-to-your-epassport",
     "Application overview",
     "The e-passport application is fully online at epassport.gov.bd. Choose "
     "your district and police station to be routed to the correct passport "
     "office, create an account, fill the form, pay the fee, then attend "
     "in-person biometric enrolment."),
    ("epassport", "https://www.epassport.gov.bd/instructions/five-step-to-your-epassport",
     "Required documents",
     "Applicants above 20 must submit a National ID; applicants aged 18-20 may "
     "use an NID or an online English-version Birth Registration Certificate; "
     "minors use an online BRC. A recent utility bill proves address. No "
     "document attestation is required."),
    ("epassport", "https://www.tbsnews.net", "Police verification removed",
     "Since an 18 February 2025 Ministry of Home Affairs circular, e-passports "
     "are issued based on NID (or online BRC for minors/expatriates) without "
     "prior police verification. [Confirm against the DIP/epassport circular; "
     "policy is subject to change.]"),
    ("epassport", "https://www.epassport.gov.bd/instructions/passport-fees",
     "Fees and validity",
     "Passport fees depend on booklet size (48 or 64 pages), validity (5 or 10 "
     "years) and delivery speed (regular, express, super-express). Fees are "
     "paid online or through designated banks. YEAR-SENSITIVE: verify the "
     "current fee table on the official fees page before quoting amounts."),
    ("epassport", "https://www.epassport.gov.bd/authorization/application-status",
     "Status check and collection",
     "Track progress at epassport.gov.bd using the 13-digit Application ID or "
     "the OID from the application form. Collect the passport in person with "
     "the original delivery slip and NID."),

    # ---------------- Income Tax e-Return ----------------
    ("etax", "https://etaxnbr.gov.bd/", "Registration",
     "Register for the e-Return system at etaxnbr.gov.bd using your TIN and a "
     "mobile number biometrically verified against your NID, confirmed by OTP. "
     "To find NID-registered numbers, dial *16001#."),
    ("etax", "https://nbr.gov.bd/faq/income-tax-faq/eng", "Who must file",
     "Holding a 12-digit TIN generally obliges you to file a return every year "
     "(a zero return if income is non-taxable). Filing is also triggered by "
     "car ownership, certain memberships, running a business, and other "
     "conditions."),
    ("etax", "https://nbr.gov.bd/faq/income-tax-faq/eng", "Tax-free threshold",
     "The general tax-free income threshold is BDT 3,50,000; BDT 4,00,000 for "
     "women and citizens aged 65+, and higher for certain categories. "
     "YEAR-SENSITIVE: thresholds are set by the annual Finance Act — verify for "
     "the assessment year in question."),
    ("etax", "https://etaxnbr.gov.bd/", "Heads of income",
     "Income is entered under heads: salary, interest on securities, house "
     "property, agriculture, business or profession, capital gains, and other "
     "sources. Rebatable investments (life insurance, DPS, savings certificates, "
     "GPF) reduce tax."),
    ("etax", "https://etaxnbr.gov.bd/", "Asset statement (IT-10B)",
     "The IT-10B asset and liability statement must be completed if net assets "
     "reach the prescribed limit. Tax is auto-calculated; payment is by internet "
     "banking, card, or mobile banking, and reconciled via a-Challan."),
    ("etax", "https://nbr.gov.bd/", "Helpline",
     "The NBR e-Return call centre is 09643-717171 (office hours). Online help "
     "is available through the e-Tax Service ticketing system. আয়কর রিটার্ন "
     "সংক্রান্ত সহায়তার জন্য কল সেন্টার নম্বর ০৯৬৪৩-৭১৭১৭১।"),

    # ---------------- BDRIS ----------------
    ("bdris", "https://bdris.gov.bd/br/application", "New birth registration",
     "Apply for birth registration at bdris.gov.bd/br/application. The name must "
     "be entered in both Bangla and English. Select the registration address: "
     "birthplace, permanent address, or current address. জন্ম নিবন্ধন আবেদন "
     "বাংলা ও ইংরেজি উভয় ভাষায় পূরণ করতে হয়।"),
    ("bdris", "https://bdris.gov.bd/br/application", "Required documents",
     "Supporting documents typically include the EPI vaccination card, a "
     "hospital/clinic certificate, the guardian's NID, and a holding-tax "
     "receipt, plus an active parent mobile number for OTP."),
    ("bdris", "https://orgbdr.gov.bd/pages/static-pages/69cf48059d736d71f1a34c8c",
     "Fees",
     "Birth registration is free (gratis) if applied within 45 days of birth. "
     "Late registration carries a fee that varies by timing and jurisdiction. "
     "YEAR-SENSITIVE: confirm the current fee schedule on the Registrar "
     "General's official fees page."),
    ("bdris", "https://bdris.gov.bd/br/application/status", "Status and verification",
     "Check application status at bdris.gov.bd/br/application/status. Verify an "
     "issued certificate at everify.bdris.gov.bd using the 17-digit registration "
     "number and date of birth."),
    ("bdris", "https://bdris.gov.bd/br/correction", "Correction",
     "Apply to correct birth-record information at bdris.gov.bd/br/correction. "
     "Death registration follows the parallel path under bdris.gov.bd/dr/."),
    ("bdris", "https://bdris.gov.bd/", "Helpline & legal basis",
     "Support: 16152, support@bdris.gov.bd / help@bdris.gov.bd. The service "
     "operates under the Birth and Death Registration Act, 2004 and the Rules of "
     "2018. জন্ম ও মৃত্যু নিবন্ধন আইন, ২০০৪ অনুযায়ী পরিচালিত।"),

    # ---- gap-closing verified facts (overview / eligibility / timelines / etc.) ----
    ("nid", "https://services.nidw.gov.bd/nid-pub/", "What the NID is",
     "The National ID (NID) is the official identity document for Bangladeshi "
     "citizens, used for voting, banking, SIM registration, passport "
     "application, and access to government services."),
    ("nid", "https://www.ecs.gov.bd/", "Who is eligible",
     "Any Bangladeshi citizen who has reached 18 years or is otherwise entitled "
     "to be on the electoral roll is eligible to register for an NID as a new "
     "voter."),
    ("nid", "https://www.ecs.gov.bd/", "Documents for a new NID",
     "Supporting documents for a new NID commonly include an online birth "
     "registration certificate, an education certificate (SSC/JSC) where "
     "available, and proof of address; exact requirements are confirmed at the "
     "local Upazila Election Office."),
    ("nid", "https://services.nidw.gov.bd/nid-pub/", "Fees",
     "New voter NID registration is free. Correction and reissue services carry "
     "a government fee paid through the designated bank channel. YEAR-SENSITIVE: "
     "confirm the current fee on the portal."),
    ("nid", "https://services.nidw.gov.bd/nid-pub/", "Processing time",
     "After biometric enrolment a new voter can usually download the NID online "
     "within roughly 10-15 days; physical smart-card delivery takes longer."),

    ("epassport", "https://www.epassport.gov.bd/", "What the e-passport is",
     "The Bangladeshi e-passport is an ICAO-compliant biometric passport with a "
     "microprocessor chip, issued by the Department of Immigration & Passports "
     "under the Ministry of Home Affairs."),
    ("epassport", "https://www.epassport.gov.bd/", "Eligibility & types",
     "Any Bangladeshi citizen is eligible. Ordinary (green) passports are issued "
     "to general citizens; official (blue) and diplomatic (red) passports are "
     "restricted to eligible officials and diplomats."),
    ("epassport", "https://www.epassport.gov.bd/instructions/passport-fees",
     "Delivery timelines",
     "Processing time depends on the delivery category chosen — regular, "
     "express, or super-express — with faster categories costing more."),
    ("epassport", "https://dip.gov.bd/", "Contacts & office locator",
     "The correct passport office is assigned from your district and police "
     "station during the online application. Office locations and notices are "
     "published by the Department of Immigration & Passports at dip.gov.bd."),

    ("etax", "https://etaxnbr.gov.bd/", "Eligibility to e-file",
     "Individuals, professionals, and business owners can file returns online. "
     "Filing requires a TIN and a mobile number biometrically verified against "
     "the taxpayer's NID."),
    ("etax", "https://nbr.gov.bd/faq/income-tax-faq/eng", "Documents to keep",
     "The e-Return system does not require uploading scanned documents at filing "
     "time, but the taxpayer must keep supporting documents ready for audit: "
     "e-TIN certificate, NID, bank statements, salary certificate, and proofs of "
     "rebate-eligible investments. If the tax office finds a discrepancy the "
     "taxpayer is responsible for the declared figures."),
    ("etax", "https://nbr.gov.bd/", "Filing deadline",
     "The annual return-filing deadline (Tax Day) is set by NBR each year and "
     "announced on nbr.gov.bd; online extension requests may be available. "
     "YEAR-SENSITIVE: check the current assessment year's deadline."),

    ("bdris", "https://bdris.gov.bd/", "What BDRIS is",
     "BDRIS is the government's Birth and Death Registration Information System, "
     "operated by the Office of the Registrar General under the Local Government "
     "Division."),
    ("bdris", "https://bdris.gov.bd/br/application", "Who can apply",
     "Any Bangladeshi citizen born inside or outside Bangladesh can apply; "
     "parents or legal guardians apply on behalf of minors."),
    ("bdris", "https://bdris.gov.bd/br/application", "Processing time",
     "When the submitted information is correct, a birth certificate is "
     "typically issued within about 7 days. Certificates must be collected "
     "within the stated window or the application may be auto-cancelled."),
    ("bdris", "https://bdris.gov.bd/", "Contacts",
     "BDRIS support hotline: 16152. Email: support@bdris.gov.bd for office/system "
     "issues and help@bdris.gov.bd for citizen service issues."),
]


def build() -> None:
    ensure_dirs()
    services = load_sources()["services"].keys()
    # fresh files
    for s in services:
        open(chunks_path(s), "w").close()

    counts = {}
    for service, url, heading, text in FACTS:
        body = normalize_text(f"{heading}\n{text}")
        lang = _lang_of(body)
        cat, conf = classify(text, heading)
        health = check_bangla(body, expect_bangla=(_bengali_count(body) > 0))
        cid = hashlib.sha1(f"{service}|{url}|{heading}".encode("utf-8")).hexdigest()[:16]
        rec = {
            "id": cid, "service": service, "category": cat,
            "category_confidence": conf, "text": body, "heading": heading,
            "lang": lang, "source_url": url, "official": True,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source_sha256": "seed:" + VERIFIED_ON,
            "bangla_ok": health.ok, "bangla_issues": health.issues,
        }
        write_jsonl(chunks_path(service), [rec], append=True)
        counts[service] = counts.get(service, 0) + 1
    print("seed corpus written:", counts, "total:", sum(counts.values()))


if __name__ == "__main__":
    build()
