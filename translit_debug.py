"""
translit_debug.py — show the RAW model output for a couple of Banglish queries.

normalize_query.py only prints its verdict (english/bangla/banglish), which hides what
the model actually said. This prints the raw string back from the LLM so we can see
whether it (a) returned the query unchanged, (b) returned empty, or (c) returned Bangla
that our detection missed. Run it exactly like the others:

    python translit_debug.py
"""
from llm_backends import get_backend
from normalize_query import SYSTEM_PROMPT

backend = get_backend()
print("backend:", backend.name, "| model:", getattr(backend, "model", "?"))
print("=" * 70)

tests = [
    "amar card hariye gese, notun card er fee koto?",
    "notun voter hote hole ki ki document lagbe?",
]
for t in tests:
    try:
        out = backend.complete(SYSTEM_PROMPT, t, max_tokens=2048)
    except Exception as e:
        print("INPUT :", t)
        print("ERROR :", type(e).__name__, e)
        print()
        continue
    has_bangla = any("\u0980" <= c <= "\u09ff" for c in out)
    print("INPUT :", t)
    print("OUTPUT:", repr(out))
    print("        (contains Bangla script? ", has_bangla, ")")
    print()
