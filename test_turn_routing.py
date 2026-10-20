"""
test_turn_routing.py — tests for the LLM-understanding dispatch in answer.py
(spec 2026-10-21-llm-query-understanding-design.md §4.3-4.6).

Plain-script convention (like test_multiturn.py): prints PASS/FAIL, exits non-zero on failure.
Run from the project root:   python test_turn_routing.py      (Windows: set PYTHONIOENCODING=utf-8)

NO LLM, network, DB or embeddings: understand(), answer(), handle_turn_registry() and the
embedding model / Chroma collections are recorders and fakes; the registry is the real
concept_registry.json.

PROTECTS:
  1. handle_turn() dispatches on the Understanding: small_talk / off_topic -> filtered reply with
     no answer() call; unclear -> clarify (model question, else the registry's question);
     service_question -> answer() with the standalone queries, the services and the language.
  2. u.services is used ONLY for service_question (search scope) and unclear (clarify options).
  3. ANY exception from understand() (not just UnderstandingError) -> handle_turn_registry(),
     response tagged route="registry" + understanding={"error": ...}.
  4. Every LLM-path response carries route="llm" + the Understanding as a dict, and the new
     state keeps {"pending": None, "last": (raw, scoped), "history": last 8 entries}.
  5. retrieve()/answer() new keyword arguments (queries, services, answer_language) work, and
     calling them WITHOUT those arguments is unchanged (normalize() still runs, all collections
     searched, language from the normalizer's kind).
"""
from __future__ import annotations

import dataclasses
import sys
import types

for _name in ("sentence_transformers", "chromadb", "llm_backends"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)
sys.modules["sentence_transformers"].SentenceTransformer = object
sys.modules["chromadb"].PersistentClient = object
sys.modules["llm_backends"].get_backend = lambda *a, **k: None

import answer as A  # noqa: E402
import understand as understand_mod  # noqa: E402
from disambiguate import clarify_question, load_registry  # noqa: E402
from normalize_query import NormalizedQuery  # noqa: E402
from understand import Understanding  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'✓ PASS' if cond else '✗ FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


REG = load_registry()
X = object()                                            # collections are opaque to handle_turn
COLLS = {"nid": X, "epassport": X, "ereturn": X, "bdris": X}
PRIOR_HISTORY = [{"role": "user", "content": "nid card korte ki lagbe?"},
                 {"role": "assistant", "content": "NID card er jonno ..."}]

ORIGINALS = {n: getattr(A, n) for n in ("understand", "answer", "handle_turn_registry",
                                         "retrieve", "normalize")}


def reset():
    """Put the real functions back (each test swaps in its own recorders)."""
    for n, f in ORIGINALS.items():
        setattr(A, n, f)


def U(**kw):
    base = dict(type="service_question", services=[], query_bn="", query_en="",
                answer_language="en", reply="", clarify="", raw="{}")
    base.update(kw)
    return Understanding(**base)


# ---------------------------------------------------------------- handle_turn dispatch

understand_calls: list[dict] = []
answer_calls: list[dict] = []
registry_calls: list[tuple] = []
REGISTRY_RESULT = {"kind": "answer", "answer": "registry says hi", "sources": [], "hits": [],
                   "normalized": None, "services": [], "scoped": None}
REGISTRY_STATE = {"pending": None, "last": ("hello", None), "history": []}


def fresh_state():
    return {"pending": None, "last": None, "history": []}


def turn(u=None, *, raw="hello", raises=None, state=None, colls=None):
    """One handle_turn() call with understand/answer/handle_turn_registry replaced by recorders.
    Returns (response, new_state)."""
    reset()
    for log in (understand_calls, answer_calls, registry_calls):
        log.clear()

    def fake_understand(message, history, backend, reg, live_services):
        understand_calls.append({"message": message, "history": history, "backend": backend,
                                 "reg": reg, "live": live_services})
        if raises is not None:
            raise raises
        return u

    def fake_answer(question, backend, model, colls, only_service=None, history=None, *,
                    queries=None, services=None, answer_language=None):
        answer_calls.append({"question": question, "only_service": only_service,
                             "history": list(history or []), "queries": queries,
                             "services": services, "answer_language": answer_language})
        return {"answer": "THE ANSWER", "sources": ["https://x.gov.bd/a"], "hits": [],
                "normalized": None, "services": ["nid"]}

    def fake_registry(*args):
        registry_calls.append(args)
        return dict(REGISTRY_RESULT), dict(REGISTRY_STATE)

    A.understand, A.answer, A.handle_turn_registry = fake_understand, fake_answer, fake_registry
    return A.handle_turn(raw, state if state is not None else fresh_state(), None, None,
                         colls if colls is not None else COLLS, REG)


def run_n_small_talk_turns(n):
    state = fresh_state()
    for i in range(n):
        _, state = turn(U(type="small_talk", reply="Hi there!"), raw=f"hi {i}", state=state)
    return state


def test_1_small_talk_and_off_topic():
    print("[1] small_talk / off_topic -> filtered reply, no answer(), no retrieval")
    r, s = turn(U(type="small_talk", reply="Hi there!"))
    check("small talk: no answer() call", answer_calls == [])
    check("small talk: reply returned",
          r["kind"] == "answer" and r["answer"] == "Hi there!" and r["route"] == "llm")
    check("small talk: history grows by 2", len(s["history"]) == 2)
    check("small talk: history = user raw + assistant reply",
          s["history"] == [{"role": "user", "content": "hello"},
                           {"role": "assistant", "content": "Hi there!"}])
    check("small talk: response shape the app reads",
          r["sources"] == [] and r["hits"] == [] and r["normalized"] is None
          and r["services"] == [] and r["scoped"] is None)
    check("small talk: pending None, last = (raw, None)",
          s["pending"] is None and s["last"] == ("hello", None))

    r, _ = turn(U(type="off_topic", reply="see https://brta.gov.bd", answer_language="en"))
    check("off-topic link replaced by template",
          r["answer"] == understand_mod.template_reply("off_topic", "en", REG, set(COLLS)))
    r, _ = turn(U(type="off_topic", reply="", answer_language="bn"))
    check("empty off-topic reply -> Bangla template",
          r["answer"] == understand_mod.template_reply("off_topic", "bn", REG, set(COLLS)))
    r, _ = turn(U(type="off_topic", reply="see https://brta.gov.bd", answer_language="en"),
                colls={"nid": X, "extra_scratch": X})
    check("template lists LIVE services only (in colls AND registry)",
          r["answer"] == understand_mod.template_reply("off_topic", "en", REG, {"nid"}))

    # ruling: u.services is meaningless off the service_question / unclear branches
    r, s = turn(U(type="small_talk", reply="Hello!", services=["nid"]))
    check("small talk ignores u.services (services=[], scoped None)",
          r["services"] == [] and r["scoped"] is None and s["last"] == ("hello", None))
    r, s = turn(U(type="off_topic", reply="No.", services=["bdris"]))
    check("off-topic ignores u.services (services=[], scoped None)",
          r["services"] == [] and r["scoped"] is None and s["last"] == ("hello", None))
    check("small talk / off-topic: no answer() call",
          answer_calls == [] and registry_calls == [])


def test_2_unclear():
    print("[2] unclear -> clarify question, no answer()")
    r, s = turn(U(type="unclear", clarify="Which service?"))
    check("unclear -> clarify", r["kind"] == "clarify" and r["question"] == "Which service?")
    check("unclear: route llm, no answer() call", r["route"] == "llm" and answer_calls == [])
    check("unclear: pending None (no pending state on the LLM path)", s["pending"] is None)
    check("unclear: clarify question appended to history",
          s["history"][-1] == {"role": "assistant", "content": "Which service?"}
          and s["history"][-2] == {"role": "user", "content": "hello"})
    r, _ = turn(U(type="unclear", clarify="", services=["nid", "bdris"]), raw="correction fee koto?")
    check("empty clarify -> registry question over u.services",
          r["question"] == clarify_question(["nid", "bdris"], "correction fee koto?", REG))
    r, _ = turn(U(type="unclear", clarify="", services=[]), raw="correction fee koto?")
    check("empty clarify + no services -> options are the live services",
          r["question"] == clarify_question(sorted(COLLS), "correction fee koto?", REG))


def test_3_service_question():
    print("[3] service_question -> answer() with standalone queries, services, language")
    r, s = turn(U(type="service_question", services=["nid"], query_bn="ক", query_en="q",
                  answer_language="bn"), raw="fee koto?", state={**fresh_state(), "history": PRIOR_HISTORY})
    c = answer_calls[-1]
    check("service question -> answer() kwargs",
          c["queries"] == ["ক", "q", "fee koto?"] and c["services"] == ["nid"]
          and c["answer_language"] == "bn")
    check("service question -> answer() gets the raw message as the question",
          c["question"] == "fee koto?" and c["only_service"] is None)
    check("service question -> history passed", c["history"] == PRIOR_HISTORY)
    check("single service -> scoped", r["scoped"] == "nid")
    check("answer() result passed through with kind/route",
          r["kind"] == "answer" and r["answer"] == "THE ANSWER" and r["route"] == "llm"
          and r["sources"] == ["https://x.gov.bd/a"] and r["services"] == ["nid"])
    check("new state: pending None, last = (raw, scoped)",
          s["pending"] is None and s["last"] == ("fee koto?", "nid"))
    check("new state: history = prior + user raw + assistant answer",
          s["history"] == PRIOR_HISTORY + [{"role": "user", "content": "fee koto?"},
                                           {"role": "assistant", "content": "THE ANSWER"}])

    r, s = turn(U(type="service_question", services=["nid", "bdris"]))
    check("two services -> not scoped",
          r["scoped"] is None and answer_calls[-1]["services"] == ["nid", "bdris"]
          and s["last"] == ("hello", None))
    r, _ = turn(U(type="service_question", services=[]))
    check("no services -> search all (services=None)", answer_calls[-1]["services"] is None)
    check("no services -> not scoped", r["scoped"] is None)


def test_4_understand_inputs_and_response_fields():
    print("[4] understand() inputs; route / understanding fields on every branch")
    hist = PRIOR_HISTORY
    u = U(type="service_question", services=["nid"], query_bn="ক", query_en="q")
    turn(u, raw="fee koto?", state={**fresh_state(), "history": hist},
         colls={"nid": X, "bdris": X, "scratch_not_in_registry": X})
    c = understand_calls[-1]
    check("understand() gets raw message and history", c["message"] == "fee koto?" and c["history"] == hist)
    check("understand() gets the registry", c["reg"] is REG)
    check("live services = colls ∩ registry services", c["live"] == {"nid", "bdris"})
    r, _ = turn(u)
    check("response.understanding = asdict(u)", r["understanding"] == dataclasses.asdict(u))
    for label, uu in (("small_talk", U(type="small_talk", reply="Hi")),
                      ("unclear", U(type="unclear", clarify="Which?")),
                      ("off_topic", U(type="off_topic", reply="No."))):
        r, _ = turn(uu)
        check(f"{label}: route llm + understanding dict",
              r["route"] == "llm" and r["understanding"] == dataclasses.asdict(uu))
    s = {"pending": ("old q", ["nid"]), "last": ("old q", "nid"), "history": []}
    _, ns = turn(U(type="small_talk", reply="Hi"), state=s)
    check("stale registry `pending` is cleared by an LLM-path turn", ns["pending"] is None)
    check("history capped at 8", len(run_n_small_talk_turns(6)["history"]) == 8)
    check("history keeps the NEWEST entries",
          run_n_small_talk_turns(6)["history"][-2:] == [{"role": "user", "content": "hi 5"},
                                                         {"role": "assistant", "content": "Hi there!"}])


def test_5_fallback():
    print("[5] understand() failure -> handle_turn_registry() fallback")
    st = {**fresh_state(), "history": PRIOR_HISTORY}
    r, s = turn(raises=A.UnderstandingError("bad json"), raw="hello", state=st)
    check("failure -> registry fallback",
          len(registry_calls) == 1 and r["route"] == "registry"
          and r["understanding"] == {"error": "bad json"})
    check("registry called with the same arguments",
          registry_calls[0][0] == "hello" and registry_calls[0][1] is st
          and registry_calls[0][2] is None and registry_calls[0][3] is None
          and registry_calls[0][4] is COLLS and registry_calls[0][5] is REG)
    check("registry response/state passed through",
          r["answer"] == "registry says hi" and s == REGISTRY_STATE)
    check("fallback: no answer() call from the LLM path", answer_calls == [])
    r, _ = turn(raises=RuntimeError("boom"))
    check("non-UnderstandingError (RuntimeError) also falls back",
          len(registry_calls) == 1 and r["route"] == "registry"
          and r["understanding"] == {"error": "boom"})
    r, _ = turn(raises=KeyError("services"))
    check("KeyError (malformed registry) also falls back", len(registry_calls) == 1 and r["route"] == "registry")
    r, _ = turn(raises=RecursionError())
    check("RecursionError (pathological JSON) falls back with a non-empty error text",
          r["route"] == "registry" and r["understanding"]["error"] == "RecursionError")


# ---------------------------------------------------------------- retrieve() / answer() kwargs

class _Vec(list):
    def tolist(self):
        return list(self)


class FakeModel:
    def encode(self, texts, normalize_embeddings=True):
        return [_Vec([0.1, 0.2]) for _ in texts]


QUERY_LOG: list[tuple[str, int]] = []                  # (collection name, n_results) per query()


class FakeColl:
    def __init__(self, name):
        self.name = name

    def query(self, query_embeddings, n_results, include):
        QUERY_LOG.append((self.name, n_results))
        return {"ids": [[f"{self.name}-1"]], "documents": [["doc"]],
                "metadatas": [[{"category": "c", "heading": "h", "source_url": "https://x.gov.bd/a"}]],
                "distances": [[0.2]]}


FAKE_COLLS = {n: FakeColl(n) for n in COLLS}


def queried_services():
    return {n for n, _ in QUERY_LOG}


def last_n_results():
    return QUERY_LOG[-1][1]


def test_6_retrieve():
    print("[6] retrieve(): queries / services / answer_language")
    reset()
    normalize_calls = []
    A.normalize = lambda *a, **k: normalize_calls.append(a) or (_ for _ in ()).throw(
        AssertionError("normalize called"))
    QUERY_LOG.clear()
    norm, hits = A.retrieve("raw", None, FakeModel(), FAKE_COLLS, queries=["a", "a", "", "b"],
                            services=["nid", "bdris"], answer_language="bn")
    check("queries deduplicated, empties dropped", norm.queries == ["a", "b"] and norm.kind == "bangla")
    check("NormalizedQuery(original=question, normalized=None)",
          norm.original == "raw" and norm.normalized is None)
    check("normalize() not called when queries are given", normalize_calls == [])
    check("only listed services searched", queried_services() == {"nid", "bdris"})
    check("every deduplicated query searched in every listed service", len(QUERY_LOG) == 4)
    check("two services -> normal candidate count (not the scoped one)", last_n_results() == A.CANDIDATE_N)
    check("hits come only from the listed services", {h["service"] for h in hits} == {"nid", "bdris"})

    QUERY_LOG.clear()
    A.retrieve("raw", None, FakeModel(), FAKE_COLLS, queries=["a"], services=["nid"])
    check("single service uses scoped candidate count", last_n_results() >= A.SCOPED_TOP_K + 4)
    check("single service -> only that collection", queried_services() == {"nid"})

    QUERY_LOG.clear()
    norm, _ = A.retrieve("raw", None, FakeModel(), FAKE_COLLS, queries=["a"], services=[],
                         answer_language="en")
    check("empty services -> all collections searched", queried_services() == set(FAKE_COLLS))
    check("answer_language en -> kind english", norm.kind == "english")
    QUERY_LOG.clear()
    norm, _ = A.retrieve("raw", None, FakeModel(), FAKE_COLLS, queries=["a"])
    check("services=None -> all collections searched", queried_services() == set(FAKE_COLLS))
    check("answer_language None with queries -> english", norm.kind == "english")

    QUERY_LOG.clear()
    A.retrieve("raw", None, FakeModel(), FAKE_COLLS, queries=["a"], services=["not_a_collection"])
    check("services naming no known collection -> ignored, all searched (like only_service)",
          queried_services() == set(FAKE_COLLS))


def test_7_retrieve_unchanged_without_kwargs():
    print("[7] retrieve() without the new kwargs behaves as before")
    reset()
    normalize_calls = []

    def fake_normalize(q, backend, **k):
        normalize_calls.append(q)
        return NormalizedQuery(q, "বাংলা", "banglish", [q, "বাংলা"])

    A.normalize = fake_normalize
    QUERY_LOG.clear()
    norm, _ = A.retrieve("raw", None, FakeModel(), FAKE_COLLS)
    check("normalize() still runs", normalize_calls == ["raw"] and norm.kind == "banglish")
    check("all collections searched", queried_services() == set(FAKE_COLLS))
    check("both normalized queries embedded per collection", len(QUERY_LOG) == 2 * len(FAKE_COLLS))
    check("candidate_n unchanged", last_n_results() == A.CANDIDATE_N)
    QUERY_LOG.clear()
    A.retrieve("raw", None, FakeModel(), FAKE_COLLS, only_service="bdris")
    check("only_service still scopes to one collection with the scoped count",
          queried_services() == {"bdris"} and last_n_results() >= A.SCOPED_TOP_K + 4)


class RecBackend:
    def __init__(self):
        self.calls = []

    def complete(self, system, user, **k):
        self.calls.append(user)
        return "MODEL REPLY"


HIT = {"service": "nid", "id": "nid-1", "sim": 0.9, "text": "doc", "category": "c",
       "heading": "h", "source_url": "https://x.gov.bd/a"}


def answer_user_prompt(*, norm_kind="english", **kw):
    """Call the real answer() with retrieve() stubbed; return the user prompt it built."""
    reset()
    backend = RecBackend()
    A.retrieve = lambda question, backend, model, colls, **k: (
        NormalizedQuery(question, None, norm_kind, [question]), [dict(HIT)])
    A.answer("q", backend, None, {}, **kw)
    return backend.calls[-1]


def test_8_answer():
    print("[8] answer(): kwargs passthrough and language directive")
    check("answer() bn directive", "বাংলায় উত্তর দিন।" in answer_user_prompt(answer_language="bn"))
    check("answer() en directive", "Answer in English." in answer_user_prompt(answer_language="en"))
    p = answer_user_prompt(norm_kind="bangla", answer_language="en")
    check("answer_language beats norm.kind",
          "Answer in English." in p and "বাংলায় উত্তর দিন।" not in p)
    check("no answer_language: english kind -> English directive",
          "Answer in English." in answer_user_prompt(norm_kind="english"))
    check("no answer_language: banglish kind -> Bangla directive",
          "বাংলায় উত্তর দিন।" in answer_user_prompt(norm_kind="banglish"))
    check("no answer_language: bangla kind -> Bangla directive",
          "বাংলায় উত্তর দিন।" in answer_user_prompt(norm_kind="bangla"))
    check("the question shown to the model is the raw message", "QUESTION: q" in answer_user_prompt())

    reset()
    seen = {}

    def spy_retrieve(question, backend, model, colls, **k):
        seen.update(k)
        return NormalizedQuery(question, None, "english", [question]), [dict(HIT)]

    A.retrieve = spy_retrieve
    A.answer("q", RecBackend(), None, {}, only_service="nid", queries=["a", "b"],
             services=["nid"], answer_language="bn")
    check("answer() passes queries/services/answer_language/only_service to retrieve()",
          seen == {"only_service": "nid", "queries": ["a", "b"], "services": ["nid"],
                   "answer_language": "bn"}, str(seen))

    reset()
    A.retrieve = lambda *a, **k: (NormalizedQuery("q", None, "english", ["q"]), [])
    res = A.answer("q", RecBackend(), None, {}, queries=["q"], services=["nid"], answer_language="en")
    check("grounding floor unchanged: no hits -> DECLINE",
          res["answer"] == A.DECLINE and res["sources"] == [] and res["services"] == [])
    res = A.answer("q", RecBackend(), None, {})
    check("answer() without kwargs still works", res["answer"] == A.DECLINE)


def main():
    print("=" * 74)
    print("handle_turn LLM dispatch + retrieve()/answer() keyword arguments")
    print("=" * 74)
    for t in (test_1_small_talk_and_off_topic, test_2_unclear, test_3_service_question,
              test_4_understand_inputs_and_response_fields, test_5_fallback,
              test_6_retrieve, test_7_retrieve_unchanged_without_kwargs, test_8_answer):
        t()
    reset()
    print("=" * 74)
    print("Result:", "ALL CHECKS PASS" if not FAILS else f"{len(FAILS)} FAIL — " + ", ".join(FAILS))
    if FAILS:
        sys.exit(1)


if __name__ == "__main__":
    main()
