"""
bangla.py — Bangla (Bengali) text normalization + encoding/"broken font" health.

Why this module exists
----------------------
Scraping Bangladeshi government sites reliably produces broken Bangla in a few
specific ways. This module DETECTS each one, REPAIRS what is safely repairable,
and REPORTS the rest so a human can look before that text is embedded.

Failure modes handled:
  1. Mojibake  — UTF-8 bytes decoded as Latin-1/CP1252 -> "à¦œ" style garble.
  2. U+FFFD    — replacement char: bytes that could not be decoded at all.
  3. Orphan matra — a vowel sign / hasant with no consonant base in front.
  4. U+25CC    — DOTTED CIRCLE: a placeholder shown when a combining mark can't
                 attach; its presence in *text* means broken input.
  5. Legacy    — Bijoy/SutonnyMJ ASCII-font Bangla: page is meant to be Bangla
                 but contains ~0 Bengali codepoints and looks like Latin noise.

Also provides the digit tooling, including the multi-digit negative-lookbehind
number scanner that the first build relied on.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import List

import regex as re  # `regex` supports \p{Bengali}; stdlib re does not

# ---------------------------------------------------------------------------
# Character ranges / maps
# ---------------------------------------------------------------------------
BENGALI_BLOCK = (0x0980, 0x09FF)
BN_TO_EN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
EN_TO_BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")

# Bengali vowel signs (matras) + hasant/nukta that MUST follow a consonant.
_DEPENDENT_SIGNS = "\u09be\u09bf\u09c0\u09c1\u09c2\u09c3\u09c7\u09c8\u09cb\u09cc\u09cd\u09d7\u09bc"
# A Bengali consonant/independent-vowel base (simplified: any letter in block).
_BN_LETTER = r"[\u0985-\u09b9\u09ce\u09dc-\u09df\u09f0\u09f1]"

# Signature of UTF-8-as-Latin1 mojibake: a UTF-8 lead byte (U+00C2–U+00F4 when
# read as Latin-1) immediately followed by 1–3 continuation bytes (U+0080–U+00BF).
# Bengali (0x0980 block) encodes as E0 A6/A7 xx -> "à¦"/"à§" clusters, which this
# catches. Requiring the continuation byte keeps ordinary accented Latin safe.
_MOJIBAKE_HINT = re.compile(r"[\u00c2-\u00f4][\u0080-\u00bf]{1,3}")
# Multi-digit number scanner: negative lookbehind so we never start matching in
# the middle of a longer run (mixed Bangla+ASCII digits, grouping commas ok).
_NUMBER_RE = re.compile(r"(?<![০-৯0-9])[০-৯0-9][০-৯0-9,.]*")
_ORPHAN_MATRA_RE = re.compile(rf"(?<!{_BN_LETTER})(?<!\u09bc)[{_DEPENDENT_SIGNS}]")
_ZERO_WIDTH = "\u200b\u200c\u200d\ufeff"  # ZWSP, ZWNJ, ZWJ, BOM


# ---------------------------------------------------------------------------
# Repair + normalize
# ---------------------------------------------------------------------------
def try_fix_mojibake(s: str) -> str:
    """If the string looks like UTF-8 wrongly read as Latin-1, re-decode it.
    Only applied when it demonstrably increases Bengali codepoint count."""
    if not _MOJIBAKE_HINT.search(s):
        return s
    try:
        repaired = s.encode("latin-1", "strict").decode("utf-8", "strict")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
    if _bengali_count(repaired) > _bengali_count(s):
        return repaired
    return s


def normalize_text(s: str, *, drop_zero_width: bool = True) -> str:
    """Canonical cleanup applied to every piece of text before chunking.
    NFC is essential for Bangla: conjuncts/nukta forms must compose so that
    identical-looking text compares and embeds consistently."""
    if not s:
        return ""
    s = try_fix_mojibake(s)
    s = unicodedata.normalize("NFC", s)
    if drop_zero_width:
        # keep ZWNJ/ZWJ *inside* Bangla words (they carry meaning) but strip
        # BOM and stray zero-width spaces that break tokenization.
        s = s.replace("\ufeff", "").replace("\u200b", "")
    s = re.sub(r"[ \t\u00a0]+", " ", s)      # collapse spaces incl. nbsp
    s = re.sub(r"\n{3,}", "\n\n", s)         # collapse blank-line runs
    return s.strip()


def to_ascii_digits(s: str) -> str:
    return s.translate(BN_TO_EN_DIGITS)


def to_bengali_digits(s: str) -> str:
    return s.translate(EN_TO_BN_DIGITS)


def find_numbers(s: str) -> List[str]:
    """Return whole multi-digit numbers (Bangla or ASCII). The negative
    lookbehind guarantees '১৭ ডিজিট' and '3,50,000' come back intact instead
    of as fragmented single digits."""
    return [m.group(0) for m in _NUMBER_RE.finditer(s)]


# ---------------------------------------------------------------------------
# Health report
# ---------------------------------------------------------------------------
def _bengali_count(s: str) -> int:
    lo, hi = BENGALI_BLOCK
    return sum(1 for ch in s if lo <= ord(ch) <= hi)


@dataclass
class BanglaHealth:
    ok: bool
    bengali_chars: int
    total_chars: int
    replacement_chars: int          # U+FFFD
    dotted_circles: int             # U+25CC
    orphan_matras: int
    mojibake_suspected: bool
    legacy_font_suspected: bool     # Bijoy/ASCII-encoded Bangla
    issues: List[str] = field(default_factory=list)


def check_bangla(s: str, *, expect_bangla: bool) -> BanglaHealth:
    """Diagnose broken-Bangla conditions. `expect_bangla` should reflect the
    page's declared language from sources.yaml, so we can catch the nasty case
    of a page that *should* be Bangla but decoded to Latin noise."""
    total = len(s)
    bn = _bengali_count(s)
    repl = s.count("\ufffd")
    dotted = s.count("\u25cc")
    orphans = len(_ORPHAN_MATRA_RE.findall(s))
    mojibake = bool(_MOJIBAKE_HINT.search(s))

    # Legacy/Bijoy heuristic: we were told to expect Bangla, the text is long
    # enough to judge, yet almost no real Bengali codepoints are present.
    legacy = bool(expect_bangla and total > 120 and (bn / max(total, 1)) < 0.03)

    issues: List[str] = []
    if repl:
        issues.append(f"{repl} U+FFFD replacement char(s) — bytes lost in decode")
    if dotted:
        issues.append(f"{dotted} dotted-circle(s) — combining marks not attaching")
    if mojibake:
        issues.append("mojibake pattern (UTF-8 read as Latin-1) detected")
    if orphans:
        issues.append(f"{orphans} orphan vowel-sign(s) — likely dropped base char")
    if legacy:
        issues.append("expected Bangla but ~no Bengali codepoints — legacy/Bijoy font?")

    ok = not issues
    return BanglaHealth(
        ok=ok,
        bengali_chars=bn,
        total_chars=total,
        replacement_chars=repl,
        dotted_circles=dotted,
        orphan_matras=orphans,
        mojibake_suspected=mojibake,
        legacy_font_suspected=legacy,
        issues=issues,
    )
