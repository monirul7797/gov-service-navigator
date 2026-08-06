"""
disambiguate.py — deterministic, data-driven service disambiguation.

Reads concept_registry.json (which concepts genuinely live in which services) and
decides, WITHOUT any LLM judgment, whether a query is ambiguous across services and
what to ask. This replaces the earlier GLM-driven clarifier that looped and offered
fake options.

Decision (see decide()):
  1. If the query NAMES exactly one service -> scope to it (no question).
  2. Else look at which CONCEPTS the query mentions and intersect their service sets:
       - >= 2 services remain and none named  -> ASK, offering only those services
       - exactly 1 remains                     -> scope to it (the concept pins it)
       - 0 (no concept, or conflicting)        -> answer best-match across all
  Because options come from the registry, tax is never offered for "reissue", etc.

Everything is substring keyword matching (English + Bangla + common Banglish) against
the raw query, case-insensitive — no normalization needed to decide.
"""
from __future__ import annotations

import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent / "concept_registry.json"


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _matches(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(kw.lower() in t for kw in keywords)


def named_services(query: str, reg: dict) -> list[str]:
    """Services explicitly named in the query."""
    return [sid for sid, s in reg["services"].items() if _matches(query, s["keywords"])]


def candidate_services(query: str, reg: dict) -> list[str]:
    """Services implied by the concept(s) in the query = intersection of each matched
    concept's service set. Empty if no concept matched or the concepts conflict."""
    matched = [set(c["services"]) for c in reg["concepts"].values() if _matches(query, c["keywords"])]
    if not matched:
        return []
    cand = matched[0]
    for s in matched[1:]:
        cand = cand & s
    return sorted(cand)


_BANGLISH_MARKERS = [" koto", "lagbe", "korte", "kivabe", "kibhabe", "naki", "hobe",
                     " ache", "amar", "kothay", "keno", " ki ", " kore", "kemne", "kore"]


def _lang(query: str, reg: dict) -> str:
    # Bangla script -> Bangla. Romanized Bangla (Banglish) -> Bangla too (they read Bangla).
    # Otherwise English.
    if any("\u0980" <= c <= "\u09ff" for c in query):
        return "bn"
    q = " " + query.lower() + " "
    if any(m in q for m in _BANGLISH_MARKERS):
        return "bn"
    return "en"


def clarify_question(services: list[str], query: str, reg: dict) -> str:
    lang = _lang(query, reg)
    labels = [reg["services"][s]["labels"][lang] for s in services]
    if lang == "bn":
        opts = " নাকি ".join(labels)
        return f"আপনি কোন সেবার বিষয়ে জানতে চাচ্ছেন — {opts}?"
    opts = " or ".join(labels)
    return f"Which service do you mean — {opts}?"


def match_reply_to_service(reply: str, offered: list[str], reg: dict) -> str | None:
    """Map a short clarification reply (e.g. 'passport') to one of the offered services."""
    for sid in offered:
        if _matches(reply, reg["services"][sid]["keywords"]):
            return sid
    return None


def decide(query: str, reg: dict) -> dict:
    """Returns one of:
      {'action': 'scope',   'service': sid}      -> answer, restricted to that service
      {'action': 'clarify', 'services': [...], 'question': str}
      {'action': 'answer'}                        -> answer best-match across all
    """
    named = named_services(query, reg)
    if len(named) == 1:
        return {"action": "scope", "service": named[0]}
    if len(named) >= 2:
        return {"action": "answer"}          # multiple services named — let retrieval sort it

    cand = candidate_services(query, reg)
    if len(cand) >= 2:
        return {"action": "clarify", "services": cand, "question": clarify_question(cand, query, reg)}
    if len(cand) == 1:
        return {"action": "scope", "service": cand[0]}
    return {"action": "answer"}


if __name__ == "__main__":
    # quick self-check of the decision logic (no models needed)
    reg = load_registry()
    tests = [
        "reissue fee koto?",                 # concept reissue+fee, no service -> clarify nid/passport
        "passport reissue fee koto?",        # names passport -> scope
        "জন্ম নিবন্ধন করতে কি লাগে?",          # names bdris -> scope
        "correction fee koto?",              # correction+fee -> clarify nid/passport/bdris
        "duplicate certificate koto?",       # duplicate -> only bdris -> scope
        "what's the weather?",               # nothing -> answer best-match
    ]
    for q in tests:
        d = decide(q, reg)
        print(f"{q!r:45} -> {d['action']:8} {d.get('service') or d.get('services') or ''}")
        if d["action"] == "clarify":
            print(f"    Q: {d['question']}")
