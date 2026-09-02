"""
acquisition/extract.py — deterministic main-content extraction (Phase 3.5 §10).

Moved VERBATIM from freshness/fetch.py in the Phase 3.5 refactor so that the
freshness pipeline and the acquisition layer share one extraction primitive
(spec §11: shared acquisition primitives preferred). The code is unchanged —
byte-for-byte identical output is REQUIRED because freshness hashes compare
hash-to-hash across runs; any change here would flip every stored hash.

The extraction is deliberately a simple readability-style strip: the text is
only ever compared to itself hash-to-hash over time (never to corpus text),
so determinism matters more than perfection. Extracted government text is
preserved verbatim — no translation, paraphrase, grammar cleanup, or
correction of fee values (Phase 3.5 §10).
"""
from __future__ import annotations

import re

# A simple readability-style strip (spec §A3: doesn't need to be exact — the
# extracted text is compared to itself hash-to-hash over time, never to the
# corpus text). Runs inside the page; must be a single JS expression.
STRIP_AND_EXTRACT_JS = """
() => {
    const sels = ['script', 'style', 'noscript', 'nav', 'footer', 'header',
                  'aside', 'iframe', '[role="banner"]', '[role="navigation"]',
                  '[role="contentinfo"]'];
    for (const sel of sels) {
        document.querySelectorAll(sel).forEach(el => el.remove());
    }
    return document.body.innerText;
}
"""


def normalize_text(text: str) -> str:
    """Deterministic whitespace normalization so hash comparisons don't flip
    on CRLF or indentation noise. (Was freshness/fetch._normalize.)"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    blanks = 0
    for ln in lines:
        if ln == "":
            blanks += 1
            if blanks > 1:
                continue
        else:
            blanks = 0
        out.append(ln)
    return "\n".join(out).strip()


def sha256_text(text: str) -> str:
    """SHA-256 of extracted text (same convention as freshness/diff.py)."""
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
