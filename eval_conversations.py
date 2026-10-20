"""
eval_conversations.py — run the 40-turn conversation suite (conversation_cases.json) through the
real turn handler and score it, so two routing designs can be compared on the same questions.

    python eval_conversations.py --route llm       [--env-bat FILE] [--out FILE.jsonl]
    python eval_conversations.py --route registry  [--env-bat FILE] [--out FILE.jsonl]

--route llm       answer.handle_turn          (one LLM "understanding" call per turn, registry fallback)
--route registry  answer.handle_turn_registry (the older keyword router, kept for comparison)
--env-bat FILE    apply the `set NAME=VALUE` lines of a .bat file (e.g. the LLM settings) to this
                  process; only the variable NAMES are printed, never the values.
--out FILE.jsonl  one JSON line per turn (question, route, kind, text, services, hit ids, problems,
                  fact-check flags, seconds), written as the run progresses.

THIS CALLS THE LLM (about US$0.05 per run) and needs data/vectordb. It is a measurement, not a
gate: the exit code is 0 whatever the scores. A turn that raises (e.g. an API 429) is recorded as
a FAIL with the error text and the run goes on; conversation state is reset for every conversation.

Per turn the scorer checks (score_turn): the response kind, that answers come only from the
expected service(s), words that must / must not appear, and for "random" (off-topic) turns a short,
link-free, figure-free reply. Separately every answer is fact-checked (factcheck, the prototype of
spec §5.3): each number and URL in it must appear in the records it was built from or in the
question. The fact check catches invented numbers and links, NOT a real number attached to the
wrong item.

Importing this module is cheap on purpose (no chromadb / sentence_transformers / answer): the heavy
imports happen inside main(), so test_eval_conversations.py can use the scoring functions alone.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "conversation_cases.json"
CHUNKS_DIR = HERE / "data" / "chunks"

TAGS = ("followup", "random", "other")
RANDOM_MAX_CHARS = 400

BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
# ASCII only on purpose: a URL followed by Bangla text ("...gov.bd-এ।") must stop at the Bangla.
URL_RE = re.compile(r"(?:https?://|www\.)[A-Za-z0-9._~:/?#@!$&+;=%\-]+", re.IGNORECASE)
NUM_RE = re.compile(r"[0-9][0-9,]*(?:\.[0-9]+)?")
LIST_MARKER_RE = re.compile(r"(?m)^\s*[0-9]+[.)]\s")
BIG_NUMBER_RE = re.compile(r"[0-9]{3,}")
URL_TRAILING = ".,;:!?-_"


# ------------------------------------------------------------------ scoring

def _response_text(resp: dict) -> str:
    """The text a user would read: the clarifying question, else the answer."""
    if resp.get("kind") == "clarify":
        return resp.get("question") or ""
    return resp.get("answer") or ""


def score_turn(case: dict, resp: dict) -> list[str]:
    """Problems found in one turn's response (empty list = pass). `case` is one entry of
    conversation_cases.json, `resp` the dict returned by the turn handler."""
    problems: list[str] = []
    kind = resp.get("kind")
    text = _response_text(resp)

    if kind not in case["kind"]:
        problems.append(f"kind {kind!r} not in {case['kind']}")

    expected = case.get("services")
    if isinstance(expected, list) and kind == "answer":
        got = list(resp.get("services") or [])
        if not got:
            problems.append(f"answer came from no service (expected within {expected})")
        elif not set(got) <= set(expected):
            problems.append(f"services {got} not within {expected}")

    wanted = case.get("must_contain_any") or []
    if wanted and not any(w in text for w in wanted):
        problems.append(f"contains none of {wanted}")
    for bad in case.get("must_not_contain") or []:
        if bad in text:
            problems.append(f"contains forbidden text {bad!r}")

    if case.get("tag") == "random":
        if len(text) > RANDOM_MAX_CHARS:
            problems.append(f"random reply too long ({len(text)} > {RANDOM_MAX_CHARS} chars)")
        low = text.lower()
        if "http://" in low or "https://" in low or "www." in low:
            problems.append("random reply contains a link")
        m = BIG_NUMBER_RE.search(text.translate(BN_DIGITS).replace(",", ""))
        if m:
            problems.append(f"random reply contains a number of 3+ digits ({m.group()})")
    return problems


def _norm_url(url: str) -> str:
    u = re.sub(r"^https?://", "", url.strip(), flags=re.IGNORECASE)
    u = re.sub(r"^www\.", "", u, flags=re.IGNORECASE)
    return re.sub(r"[/" + re.escape(URL_TRAILING) + r"]+$", "", u).lower()


def _urls(text: str) -> list[str]:
    """URLs as written (trailing punctuation removed), in order of first appearance; a URL that
    differs from an earlier one only by scheme / "www." / case / trailing "/" is a duplicate."""
    out: list[str] = []
    seen: set[str] = set()
    for m in URL_RE.findall(text):
        u = m.rstrip(URL_TRAILING)
        if u and _norm_url(u) not in seen:
            seen.add(_norm_url(u))
            out.append(u)
    return out


def _numbers(text: str) -> list[str]:
    """Normalised numbers (Western digits, commas removed) in order of first appearance. List
    markers ("1. ") are not numbers and digits inside URLs are not counted."""
    t = LIST_MARKER_RE.sub(" ", text.translate(BN_DIGITS))
    t = URL_RE.sub(" ", t)
    out: list[str] = []
    for m in NUM_RE.findall(t):
        n = m.replace(",", "")
        if n and n not in out:
            out.append(n)
    return out


def factcheck(answer: str, hit_ids: list[str], question: str, records: dict[str, str]) -> list[str]:
    """Numbers and URLs in `answer` that appear neither in the retrieved records (`hit_ids` looked
    up in `records`) nor in the question. Returns unsourced numbers (normalised digit strings;
    one-digit numbers are ignored) first, then unsourced URLs as written, each in order of first
    appearance. URLs are compared without scheme, "www.", case or trailing "/" / punctuation."""
    source = " ".join(records.get(h, "") for h in hit_ids) + " " + question
    src_nums = set(_numbers(source))
    src_urls = {_norm_url(u) for u in _urls(source)}
    bad_numbers = [n for n in _numbers(answer) if n not in src_nums and len(n) > 1]
    bad_urls = [u for u in _urls(answer) if _norm_url(u) not in src_urls]
    return bad_numbers + bad_urls


def _strings(value, top: bool = True):
    """Every string inside a record value; numbers only when nested in a list / dict (a fee tier
    stored as {"fee": 13800}), not top-level counters such as char_len."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v, False)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v, False)
    elif not top and isinstance(value, (int, float)) and not isinstance(value, bool):
        yield str(value)


def load_records(chunks_dir: Path) -> dict[str, str]:
    """record id -> all the text of that record joined (every string field, including strings
    nested in lists / dicts). The same id in several files is merged, not overwritten."""
    records: dict[str, str] = {}
    for f in sorted(Path(chunks_dir).glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict) or rec.get("id") is None:
                continue
            text = " ".join(s for v in rec.values() for s in _strings(v))
            rid = rec["id"]
            records[rid] = f"{records[rid]} {text}" if rid in records else text
    return records


# ------------------------------------------------------------------ environment

def apply_env_bat(path) -> list[str]:
    """Apply the `set NAME=VALUE` (or `set "NAME=VALUE"`) lines of a .bat file to os.environ and
    return the variable NAMES, in order. Values are never returned or printed."""
    names: list[str] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip().lstrip("@").strip()
        if s[:4].lower() != "set ":
            continue
        body = s[4:].strip()
        if len(body) >= 2 and body[0] == '"' and body[-1] == '"':
            body = body[1:-1]
        if "=" not in body:
            continue
        name, value = body.split("=", 1)
        name = name.strip()
        if not name or name.startswith("/"):        # `set /a`, `set /p` are not assignments
            continue
        os.environ[name] = value.strip()
        names.append(name)
    return names


# ------------------------------------------------------------------ running

def _make_row(conv_id, idx, case, resp, err, wall, route, records) -> dict:
    kind = resp.get("kind")
    if err is not None:
        text, problems, flags = err, [f"error: {err}"], []
        services, hit_ids, understanding, kind = [], [], None, "error"
    else:
        text = _response_text(resp)
        problems = score_turn(case, resp)
        services = list(resp.get("services") or [])
        hit_ids = [h["id"] for h in resp.get("hits") or [] if isinstance(h, dict) and h.get("id") is not None]
        understanding = resp.get("understanding")
        flags = factcheck(text, hit_ids, case["q"], records) if kind == "answer" and text.strip() else []
    return {"conv": conv_id, "turn": idx, "tag": case["tag"], "question": case["q"],
            "requested_route": route, "route": resp.get("route", route), "kind": kind, "text": text,
            "services": services, "hit_ids": hit_ids, "problems": problems, "passed": not problems,
            "factcheck": flags, "wall_s": wall, "understanding": understanding}


def run_suite(cases, handle, records, *, route, on_row=None, clock=time.perf_counter) -> list[dict]:
    """Run every conversation through `handle(question, state) -> (resp, new_state)`. State starts
    fresh for each conversation. A turn that raises is recorded as a FAIL (state left as it was)
    and the run continues. `on_row(row)` is called after every turn."""
    rows: list[dict] = []
    for conv in cases:
        state = {"pending": None, "last": None}
        for idx, case in enumerate(conv["turns"], 1):
            t0 = clock()
            try:
                resp, state = handle(case["q"], state)
                err = None
            except Exception as e:                       # one bad API call must not end the run
                resp, err = {}, f"{type(e).__name__}: {str(e)[:300]}"
            row = _make_row(conv["id"], idx, case, resp, err, round(clock() - t0, 2), route, records)
            rows.append(row)
            if on_row:
                on_row(row)
    return rows


def scorecard(rows: list[dict]) -> list[str]:
    """The summary lines: passes per tag, answers flagged by the fact check, median seconds."""
    lines = [f"total {sum(r['passed'] for r in rows)}/{len(rows)}"]
    for tag in TAGS:
        sel = [r for r in rows if r["tag"] == tag]
        lines.append(f"{tag} {sum(r['passed'] for r in sel)}/{len(sel)}")
    answers = [r for r in rows if r["kind"] == "answer" and (r["text"] or "").strip()]
    lines.append(f"factcheck_flagged {sum(bool(r['factcheck']) for r in answers)}/{len(answers)}")
    median = statistics.median(r["wall_s"] for r in rows) if rows else 0.0
    lines.append(f"median seconds per turn: {median:.1f}")
    lines.append(f"route_fallbacks {sum(r['route'] != r['requested_route'] for r in rows)}/{len(rows)}")
    return lines


def _print_row(row: dict) -> None:
    print(f"{'PASS' if row['passed'] else 'FAIL'} {row['conv']}.{row['turn']} [{row['tag']}] "
          f"{row['question'][:70]}  ({row['wall_s']:.1f}s, {row['route']}, {row['kind']})", flush=True)
    for p in row["problems"]:
        print(f"       - {p}", flush=True)
    if row["factcheck"]:
        print(f"       ! fact check: unsourced {row['factcheck']}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="eval_conversations.py",
        description="Run the conversation suite through a routing design and print a scorecard. "
                    "Calls the LLM (costs money). Exit code is 0 whatever the scores.")
    ap.add_argument("--route", choices=("llm", "registry"), required=True,
                    help="llm = answer.handle_turn (LLM understanding); "
                         "registry = answer.handle_turn_registry (keyword router)")
    ap.add_argument("--env-bat", metavar="FILE",
                    help="apply `set NAME=VALUE` lines of this .bat file to the environment "
                         "(only the variable names are printed)")
    ap.add_argument("--out", metavar="FILE.jsonl", help="write one JSON line per turn to this file")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):                  # Bangla must not crash a cp1252 console
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    env_path = Path(args.env_bat).resolve() if args.env_bat else None
    out_path = Path(args.out).resolve() if args.out else None
    if env_path is not None:
        if not env_path.is_file():
            raise SystemExit(f"--env-bat: file not found: {env_path.name}")
        print("env vars set: " + ", ".join(apply_env_bat(env_path)))

    os.chdir(HERE)                                           # answer.DB_DIR is relative
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    records = load_records(CHUNKS_DIR)

    # heavy imports only now, so `--help` and the unit tests stay instant
    import chromadb
    from sentence_transformers import SentenceTransformer

    import answer as A
    from disambiguate import load_registry
    from llm_backends import get_backend

    backend = get_backend()
    print(f"LLM backend: {backend.name}")
    model = SentenceTransformer(A.MODEL_NAME)
    colls = A.load_collections(chromadb.PersistentClient(path=A.DB_DIR))
    if not colls:
        raise SystemExit(f"No collections found in {A.DB_DIR}. Run the embed_*.py steps first.")
    reg = load_registry()
    turn_fn = A.handle_turn if args.route == "llm" else A.handle_turn_registry

    def handle(q, state):
        return turn_fn(q, state, backend, model, colls, reg)

    n_turns = sum(len(c["turns"]) for c in cases)
    print(f"route={args.route}: {len(cases)} conversations, {n_turns} turns")
    print("=" * 74)
    out_file = out_path.open("w", encoding="utf-8") if out_path else None

    def on_row(row):
        _print_row(row)
        if out_file:
            out_file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            out_file.flush()

    try:
        rows = run_suite(cases, handle, records, route=args.route, on_row=on_row)
    finally:
        if out_file:
            out_file.close()

    print("=" * 74)
    print(f"Scorecard (route={args.route})")
    for line in scorecard(rows):
        print(line)
    if out_path:
        print(f"per-turn log: {out_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
