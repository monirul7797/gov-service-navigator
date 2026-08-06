"""
check_encoding.py — scan a corpus .jsonl for double-encoded (mojibake) Bangla.

The NID corpus was found to have 86/106 records with UTF-8 Bangla that had been
mis-decoded (cp1252/latin-1) and re-encoded, so real Bangla like ৮ (bytes e0 a7 ae)
was stored as c3 a0 c2 a7 c2 ae and read back as "à§®". Dense retrieval then can't
match a clean Bangla query to the corrupted record. This script flags whether any
other corpus has the same problem, before it silently degrades retrieval.

Detection: a naive "contains any U+0080–U+00FF character" test is too broad — it
also flags legitimate accented Latin letters (é, ñ, ü, names with diacritics). The
real signature of double-encoded UTF-8 is that it ROUND-TRIPS: encoding the string
back to Latin-1 recovers the original bytes, and decoding those as UTF-8 yields valid
non-Latin script (here, Bangla in U+0980–09FF). A lone é fails that round-trip (its
byte 0xE9 isn't valid standalone UTF-8), so it is correctly left alone.

Usage:
    python check_encoding.py data/chunks/nid.jsonl
    python check_encoding.py data/chunks/epassport.jsonl data/chunks/ereturn.jsonl bdris.jsonl

Exit code is non-zero if any file has suspected mojibake, so it can gate a pipeline.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FIELDS = ("text", "heading", "edit_note")


def looks_mojibake(s: str) -> bool:
    # cheap pre-filter: no high-Latin chars at all -> definitely not double-encoded
    if not any("\u0080" <= ch <= "\u00ff" for ch in (s or "")):
        return False
    # precise test: does a latin-1 -> utf-8 round-trip recover real (non-Latin) script?
    try:
        repaired = s.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return False  # can't round-trip => legitimate accented letters, not mojibake
    return repaired != s and any(ord(ch) > 0x00ff for ch in repaired)


def has_bangla(s: str) -> bool:
    return any("\u0980" <= ch <= "\u09ff" for ch in (s or ""))


def scan(path: Path) -> int:
    if not path.exists():
        print(f"  !! not found: {path}")
        return 1
    total = moji = clean_bangla = 0
    examples = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            total += 1
            blob = " ".join(str(r.get(f, "")) for f in FIELDS)
            if looks_mojibake(blob):
                moji += 1
                if len(examples) < 2:
                    examples.append((r.get("id", "?"), blob[:50]))
            elif has_bangla(blob):
                clean_bangla += 1
    verdict = "MOJIBAKE DETECTED" if moji else "clean"
    print(f"{path.name}: {verdict} — {moji}/{total} records with mojibake signature, "
          f"{clean_bangla} with clean Bangla")
    for eid, ex in examples:
        print(f"    e.g. {eid}: {ex!r}")
    return 1 if moji else 0


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python check_encoding.py <corpus.jsonl> [more.jsonl ...]")
        raise SystemExit(2)
    bad = 0
    for p in sys.argv[1:]:
        bad |= scan(Path(p))
    print("-" * 60)
    print("RESULT:", "at least one corpus needs repair (run ftfy)" if bad else "all scanned corpora look clean")
    raise SystemExit(bad)


if __name__ == "__main__":
    main()
