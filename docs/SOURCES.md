# Verified Source Registry — Phase 1

Every source below is an official Government of Bangladesh domain (`*.gov.bd`),
confirmed live on its `verified_on` date. The fetcher refuses any host not on
the allow-list in `config/sources.yaml`.

## Allow-listed hosts

- `services.nidw.gov.bd`
- `ecs.gov.bd`
- `www.ecs.gov.bd`
- `epassport.gov.bd`
- `www.epassport.gov.bd`
- `dip.gov.bd`
- `nbr.gov.bd`
- `www.nbr.gov.bd`
- `etaxnbr.gov.bd`
- `bdris.gov.bd`
- `everify.bdris.gov.bd`
- `orgbdr.gov.bd`

## National ID (NID)  (জাতীয় পরিচয়পত্র)

**Authority:** Bangladesh Election Commission — National Identity Registration Wing

| Seed URL | Render | Lang | Verified | Note |
|---|---|---|---|---|
| `https://services.nidw.gov.bd/nid-pub/` | js | en,bn | 2026-07-12 | Public portal landing; General Information / FAQ / How to Register links |
| `https://www.ecs.gov.bd/` | static | bn,en | 2026-07-12 | Election Commission main site; forms, notices, laws |

## e-Passport  (ই-পাসপোর্ট)

**Authority:** Department of Immigration & Passports (DIP), Ministry of Home Affairs

| Seed URL | Render | Lang | Verified | Note |
|---|---|---|---|---|
| `https://www.epassport.gov.bd/instructions/five-step-to-your-epassport` | static | en | 2026-07-12 | 5-step application overview |
| `https://www.epassport.gov.bd/instructions/passport-fees` | static | en | 2026-07-12 | Official fee schedule by pages/validity/delivery |
| `https://www.epassport.gov.bd/landing` | js | en | 2026-07-12 | Instruction hub — enumerate remaining /instructions/* pages here |
| `https://dip.gov.bd/` | static | bn,en | 2026-07-12 | DIP parent department — office locator, notices |

## Income Tax e-Return  (আয়কর ই-রিটার্ন)

**Authority:** National Board of Revenue (NBR)

| Seed URL | Render | Lang | Verified | Note |
|---|---|---|---|---|
| `https://nbr.gov.bd/` | static | bn,en | 2026-07-12 | NBR home; links to income-tax guide + e-Return FAQ 2025 |
| `https://nbr.gov.bd/form/income-tax/eng` | static | en | 2026-07-12 | Income tax forms (IT-11GA, IT-10B, return forms, etc.) |
| `https://etaxnbr.gov.bd/` | js | en,bn | 2026-07-12 | e-Return portal — registration/login/FAQ/user-manual (JS SPA) |

## Birth & Death Registration (BDRIS)  (জন্ম ও মৃত্যু নিবন্ধন)

**Authority:** Office of the Registrar General, Birth & Death Registration

| Seed URL | Render | Lang | Verified | Note |
|---|---|---|---|---|
| `https://bdris.gov.bd/` | js | bn,en | 2026-07-12 | BDRIS application portal (JS) |
| `https://orgbdr.gov.bd/` | static | bn | 2026-07-12 | Registrar General office site — notices, rules, fees, contacts |
| `https://everify.bdris.gov.bd/` | static | bn,en | 2026-07-12 | Certificate verification endpoint |

