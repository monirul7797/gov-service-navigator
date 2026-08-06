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

    # Latin script: could be English or Banglish. Let the LLM decide + transliterate.
    out = backend.complete(SYSTEM_PROMPT, q, max_tokens=max_tokens, temperature=0.0).strip()

    # Interpret the result. Banglish -> the model produced Bangla script different from input.
    if out and has_bangla(out) and out != q:
        return NormalizedQuery(q, out, "banglish", [q, out])   # expand, don't replace

    # English (model returned it unchanged / no Bangla) -> original only.
    return NormalizedQuery(q, None, "english", [q])


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
