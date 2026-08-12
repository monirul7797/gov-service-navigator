"""
normalize_query.py — turn a romanized-Bangla (Banglish) query into Bangla script
before retrieval, WITHOUT ever discarding the original.

Why: bge-m3 retrieves Bangla-script queries strongly but romanized Bangla weakly
(measured on this project: romanized ~0.43 misses, transliterated ~0.71 rank-1). So we
convert Banglish -> Bangla with an LLM and embed BOTH the original and the transliteration
("expand, don't replace"). A bad transliteration can then only fail to help — it can
never overwrite a query that would have worked.

Two guards keep this cheap and safe:
  1. Script detection runs first. Already-Bangla queries skip the LLM entirely (nothing
     to transliterate). English queries are handled by the LLM returning them unchanged.
  2. Expand-don't-replace: normalize() returns a LIST of query strings to embed. It is
     [original] for English / already-Bangla, and [original, bangla] for Banglish.

Provider is swappable via llm_backends.get_backend() — Gemini free tier by default.

    from normalize_query import normalize
    from llm_backends import get_backend
    backend = get_backend()                 # Gemini by default; LLM_PROVIDER=claude to switch
    result = normalize("amar card hariye gese, notun card er fee koto?", backend)
    # result["queries"] -> the list to embed (original + Bangla)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Fraction of *letters* that must be Bangla-script for us to treat the query as already
# Bangla and skip the LLM. Code-mixed Bangla-script queries still clear this easily.
BANGLA_SKIP_RATIO = 0.30

SYSTEM_PROMPT = (
    "You convert search queries to Bangla script for a Bangladeshi government-services "
    "search engine. MOST queries are 'Banglish' — Bangla written in Latin letters, often "
    "mixed with English words. Your job is to rewrite them in natural Bangla script.\n\n"
    "Rules:\n"
    "- Banglish (romanized Bangla, even with English words mixed in) → rewrite in natural "
    "Bangla script, preserving meaning. Convert the English words to natural Bangla too "
    "(card→কার্ড, fee→ফি, registration→রেজিস্ট্রেশন, document→ডকুমেন্ট, download→ডাউনলোড, GD→জিডি).\n"
    "- Already Bangla script → return it unchanged.\n"
    "- Pure English with NO Bengali words at all → return it unchanged.\n"
    "- Output ONLY the resulting query text. No quotes, no explanation, no thinking aloud.\n\n"
    "Examples:\n"
    "amar card hariye gese, notun card er fee koto?\n"
    "→ আমার কার্ড হারিয়ে গেছে, নতুন কার্ডের ফি কত?\n\n"
    "notun voter hote hole ki ki document lagbe?\n"
    "→ নতুন ভোটার হতে হলে কী কী ডকুমেন্ট লাগবে?\n\n"
    "biye r por husband er name add korte hobe kivabe?\n"
    "→ বিয়ের পর স্বামীর নাম কীভাবে যুক্ত করতে হবে?\n\n"
    "How much is the correction fee?\n"
    "→ How much is the correction fee?"
)


# English function words that essentially never appear as romanized Bangla. Their presence
# marks a query as English, so we skip the transliteration LLM entirely instead of trusting
# it to "return English unchanged" — GLM-4.x translates it to Bangla instead (found via live
# testing; see report Section V-D). Deliberately EXCLUDES English nouns commonly borrowed
# into Banglish (card, fee, document, online, must, add, download, name, GD...) and ambiguous
# short tokens that collide with romanized Bangla ("to"=তো, "or"=ওর, "a", "i"), so a Banglish
# query that borrows an English noun is NOT misread as English.
ENGLISH_MARKERS = {
    "the", "is", "are", "am", "was", "were", "be", "been", "being",
    "what", "which", "how", "when", "where", "why", "who", "whom", "whose",
    "do", "does", "did", "can", "could", "should", "would", "will", "shall", "may", "might",
    "for", "of", "with", "without", "about", "into", "onto", "than", "from",
    "and", "if", "because", "while", "during",
    "my", "your", "their", "our", "his", "her", "its",
    "have", "has", "had",
    "this", "that", "these", "those", "there",
    "inside", "outside", "much", "many", "please",
}


def looks_english(s: str) -> bool:
    """True if the (Latin-script) query contains a clear English function word.

    Conservative by design: keys on grammatical function words, not borrowed English nouns
    that also appear inside Banglish. A natural-language English question ("What is the fee
    for...") always contains several of these; romanized Bangla uses its own function words
    (ki, koto, er, hobe, lagbe, korte) and so contains none, even when it borrows an English
    noun. Noun-only English fragments ("passport fee") match nothing here and fall through to
    the LLM, exactly as before — the gate fixes natural-language English, the common case."""
    tokens = re.findall(r"[a-z]+", s.lower())
    return any(t in ENGLISH_MARKERS for t in tokens)


def bangla_ratio(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if "\u0980" <= c <= "\u09ff") / len(letters)


def has_bangla(s: str) -> bool:
    return any("\u0980" <= c <= "\u09ff" for c in s)


@dataclass
class NormalizedQuery:
    original: str
    normalized: str | None          # Bangla transliteration, or None if not applicable
    kind: str                       # "bangla" | "english" | "banglish"
    queries: list[str] = field(default_factory=list)   # what to actually embed


def normalize(query: str, backend, *, max_tokens: int = 2048) -> NormalizedQuery:
    q = query.strip()

    # Guard 1: already Bangla -> nothing to transliterate, skip the LLM call.
    if bangla_ratio(q) >= BANGLA_SKIP_RATIO:
        return NormalizedQuery(q, None, "bangla", [q])

    # Guard 2: clear English -> skip the LLM. We do NOT rely on the LLM to "return English
    # unchanged" (GLM-4.x translates it to Bangla, which then drives the answer into Bangla).
    # Detect English deterministically, the same way Guard 1 detects Bangla deterministically.
    if looks_english(q):
        return NormalizedQuery(q, None, "english", [q])

    # Latin script, no clear English markers: could be English fragment or Banglish. Let the
    # LLM decide + transliterate. (English fragments the LLM leaves unchanged fall to "english".)
    out = backend.complete(SYSTEM_PROMPT, q, max_tokens=max_tokens, temperature=0.0).strip()

    # Interpret the result. Banglish -> the model produced Bangla script different from input.
    if out and has_bangla(out) and out != q:
        return NormalizedQuery(q, out, "banglish", [q, out])   # expand, don't replace

    # English (model returned it unchanged / no Bangla) -> original only.
    return NormalizedQuery(q, None, "english", [q])


# ---------------------------------------------------------------------------------------
# Continuation classifier (multi-turn routing) — a single NARROW binary LLM judgment used
# ONLY by handle_turn's follow-up branch, for the one case lexical signals provably cannot
# decide: a short reply that names no service and hits no registry concept is EITHER a
# genuine elliptical follow-up ("64 page ta") OR a new off-topic question ("gold price").
# The probe (probe_followup.py) showed embedding similarity cannot separate these — an
# off-topic "bkash" query outscored a real follow-up — so we ask the LLM a yes/no instead.
# This is deliberately NOT service-routing (the deterministic registry still owns that);
# it only decides whether to inherit the previous turn's scope.
CONTINUATION_SYSTEM_PROMPT = (
    "In a Bangladeshi government-services chat (NID, birth/death registration, passport, "
    "income tax), decide whether the user's new MESSAGE is a FOLLOW-UP that refines their "
    "PREVIOUS question, or a NEW/unrelated question.\n"
    "A FOLLOW-UP drills into the same topic — picks an option, narrows a detail, or asks "
    "'what about X?' about the same thing (e.g. after a passport fee question: '64 page ta', "
    "'abroad theke korle?', '5 year er ta').\n"
    "A NEW question changes the subject — a different service, or something off-topic "
    "entirely (weather, gold/stock prices, sports, general chit-chat).\n"
    "Reply with EXACTLY one word: FOLLOWUP or NEW. No explanation.\n\n"
    "Previous: 10 year passport fee koto?\nMessage: 64 page ta\n-> FOLLOWUP\n\n"
    "Previous: 10 year passport fee koto?\nMessage: abroad theke korle?\n-> FOLLOWUP\n\n"
    "Previous: correction korte ki ki document lagbe?\nMessage: আজকে ঢাকায় সোনার দাম কত?\n-> NEW\n\n"
    "Previous: জন্ম নিবন্ধন সংশোধন করতে কী কী লাগে?\nMessage: ajke Dhaka te brishti hobe?\n-> NEW"
)


def is_continuation(message: str, prev_question: str, backend, *, max_tokens: int = 8) -> bool:
    """True if `message` continues `prev_question` (so handle_turn should inherit its scope);
    False if it's a new/off-topic question (so it should fall through to normal retrieval,
    where the grounding floor declines a true off-topic like a gold-price query).

    Bias: only a clear leading 'NEW' blocks continuation. Any other / ambiguous output keeps
    the working follow-up behavior (losing a real follow-up is a worse demo regression than a
    rare off-topic slipping through, and the answer prompt's off-topic rule is a second guard).
    """
    user = f"Previous: {prev_question}\nMessage: {message}"
    try:
        out = backend.complete(CONTINUATION_SYSTEM_PROMPT, user,
                               max_tokens=max_tokens, temperature=0.0).strip().upper()
    except Exception:
        return True   # never let a classifier error break a legitimate follow-up
    return not out.startswith("NEW")


def _demo() -> None:
    """Show the transliteration on a batch of messy, code-mixed Banglish — no ChromaDB
    needed, just the LLM. Run: python normalize_query.py"""
    from llm_backends import get_backend
    backend = get_backend()
    print(f"backend: {backend.name}\n" + "=" * 70)
    samples = [
        "amar NID card e baba r name vul ache, ki kore thik korbo?",
        "notun voter hote hole ki ki document lagbe?",
        "card hariye felechi, GD kora ki must?",
        "online theke NID download korar niyom ki?",
        "biye r por husband er name add korte hobe, kivabe?",
        "How much is the correction fee?",                 # English -> unchanged
        "ঠিকানা পরিবর্তন করতে চাই",                          # Bangla -> unchanged, no LLM call
    ]
    for s in samples:
        r = normalize(s, backend)
        print(f"[{r.kind:8}] {s}")
        if r.normalized:
            print(f"           -> {r.normalized}")
        print(f"           embed: {r.queries}\n")


if __name__ == "__main__":
    _demo()
