# Freshness report — 2026-09-02

- Services checked: nid, epassport, ereturn, bdris
- Records targeted: 293
- Fetched: 181  (new baselines: 84, changed: 0, unchanged: 97)
- Needs manual screenshot: 102
- Skipped (no usable source_url): 10
- Pending review queue: 0 → `freshness/pending_review.json` (approve via `python freshness/review.py`)

## Notes

- robots.txt was unreachable on at least one domain (robots_unreachable); pages were fetched because no robots rules were actually served. Explicit robots directives are always honored.
- robots.txt was unreachable on at least one domain (robots_unreachable_http_503); pages were fetched because no robots rules were actually served. Explicit robots directives are always honored.

## New baselines (first check — no comparison yet)

| record_id | service | source_url |
|---|---|---|
| `epassport-payref-fee-taxonomy` | epassport | https://achallan.gov.bd/acs/v2/general/challan-payment?id=1 |
| `epassport-payref-chalan-verification` | epassport | https://challanverification.finance.gov.bd/echalan/ |
| `ereturn-faq-gen-what-is-income-tax` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-how-to-pay-income-tax` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-who-must-pay-tax` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-cancel-registration-below-threshold` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-register-without-old-mobile` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-file-with-source-advance-tax` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-gender-wrongly-male` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-prior-print-return-not-showing` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-declared-bank-tds-mismatch` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-car-tax-not-adjusting` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-two-employers-income` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-nid-name-dob-corrected` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-paid-but-still-unpaid` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-area-wise-minimum-tax` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-taxpayer-location-determination` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-what-is-income-tax-registration` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-what-is-return` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-who-files-return` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-where-to-file-return` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-what-is-etin` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-how-to-apply-etin` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-etin-required-documents` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-find-tax-jurisdiction` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-return-filing-method` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-return-filing-deadline` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-change-address` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-consequence-of-late-return` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-time-extension-request` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-penalty-for-not-filing` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-where-to-get-return-form` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-mandatory-return-filing` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-what-is-income` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-heads-of-income` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-what-is-total-income` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-firm-aop-income-in-total` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-spouse-minor-child-income` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-taxable-threshold` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-individual-tax-rate-structure` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-company-tax-rate-structure` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-tax-free-income-heads` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-what-is-tax-rebate` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-rebate-eligible-investments-donations` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-what-is-assessment` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-what-is-tax-year` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-gen-cancel-tin-after-fathers-death` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-how-to-start` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-how-to-sign-in` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-registration-requirements` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-registration-method` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-password-set-reset` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-verify-mobile-in-own-name` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-support-contact` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-mobile-phone-filing` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-supporting-documents` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-no-need-to-resubmit-at-circle` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-how-to-start-filling` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-submission-process` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-claim-withholding-advance-tax` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-salary-source-tax-credit` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-bank-sanchaypatra-dividend-credit` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-meeting-fee-honorarium-income` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-pre-2019-sanchaypatra-credit` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-joint-sanchaypatra` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-car-advance-tax-credit` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-pay-tax-via-system` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-previous-year-excess-tax` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-benefits` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-2025-2026-mandatory` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-asset-autofill` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-tax-service-month-support` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-sanchaypatra-data-not-found` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-gold-market-value` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-doctor-lawyer-income` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-show-all-previous-assets` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-correct-filed-return` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-mpo-teachers-salary` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-data-security` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-certified-copy` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-file-after-deadline` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-surcharge-applicability` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `ereturn-faq-online-multiple-work-locations` | ereturn | https://nbr.gov.bd/all-faq/ban |
| `bdris_faq_017` | bdris | https://everify.bdris.gov.bd |

## Needs manual screenshot (not fetched — do not evade)

| record_id | service | reason | source_url |
|---|---|---|---|
| `0328798228089b51` | nid | http_403 | https://www.ecs.gov.bd/ |
| `6367ec9f93d88546` | nid | http_403 | https://www.ecs.gov.bd/ |
| `d9e46d089fa7e9a2` | nid | http_403 | https://www.ecs.gov.bd/ |
| `b7d46b701dece60f` | nid | http_404 | https://services.nidw.gov.bd/nid-pub/form-download |
| `69036e44760278d0` | nid | http_404 | https://services.nidw.gov.bd/nid-pub/form-download |
| `epassport-faq-7-how-can-i-check-the-status-of-my-passport-application` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/7 |
| `epassport-faq-8-is-it-possible-to-upgrade-to-express-delivery-after-the-enrolment-for-epassport-` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/8 |
| `epassport-faq-9-i-forgot-the-password-of-my-online-application-account-what-should-i-do` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/9 |
| `epassport-faq-10-i-dont-have-an-email-address-can-i-also-apply-online` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/10 |
| `epassport-faq-11-i-did-not-receive-the-account-activation-email-what-should-i-do` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/11 |
| `epassport-faq-12-can-i-cancel-or-re-schedule-an-online-appointment-at-passport-office` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/12 |
| `epassport-faq-13-can-i-change-the-email-address-for-my-online-application-account` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/13 |
| `epassport-faq-14-can-i-change-the-mobile-number-registered-in-my-online-account` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/14 |
| `epassport-faq-15-how-can-i-schedule-an-appointment-at-passport-office` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/15 |
| `epassport-faq-19-can-i-schedule-online-appointments-even-if-i-dont-use-online-application-but-pdf` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/19 |
| `epassport-faq-20-i-made-mistakes-in-my-application-data-and-my-application-is-already-submitted-w` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/20 |
| `epassport-faq-21-some-of-my-data-changed-compared-to-my-previous-passport-how-can-i-apply` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/21 |
| `epassport-faq-22-my-present-address-changed-compared-to-my-previous-passport-where-do-i-need-to-a` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/22 |
| `epassport-faq-24-i-experienced-issues-with-online-payment-what-can-i-do` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/24 |
| `epassport-faq-25-i-cant-get-an-appointment-at-my-passport-office-in-the-near-future-what-can-i-do` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/25 |
| `epassport-faq-26-i-have-an-mrp-how-is-the-process-to-get-the-epassport` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/26 |
| `epassport-faq-72-how-do-i-fill-in-the-given-name-and-surname-part-of-the-name` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/72 |
| `epassport-faq-149-having-trouble-viewing-online-status-application-id-online-registration-id-is-re` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/149 |
| `epassport-faq-150-how-do-i-apply-for-super-express` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/150 |
| `epassport-faq-270-i-have-retired-from-government-service-now-what-documents-are-required-to-comple` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/270 |
| `epassport-faq-271-i-have-lost-my-delivery-slip-now-how-i-will-collect-my-passport` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/271 |
| `epassport-faq-272-what-types-of-documents-are-required-to-show-at-passport-office-to-collect-a-new` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/272 |
| `epassport-faq-273-after-receiving-a-new-passport-if-i-find-any-printing-error-in-my-passport-then-` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/273 |
| `epassport-faq-274-how-much-is-it-important-to-provide-previous-passport-number` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/274 |
| `epassport-faq-275-how-can-i-check-my-e-passport-status` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/275 |
| `epassport-faq-276-during-lprprl-can-i-use-official-passport` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/276 |
| `epassport-faq-277-i-have-received-an-e-mail-for-collection-of-a-new-passport-from-passport-office-` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/277 |
| `epassport-faq-278-i-have-lost-my-e-passport-i-dont-have-my-previous-e-passport-number-how-can-i-ge` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/278 |
| `epassport-faq-279-while-checking-my-passport-status-i-see-pending-for-backend-verification-now-wha` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/279 |
| `epassport-faq-280-what-are-the-reasons-for-getting-passport-delayed` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/280 |
| `epassport-faq-281-do-i-need-attest-my-photograph-for-e-passport` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/281 |
| `epassport-faq-282-if-someone-applies-for-new-e-passport-but-he-is-having-a-machine-readable-passpo` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/282 |
| `epassport-faq-283-how-can-i-change-my-information-in-the-passport-after-i-changing-information-of-` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/283 |
| `epassport-faq-284-can-anyone-below-18-years-get-a-passport-for-10-years` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/284 |
| `epassport-faq-285-after-how-many-years-e-passport-must-be-re-issued` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/285 |
| `epassport-faq-286-when-the-passport-validity-date-expires-is-there-any-fee-to-be-paid-for-the-expi` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/286 |
| `epassport-faq-287-i-want-to-get-a-passport-i-am-21-years-old-but-i-dont-have-an-id-card-i-have-bir` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/287 |
| `epassport-faq-288-i-have-applied-for-my-passport-in-chittagong-can-i-collect-it-from-dhaka` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/288 |
| `epassport-faq-289-in-case-of-minors-who-can-collect-the-passport` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/289 |
| `epassport-notice-special-attention-fraud` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/notices/194 |
| `epassport-notice-documents-carried-enrolment` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/notices/34 |
| `epassport-notice-documents-checklist-enrollment` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/notices/160 |
| `epassport-notice-narayanganj-rpo-resumption` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/notices/158 |
| `epassport-notice-dhaka-cantonment-jurisdiction` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/notices/158 |
| `epassport-notice-functional-epassport-offices` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/notices/33 |
| `epassport-faq-taxonomy-index` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs |
| `epassport-payref-receipt-download` | epassport | http_404 | https://receipt.dgepay.net/search/details |
| `epassport-instructions-documents-eligibility` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/instructions |
| `epassport-instructions-urgent-timelines` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/instructions |
| `epassport-urgent-super-express` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/urgent-applications |
| `epassport-fees-payment-methods` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/passport-fees |
| `epassport-fees-mission-general` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/passport-fees |
| `epassport-fees-mission-labor-student` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/passport-fees |
| `epassport-instructions-five-step-overview` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/five-step-to-your-epassport |
| `epassport-onboarding-region-check` | epassport | known_blocked_domain | https://www.epassport.gov.bd/onboarding |
| `epassport-instructions-adoption-guardianship` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/instructions |
| `epassport-instructions-submission-jurisdiction` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/instructions |
| `epassport-instructions-minor-validity` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/instructions |
| `epassport-instructions-vat-fees-domestic-foreign` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/instructions |
| `epassport-instructions-diplomatic-passport` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/instructions |
| `epassport-instructions-foreign-mission-contact-address` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/instructions |
| `epassport-fees-inside-bd-all` | epassport | known_blocked_domain | https://www.epassport.gov.bd/instructions/passport-fees |
| `epassport-faq-3-what-documents-do-i-need-to-bring-for-epassport-enrolment` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/3 |
| `epassport-faq-4-do-i-have-to-bring-photos-for-applying-for-the-epassport` | epassport | known_blocked_domain | https://www.epassport.gov.bd/landing/faqs/4 |
| `ereturn-faq-online-security-certificate-warning` | ereturn | http_500 | https://www.incometax.gov.bd/Help/FaqAnswer |
| `bdris_faq_registration_basics_1` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_registration_basics_2` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_registration_basics_3` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_registration_basics_4` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_special_populations_1` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_special_populations_2` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_001` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_002` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_003` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_004` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_009` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_012` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_013` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_014` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_015` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_faq_016` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e |
| `bdris_manual_stage_0_prerequisites` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922db5f933eb65569e09bfd |
| `bdris_manual_stage_1_entry` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922db5f933eb65569e09bfd |
| `bdris_manual_stage_2_address_basis` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922db5f933eb65569e09bfd |
| `bdris_manual_stage_3_personal_parent_info` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922db5f933eb65569e09bfd |
| `bdris_manual_stage_4_addresses` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922db5f933eb65569e09bfd |
| `bdris_manual_stage_5_applicant_declaration_documents` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922db5f933eb65569e09bfd |
| `bdris_manual_stage_6_review_otp` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922db5f933eb65569e09bfd |
| `bdris_manual_stage_7_confirmation_next_steps` | bdris | known_blocked_domain | https://orgbdr.gov.bd/pages/static-pages/6922db5f933eb65569e09bfd |
| `bdris_correction_001` | bdris | http_403 | https://bdris.gov.bd/br-correction |
| `bdris_correction_002` | bdris | http_403 | https://bdris.gov.bd/br-correction |
| `bdris_correction_003` | bdris | http_403 | https://bdris.gov.bd/br-correction |
| `bdris_correction_004` | bdris | http_403 | https://bdris.gov.bd/br-correction |
| `bdris_correction_005` | bdris | http_403 | https://bdris.gov.bd/br-correction |
| `bdris_correction_006` | bdris | http_403 | https://bdris.gov.bd/br-correction |
| `bdris_payment_001` | bdris | Page.goto: net::ERR_CONNECTION_REFUSED at https://eservices.bdris.gov.bd/
Call log:
  - navigating to "https://eservices | https://eservices.bdris.gov.bd/ |
| `bdris_login_instructions_001` | bdris | http_403 | https://bdris.gov.bd/login |

## Skipped (missing or malformed source_url)

| record_id | service | source_url value |
|---|---|---|
| `ereturn-faq-frontdoor-landing-pages` | ereturn | https://etaxnbr.gov.bd/, https://nbr.gov.bd/ |
| `bdris_rules2018_009_domestic_birth` | bdris | gazette:S.R.O.79-Law/2018 (জন্ম ও মৃত্যু নিবন্ধন বিধিমালা, ২০১৮) |
| `bdris_rules2018_010_expat_birth` | bdris | gazette:S.R.O.79-Law/2018 (জন্ম ও মৃত্যু নিবন্ধন বিধিমালা, ২০১৮) |
| `bdris_rules2018_011_domestic_death` | bdris | gazette:S.R.O.79-Law/2018 (জন্ম ও মৃত্যু নিবন্ধন বিধিমালা, ২০১৮) |
| `bdris_rules2018_012_expat_death` | bdris | gazette:S.R.O.79-Law/2018 (জন্ম ও মৃত্যু নিবন্ধন বিধিমালা, ২০১৮) |
| `bdris_rules2018_013_duplicate_certificate` | bdris | gazette:S.R.O.79-Law/2018 (জন্ম ও মৃত্যু নিবন্ধন বিধিমালা, ২০১৮) |
| `bdris_rules2018_015_correction_cancellation` | bdris | gazette:S.R.O.79-Law/2018 (জন্ম ও মৃত্যু নিবন্ধন বিধিমালা, ২০১৮) |
| `bdris_rules2018_018_supervision` | bdris | gazette:S.R.O.79-Law/2018 (জন্ম ও মৃত্যু নিবন্ধন বিধিমালা, ২০১৮) |
| `bdris_rules2018_021_fees` | bdris | gazette:S.R.O.79-Law/2018 (জন্ম ও মৃত্যু নিবন্ধন বিধিমালা, ২০১৮) |
| `bdris_rules2018_022_certificate_uses` | bdris | gazette:S.R.O.79-Law/2018 (জন্ম ও মৃত্যু নিবন্ধন বিধিমালা, ২০১৮) |

This run detects drift only — no corpus .jsonl or data/vectordb file was written by this script.
