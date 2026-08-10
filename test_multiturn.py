"""
test_multiturn.py — regression test for handle_turn's multi-turn ROUTING and state,
plus a unit test of the continuation classifier's parsing.

Plain-script convention (like eval_<svc>.py): prints PASS/FAIL, exits non-zero on failure.
Run from the project root:   python test_multiturn.py

PROTECTS (the behaviors established while fixing the multi-turn bug):
  1. decide()==clarify  -> clarify + pending set; state.last unchanged.
  2. clarify reply       -> scopes chosen service AND stores the ORIGINAL question (not "nid").
  3. genuine follow-up   -> combines with ONLY the immediate-previous raw question; no pile-up.
  4. chain "10y fee"->"64 page ta"->"abroad theke korle?" keeps one-step memory.
  5. concept-bearing short query -> clarifies (not swallowed as a follow-up).
  6. short OFF-TOPIC follow-up  -> when the continuation classifier says NEW, it does NOT
     inherit scope / combine; it falls through (the gold-price fix, Plan B).
  7. is_continuation() parses FOLLOWUP/NEW/garbage/errors with the intended bias.

SCOPE: routing/state is tested in isolation — answer(), the registry functions, and the
continuation classifier are stubbed, so no LLM / embeddings / DB / registry file is needed.
This does NOT test the classifier's real-world ACCURACY (whether GLM actually says NEW for a
given off-topic query) — that is verified live. It tests that handle_turn correctly OBEYS the
classifier's verdict. The registry's own ambiguity detection is covered by `python disambiguate.py`.
"""
from __future__ import annotations
import sys
import types

for name in ("sentence_transformers", "chromadb", "llm_backends", "normalize_query"):
    if name not in sys.modules and name in ("sentence_transformers", "chromadb", "llm_backends"):
        sys.modules[name] = types.ModuleType(name)
sys.modules["sentence_transformers"].SentenceTransformer = object
sys.modules["chromadb"].PersistentClient = object
sys.modules["llm_backends"].get_backend = lambda *a, **k: None

# import the REAL normalize_query (we unit-test is_continuation), then answer.
import normalize_query as NQ  # noqa: E402
import answer as A  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'✓ PASS' if cond else '✗ FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class RecordingAnswer:
    def __init__(self):
        self.calls = []

    def __call__(self, q, backend, model, colls, only_service=None, history=None):
        self.calls.append({"q": q, "only_service": only_service, "history": list(history or [])})
        return {"answer": f"[stub:{only_service}] {q}", "sources": [], "hits": []}


def run_turn(raw, state, decide_ret, named=(), cand=(), reply_match=None, cont=True):
    rec = RecordingAnswer()
    A.answer = rec
    A.decide = lambda q, reg: decide_ret
    A.named_services = lambda q, reg: list(named)
    A.candidate_services = lambda q, reg: list(cand)
    A.match_reply_to_service = lambda reply, offered, reg: reply_match
    A.is_continuation = lambda msg, prev, backend: cont     # controlled classifier verdict
    resp, ns = A.handle_turn(raw, state, backend=None, model=None, colls=None, reg={})
    return resp, ns, rec


EMPTY = {"pending": None, "last": None, "history": []}


def test_1_clarify_fires():
    print("[1] decide()==clarify -> clarify, pending set, last untouched")
    prior = ("earlier q", "nid")
    resp, ns, rec = run_turn("correction korte ki ki document lagbe",
                             {"pending": None, "last": prior, "history": []},
                             decide_ret={"action": "clarify", "services": ["nid", "epassport", "bdris", "ereturn"],
                                         "question": "Which service?"},
                             cand=["nid", "epassport", "bdris", "ereturn"])
    check("returns a clarify", resp.get("kind") == "clarify")
    check("no answer() call", len(rec.calls) == 0)
    check("pending = (raw, offered)", ns["pending"][0] == "correction korte ki ki document lagbe")
    check("last unchanged", ns["last"] == prior)


def test_2_clarify_reply_stores_original():
    print("[2] clarify reply 'nid' -> scopes nid, answers ORIGINAL question, stores it")
    pending = ("correction korte ki ki document lagbe", ["nid", "epassport", "bdris", "ereturn"])
    resp, ns, rec = run_turn("nid", {"pending": pending, "last": None, "history": []},
                             decide_ret={"action": "answer"}, reply_match="nid")
    check("scoped nid", resp.get("scoped") == "nid")
    check("answered the ORIGINAL question, not 'nid'",
          rec.calls[0]["q"] == "correction korte ki ki document lagbe")
    check("last = (ORIGINAL question, nid)  [Fix 2]",
          ns["last"] == ("correction korte ki ki document lagbe", "nid"))


def test_3_followup_no_pileup():
    print("[3] follow-up (classifier=FOLLOWUP) -> combine w/ immediate prev; store raw")
    resp, ns, rec = run_turn("64 page ta",
                             {"pending": None, "last": ("10 year passport fee koto?", "epassport"), "history": []},
                             decide_ret={"action": "answer"}, cont=True)
    check("stays scoped epassport", resp.get("scoped") == "epassport")
    check("combined = '<reply> <prev raw>'", rec.calls[0]["q"] == "64 page ta 10 year passport fee koto?")
    check("last stores RAW reply, not combined", ns["last"] == ("64 page ta", "epassport"))


def test_4_chain_one_step_memory():
    print("[4] chain keeps one-step memory (classifier=FOLLOWUP throughout)")
    _, s1, r1 = run_turn("10 year passport fee koto?", dict(EMPTY),
                         decide_ret={"action": "scope", "service": "epassport"}, named=["epassport"])
    check("t1 scoped epassport, plain q", r1.calls[0]["q"] == "10 year passport fee koto?")
    _, s2, r2 = run_turn("64 page ta", s1, decide_ret={"action": "answer"}, cont=True)
    check("t2 combined w/ t1", r2.calls[0]["q"] == "64 page ta 10 year passport fee koto?")
    _, s3, r3 = run_turn("abroad theke korle?", s2, decide_ret={"action": "answer"}, cont=True)
    check("t3 uses IMMEDIATE prev ('64 page ta')", "64 page ta" in r3.calls[0]["q"])
    check("t3 no pile-up (no '10 year')", "10 year passport fee koto?" not in r3.calls[0]["q"])


def test_5_concept_beats_followup():
    print("[5] short concept-bearing query -> clarifies, not swallowed")
    resp, ns, rec = run_turn("correction fee koto",
                             {"pending": None, "last": ("10 year passport fee koto?", "epassport"), "history": []},
                             decide_ret={"action": "clarify", "services": ["nid", "epassport", "bdris"],
                                         "question": "Which?"},
                             cand=["nid", "epassport", "bdris"])
    check("clarifies", resp.get("kind") == "clarify")
    check("no answer() call", len(rec.calls) == 0)


def test_6_offtopic_followup_falls_through():
    print("[6] short OFF-TOPIC follow-up (classifier=NEW) -> does NOT inherit scope  [Plan B]")
    resp, ns, rec = run_turn("আজকে ঢাকায় সোনার দাম কত?",
                             {"pending": None, "last": ("correction korte ki ki document lagbe", "nid"), "history": []},
                             decide_ret={"action": "answer"}, cont=False)   # classifier says NEW
    check("did NOT inherit nid scope", resp.get("scoped") is None,
          f"scoped={resp.get('scoped')}")
    check("did NOT combine with the previous question (raw only)",
          rec.calls[0]["q"] == "আজকে ঢাকায় সোনার দাম কত?", f"q={rec.calls[0]['q']!r}")
    check("last stores the off-topic raw, no service", ns["last"] == ("আজকে ঢাকায় সোনার দাম কত?", None))


class FakeBackend:
    def __init__(self, reply): self.reply = reply
    def complete(self, sys, user, **k):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_7_classifier_parsing():
    print("[7] is_continuation() parsing/bias (unit test, fake backend)")
    check("FOLLOWUP -> True", NQ.is_continuation("64 page ta", "prev", FakeBackend("FOLLOWUP")) is True)
    check("NEW -> False", NQ.is_continuation("gold price", "prev", FakeBackend("NEW")) is False)
    check("' new ' lowercase/padded -> False", NQ.is_continuation("x", "prev", FakeBackend("  new  ")) is False)
    check("garbage -> True (bias: keep follow-up)", NQ.is_continuation("x", "prev", FakeBackend("hmm?")) is True)
    check("backend error -> True (never break a follow-up)",
          NQ.is_continuation("x", "prev", FakeBackend(RuntimeError("boom"))) is True)


def test_8_resolved_clarify_no_history_leak():
    print("[8] a resolved clarify does NOT leak the 'which service?' question into generation")
    clarify_q = "Which service — NID / passport / birth registration / income tax?"
    # turn 1: real clarify (registry says clarify) — capture the state it produces
    _, s1, r1 = run_turn("correction korte ki ki document lagbe", dict(EMPTY),
                         decide_ret={"action": "clarify",
                                     "services": ["nid", "epassport", "bdris", "ereturn"],
                                     "question": clarify_q},
                         cand=["nid", "epassport", "bdris", "ereturn"])
    check("t1 stored the clarify Q&A in state.history (precondition)",
          any(clarify_q == h.get("content") for h in s1["history"]))
    # turn 2: user answers "nid" — the generator must NOT receive the clarify question
    _, s2, r2 = run_turn("nid", s1, decide_ret={"action": "answer"}, reply_match="nid")
    gen_hist = r2.calls[0]["history"]
    check("clarify question is ABSENT from the history passed to answer()",
          all(clarify_q != h.get("content") for h in gen_hist),
          f"leaked: {gen_hist}")
    check("clarify user-echo is also stripped (history is clean of the scaffolding)",
          all("correction korte ki ki document lagbe" != h.get("content") for h in gen_hist)
          or gen_hist == [])
    check("stored history for the NEXT turn is also clean of the clarify question",
          all(clarify_q != h.get("content") for h in s2["history"]))


def test_9_url_classification():
    print("[9] url_kind() portal-vs-info classification (protects the actionable-URL fix)")
    portal = ["https://bdris.gov.bd/br-correction",
              "https://services.nidw.gov.bd/nid-pub/",
              "https://services.nidw.gov.bd/nid-pub/form-download",
              "https://everify.bdris.gov.bd",
              "https://etaxnbr.gov.bd"]
    info = ["https://orgbdr.gov.bd/pages/static-pages/6922e065933eb65569e26d6e",
            "https://services.nidw.gov.bd/nid-pub/faq",
            "https://www.epassport.gov.bd/landing/faqs/3",
            "https://www.epassport.gov.bd/landing/notices/158",
            "https://www.incometax.gov.bd/Help/FaqAnswer",
            "https://nbr.gov.bd/all-faq/ban",
            "gazette:S.R.O.79-Law/2018"]        # non-http reference -> never "apply here"
    for u in portal:
        check(f"portal: {u[:44]}", A.url_kind(u) == "portal", f"got {A.url_kind(u)}")
    for u in info:
        check(f"info:   {u[:44]}", A.url_kind(u) == "info", f"got {A.url_kind(u)}")


def main():
    print("=" * 74)
    print("handle_turn multi-turn regression + continuation-classifier unit test")
    print("=" * 74)
    for t in (test_1_clarify_fires, test_2_clarify_reply_stores_original,
              test_3_followup_no_pileup, test_4_chain_one_step_memory,
              test_5_concept_beats_followup, test_6_offtopic_followup_falls_through,
              test_7_classifier_parsing, test_8_resolved_clarify_no_history_leak,
              test_9_url_classification):
        t()
    print("=" * 74)
    print("Result:", "ALL CHECKS PASS" if not FAILS else f"{len(FAILS)} FAIL — " + ", ".join(FAILS))
    if FAILS:
        sys.exit(1)


if __name__ == "__main__":
    main()
