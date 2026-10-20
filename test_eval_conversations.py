"""
test_eval_conversations.py — tests for eval_conversations.py (the conversation-suite scorer) and
the shape of conversation_cases.json.

Plain-script convention (like test_multiturn.py): prints PASS/FAIL, exits non-zero on failure.
Run from the project root:   python test_eval_conversations.py   (Windows: set PYTHONIOENCODING=utf-8)

NO LLM, network, DB or embeddings: eval_conversations.py imports its heavy modules only inside
main(), the turn runner is driven by a fake handler, and the corpus is a temp directory.

PROTECTS:
  1. score_turn(): kind / service-subset / must_contain_any / must_not_contain for every turn, plus
     the extra "random" rules (<= 400 chars, no link, no 3+ digit number in either digit script).
  2. factcheck(): prototype logic (Bengali -> Western digits, list markers and one-digit numbers
     ignored, URL compared without scheme / "www." / trailing punctuation), returning unsourced
     numbers then unsourced URLs in first-appearance order.
  3. load_records(): id -> every string of the record joined (nested strings too), duplicates merged.
  4. apply_env_bat(): applies `set NAME=VALUE` lines, returns the NAMES only.
  5. run_suite()/scorecard(): state is reset per conversation, one failing turn (exception) is
     recorded as FAIL and does not stop the run, tag counts and the median are computed.
  6. conversation_cases.json: schema, UTF-8 Bangla, turn counts per tag, and the expected answer
     language (`lang`: bn 27 / en 11 / null 2) on every turn.
  7. score_turn() answer-language rule: `lang` bn needs a Bangla-letter share >= 0.30 in the text
     the user reads, en needs < 0.10, null / absent adds no rule; the scorecard prints
     `language x/<turns with lang set>`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from eval_conversations import (CASES_PATH, apply_env_bat, factcheck, load_cases, load_records,
                                measured_language, parse_args, run_suite, score_turn, scorecard)

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'✓ PASS' if cond else '✗ FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def counts(convs):
    """(total turns, followup turns, random turns) of a loaded conversation_cases.json."""
    turns = [t for c in convs for t in c["turns"]]
    return (len(turns), sum(t["tag"] == "followup" for t in turns), sum(t["tag"] == "random" for t in turns))


def section(title):
    print("\n" + title)


# ------------------------------------------------------------------ score_turn
section("score_turn")
RAND = {"q": "x", "tag": "random", "kind": ["answer"], "services": None, "must_contain_any": [], "must_not_contain": ["৮,০৫০"]}
check("random clean passes", score_turn(RAND, {"kind": "answer", "answer": "Sorry, I can't help.", "services": []}) == [])
check("random long fails", score_turn(RAND, {"kind": "answer", "answer": "a" * 401, "services": []}) != [])
check("random link fails", score_turn(RAND, {"kind": "answer", "answer": "see www.brta.gov.bd", "services": []}) != [])
check("random Bengali 3-digit number fails", score_turn(RAND, {"kind": "answer", "answer": "ফি ৳৫০০", "services": []}) != [])
check("random forbidden text fails", score_turn(RAND, {"kind": "answer", "answer": "৮,০৫০", "services": []}) != [])
FU = {"q": "x", "tag": "followup", "kind": ["answer"], "services": ["nid"], "must_contain_any": ["230", "২৩০"], "must_not_contain": []}
check("followup passes", score_turn(FU, {"kind": "answer", "answer": "fee ২৩০ টাকা", "services": ["nid"]}) == [])
check("followup clarify fails", score_turn(FU, {"kind": "clarify", "question": "Which service?"}) != [])
check("followup wrong service fails", score_turn(FU, {"kind": "answer", "answer": "230", "services": ["bdris"]}) != [])
check("followup empty services fails", score_turn(FU, {"kind": "answer", "answer": "230", "services": []}) != [])
check("followup missing content fails", score_turn(FU, {"kind": "answer", "answer": "no figure", "services": ["nid"]}) != [])

# --- further rules from the brief (not in its sample tests)
check("random: exactly 400 chars passes", score_turn(RAND, {"kind": "answer", "answer": "a" * 400, "services": []}) == [])
check("random: https link fails", score_turn(RAND, {"kind": "answer", "answer": "go to https://x.gov.bd", "services": []}) != [])
check("random: http link fails", score_turn(RAND, {"kind": "answer", "answer": "go to http://x.gov.bd", "services": []}) != [])
check("random: Western 3-digit number fails", score_turn(RAND, {"kind": "answer", "answer": "it is 500", "services": []}) != [])
check("random: comma-grouped number (1,234) fails", score_turn(RAND, {"kind": "answer", "answer": "about 1,234 taka", "services": []}) != [])
check("random: two-digit number passes", score_turn(RAND, {"kind": "answer", "answer": "I am 42", "services": []}) == [])
check("random: Bengali two-digit number passes", score_turn(RAND, {"kind": "answer", "answer": "আমি ৪২", "services": []}) == [])
# the 400-char/link/number rules are for 'random' only
OTHER = {"q": "x", "tag": "other", "kind": ["answer"], "services": None, "must_contain_any": [], "must_not_contain": []}
check("other: long answer with link and number is fine",
      score_turn(OTHER, {"kind": "answer", "answer": "a" * 500 + " https://x.gov.bd 12345", "services": ["nid"]}) == [])
check("followup: long answer with link and number is fine",
      score_turn(dict(FU, must_contain_any=[]), {"kind": "answer", "answer": "a" * 500 + " https://x.gov.bd 12345", "services": ["nid"]}) == [])
check("unexpected kind 'error' fails", score_turn(OTHER, {"kind": "error"}) != [])
check("missing kind fails", score_turn(OTHER, {"answer": "hi", "services": []}) != [])
# services: null = no check; list = non-empty subset (answers only)
check("services null: any services pass", score_turn(OTHER, {"kind": "answer", "answer": "hi", "services": ["nid", "bdris"]}) == [])
check("services null: empty services pass", score_turn(OTHER, {"kind": "answer", "answer": "hi", "services": []}) == [])
TWO = dict(OTHER, services=["nid", "epassport"])
check("services list: subset of two passes", score_turn(TWO, {"kind": "answer", "answer": "hi", "services": ["epassport"]}) == [])
check("services list: both pass", score_turn(TWO, {"kind": "answer", "answer": "hi", "services": ["nid", "epassport"]}) == [])
check("services list: one extra service fails", score_turn(TWO, {"kind": "answer", "answer": "hi", "services": ["nid", "bdris"]}) != [])
check("services missing key fails (non-empty required)", score_turn(TWO, {"kind": "answer", "answer": "hi"}) != [])
# clarify cases
CL = dict(OTHER, kind=["clarify"])
check("clarify case: clarify passes", score_turn(CL, {"kind": "clarify", "question": "Which service?"}) == [])
check("clarify case: answer fails", score_turn(CL, {"kind": "answer", "answer": "NID fee 230", "services": ["nid"]}) != [])
CL_SVC = dict(OTHER, kind=["answer", "clarify"], services=["nid"])
check("answer-or-clarify case: clarify passes without services", score_turn(CL_SVC, {"kind": "clarify", "question": "Which?"}) == [])
check("answer-or-clarify case: answer must be from nid", score_turn(CL_SVC, {"kind": "answer", "answer": "x", "services": ["bdris"]}) != [])
check("answer-or-clarify case: nid answer passes", score_turn(CL_SVC, {"kind": "answer", "answer": "x", "services": ["nid"]}) == [])
# content rules read the clarify question for clarify responses
CL_TXT = dict(OTHER, kind=["clarify"], must_contain_any=["NID"], must_not_contain=["passport"])
check("clarify text is scored (must_contain_any hit)", score_turn(CL_TXT, {"kind": "clarify", "question": "NID or BDRIS?"}) == [])
check("clarify text is scored (must_contain_any miss)", score_turn(CL_TXT, {"kind": "clarify", "question": "Which one?"}) != [])
check("clarify text is scored (must_not_contain hit)", score_turn(CL_TXT, {"kind": "clarify", "question": "NID or passport?"}) != [])
# must_contain_any: any ONE suffices; empty = no requirement; exact substring
MC = dict(OTHER, must_contain_any=["register", "Register", "e-Return"])
check("must_contain_any: one of several is enough", score_turn(MC, {"kind": "answer", "answer": "Please Register first", "services": []}) == [])
check("must_contain_any: none present fails", score_turn(MC, {"kind": "answer", "answer": "go online", "services": []}) != [])
MN = dict(OTHER, must_not_contain=["১২৩৪৫৬৭৮৯০", "1234567890"])
check("must_not_contain: second forbidden string caught", score_turn(MN, {"kind": "answer", "answer": "your number 1234567890", "services": []}) != [])
check("must_not_contain: absent passes", score_turn(MN, {"kind": "answer", "answer": "check the portal", "services": []}) == [])
check("score_turn returns a list of strings",
      isinstance(score_turn(FU, {"kind": "clarify", "question": "?"}), list)
      and all(isinstance(p, str) for p in score_turn(FU, {"kind": "clarify", "question": "?"})))

# --- answer language (Task 4a): `lang` is optional; set -> the text the user reads must be Bangla / English
section("score_turn: lang")
BN_TXT = "আপনি এনআইডি সংশোধনের জন্য অনলাইনে আবেদন করতে পারেন।"
EN_TXT = "You can apply for the NID correction online."
L_BN, L_EN, L_NULL = dict(OTHER, lang="bn"), dict(OTHER, lang="en"), dict(OTHER, lang=None)


def ans(text):
    return {"kind": "answer", "answer": text, "services": ["nid"]}


check("lang bn: Bangla answer passes", score_turn(L_BN, ans(BN_TXT)) == [])
check("lang bn: English answer fails", score_turn(L_BN, ans(EN_TXT)) == ["answer language 'en' expected 'bn'"], str(score_turn(L_BN, ans(EN_TXT))))
check("lang en: English answer passes", score_turn(L_EN, ans(EN_TXT)) == [])
check("lang en: Bangla answer fails", score_turn(L_EN, ans(BN_TXT)) == ["answer language 'bn' expected 'en'"], str(score_turn(L_EN, ans(BN_TXT))))
check("lang null: adds no rule (English)", score_turn(L_NULL, ans(EN_TXT)) == [])
check("lang null: adds no rule (Bangla)", score_turn(L_NULL, ans(BN_TXT)) == [])
check("no lang key: adds no rule", score_turn(OTHER, ans(EN_TXT)) == [] and score_turn(OTHER, ans(BN_TXT)) == [])
check("lang bn: a Banglish (Latin-letter) answer fails", score_turn(L_BN, ans("apni online e apply korte paren")) != [])
check("lang bn: share exactly 0.30 passes", score_turn(L_BN, ans("অআই" + "abcdefg")) == [])
check("lang bn: share 0.20 fails", score_turn(L_BN, ans("অআ" + "abcdefgh")) != [])
check("lang en: share just under 0.10 passes", score_turn(L_EN, ans("অ" + "abcdefghijk")) == [])
check("lang en: share exactly 0.10 fails and is reported as mixed",
      score_turn(L_EN, ans("অ" + "abcdefghi")) == ["answer language 'mixed' expected 'en'"], str(score_turn(L_EN, ans("অ" + "abcdefghi"))))
check("lang bn: a mixed answer (0.10 <= share < 0.30) is reported as mixed",
      score_turn(L_BN, ans("অ" + "abcdefghi")) == ["answer language 'mixed' expected 'bn'"])
check("lang bn: Bengali digits, punctuation and a URL in a Bangla answer do not hide the language",
      score_turn(L_BN, ans("ফি ৳৮,০৫০। বিস্তারিত https://x.gov.bd")) == [])
check("lang is checked on the clarify question", score_turn(dict(L_BN, kind=["clarify"]), {"kind": "clarify", "question": BN_TXT}) == []
      and score_turn(dict(L_BN, kind=["clarify"]), {"kind": "clarify", "question": EN_TXT}) != [])
check("lang en: Bangla clarify question fails", score_turn(dict(L_EN, kind=["clarify"]), {"kind": "clarify", "question": BN_TXT}) != [])
check("lang bn: empty text fails", score_turn(L_BN, ans("")) != [])
check("lang problem is added to the other problems",
      len(score_turn(dict(L_BN, must_contain_any=["zzz"]), ans(EN_TXT))) == 2)
# text with no letters at all (empty, digits, punctuation) is "none": it is neither Bangla nor English
check("measured_language: empty text is 'none'", measured_language("") == "none")
check("measured_language: whitespace is 'none'", measured_language("  \n\t ") == "none")
check("measured_language: digits and punctuation only are 'none'", measured_language("৳৫০০, 12.5 (৩)।") == "none")
check("measured_language: bn / en / mixed unchanged",
      (measured_language(BN_TXT), measured_language(EN_TXT), measured_language("অ" + "abcdefghi")) == ("bn", "en", "mixed"))
check("lang en: empty text is a miss ('none')", score_turn(L_EN, ans("")) == ["answer language 'none' expected 'en'"], str(score_turn(L_EN, ans(""))))
check("lang bn: empty text is a miss ('none')", score_turn(L_BN, ans("")) == ["answer language 'none' expected 'bn'"], str(score_turn(L_BN, ans(""))))
check("lang en: digits-only text is a miss", score_turn(L_EN, ans("৫০০ 123")) == ["answer language 'none' expected 'en'"])
check("lang bn: digits-only text is a miss", score_turn(L_BN, ans("৫০০ 123")) == ["answer language 'none' expected 'bn'"])
check("lang null: empty text still adds no language rule", score_turn(L_NULL, ans("")) == [])
check("lang en: a response with no answer / question key (empty text) is a miss",
      score_turn(L_EN, {"kind": "answer", "services": ["nid"]}) == ["answer language 'none' expected 'en'"])

# ------------------------------------------------------------------ factcheck
section("factcheck")
REC = {"r1": "fee 8,050 TK https://www.epassport.gov.bd/instructions/passport-fees"}
check("sourced number and url pass", factcheck("ফি ৳৮,০৫০ — https://epassport.gov.bd/instructions/passport-fees/", ["r1"], "q", REC) == [])
check("unsourced number flagged", factcheck("fee 9,999", ["r1"], "q", REC) == ["9999"])
check("unsourced url flagged", factcheck("see https://brta.gov.bd", ["r1"], "q", REC) == ["https://brta.gov.bd"])
check("number from the question allowed", factcheck("age 18", ["r1"], "under 18?", REC) == [])
check("list markers ignored", factcheck("1. step\n2. step", ["r1"], "q", REC) == [])

# --- further rules from the brief / controller resolutions
check("one-digit numbers ignored", factcheck("step 7 of 9", ["r1"], "q", REC) == [])
check("Bengali digits normalised for the check", factcheck("ফি ৯,৯৯৯", ["r1"], "q", REC) == ["9999"])
check("Bengali number sourced from a Western record passes", factcheck("৮০৫০", ["r1"], "q", REC) == [])
check("Western number sourced from a Bengali record passes",
      factcheck("fee 8,050", ["b"], "q", {"b": "ফি ৮,০৫০ টাকা"}) == [])
check("'1)' list marker also ignored", factcheck("1) step\n2) step", ["r1"], "q", REC) == [])
check("numbered list marker ignored but body number checked",
      factcheck("1. pay 9,999 tk\n2. wait", ["r1"], "q", REC) == ["9999"])
check("number glued to text is not a list marker", factcheck("the 99 offices", ["r1"], "q", REC) == ["99"])
check("commas removed, decimal kept", factcheck("rate 12.5", ["r1"], "q", REC) == ["12.5"])
check("digits inside a sourced URL are not numbers",
      factcheck("see https://epassport.gov.bd/instructions/passport-fees", ["r1"], "q", {"r1": "https://epassport.gov.bd/instructions/passport-fees"}) == [])
check("digits inside an unsourced URL are not reported as numbers",
      factcheck("see https://a.gov.bd/p123456", ["r1"], "q", REC) == ["https://a.gov.bd/p123456"])
check("numbers first then URLs", factcheck("see https://brta.gov.bd or pay 9,999", ["r1"], "q", REC) == ["9999", "https://brta.gov.bd"])
check("numbers in first-appearance order",
      factcheck("pay 777 then 555 then 888", ["r1"], "q", REC) == ["777", "555", "888"])
check("duplicates removed", factcheck("pay 777 and again 777 and ৭৭৭", ["r1"], "q", REC) == ["777"])
check("urls in first-appearance order, deduplicated",
      factcheck("https://b.gov.bd then https://a.gov.bd then https://b.gov.bd/", ["r1"], "q", REC)
      == ["https://b.gov.bd", "https://a.gov.bd"])
check("scheme and www ignored when comparing",
      factcheck("see www.epassport.gov.bd/instructions/passport-fees", ["r1"], "q", REC) == [])
check("http vs https ignored", factcheck("see http://epassport.gov.bd/instructions/passport-fees", ["r1"], "q", REC) == [])
check("URL comparison is case-insensitive",
      factcheck("see HTTPS://EPASSPORT.GOV.BD/Instructions/Passport-Fees", ["r1"], "q", REC) == [])
check("trailing slash ignored", factcheck("see https://epassport.gov.bd/instructions/passport-fees/", ["r1"], "q", REC) == [])
# URLs followed by Bangla text/punctuation (the Bangla sentence marker is the danda)
BN_REC = {"b": "portal https://services.nidw.gov.bd/ and https://www.incometax.gov.bd"}
check("URL followed by a Bangla suffix is still the same URL",
      factcheck("যান https://services.nidw.gov.bd-এ।", ["b"], "q", BN_REC) == [])
check("URL followed by a danda is still the same URL",
      factcheck("যান https://services.nidw.gov.bd।", ["b"], "q", BN_REC) == [])
check("URL followed by '/,' is still the same URL",
      factcheck("visit https://www.incometax.gov.bd/, then wait", ["b"], "q", BN_REC) == [])
check("URL followed by a period is still the same URL", factcheck("visit https://www.incometax.gov.bd.", ["b"], "q", BN_REC) == [])
check("URL in parentheses / markdown link", factcheck("[portal](https://www.incometax.gov.bd)", ["b"], "q", BN_REC) == [])
check("URL followed by comma", factcheck("see https://www.incometax.gov.bd, and", ["b"], "q", BN_REC) == [])
check("unsourced URL is reported without trailing punctuation",
      factcheck("যান https://brta.gov.bd-এ।", ["b"], "q", BN_REC) == ["https://brta.gov.bd"])
check("unsourced URL ending '/,' is reported without the comma",
      factcheck("visit https://brta.gov.bd/, now", ["b"], "q", BN_REC) == ["https://brta.gov.bd/"])
check("unsourced www URL reported as written", factcheck("see www.brta.gov.bd.", ["b"], "q", BN_REC) == ["www.brta.gov.bd"])
check("URL from the question counts as sourced", factcheck("see https://x.gov.bd", ["r1"], "is https://x.gov.bd ok?", REC) == [])
check("unknown hit id gives no sources", factcheck("fee 9,999", ["nope"], "q", REC) == ["9999"])
check("no hits gives no sources", factcheck("fee 9,999", [], "q", REC) == ["9999"])
check("several hits are pooled", factcheck("fee 8,050 and 330", ["r1", "r2"], "q", dict(REC, r2="cost 330")) == [])
check("empty answer flags nothing", factcheck("", ["r1"], "q", REC) == [])
check("non-hit records are not sources", factcheck("fee 330", ["r1"], "q", dict(REC, r2="cost 330")) == ["330"])

# ------------------------------------------------------------------ load_records
section("load_records")
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    (d / "a.jsonl").write_text(
        json.dumps({"id": "r1", "text": "ফি ৮,০৫০", "heading": "H1", "official": True, "char_len": 987654, "stage": 4321,
                    "source_url": "https://epassport.gov.bd/x", "bangla_issues": [],
                    "qa_pairs": [{"q": "how much?", "a": "ten thousand"}], "tiers": [{"fee": 13800, "name": "express"}]},
                   ensure_ascii=False) + "\n"
        + "\n"
        + json.dumps({"id": "r2", "text": "second", "topics": ["alpha", "beta"]}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (d / "b.jsonl").write_text(
        json.dumps({"id": "r2", "text": "second again", "heading": "dup"}, ensure_ascii=False) + "\n"
        + "not json at all\n"
        + json.dumps({"no_id": 1, "text": "skipped"}) + "\n"
        + json.dumps(["a", "list", "line"]) + "\n"
        + json.dumps({"id": "r3", "text": "third"}) + "\n",
        encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"id": "ignored", "text": "not a jsonl file"}), encoding="utf-8")
    recs = load_records(d)
    check("ids from every *.jsonl file", set(recs) == {"r1", "r2", "r3"}, str(sorted(recs)))
    check("string fields are joined", "ফি ৮,০৫০" in recs["r1"] and "H1" in recs["r1"] and "https://epassport.gov.bd/x" in recs["r1"])
    check("strings inside lists / dicts are included", "ten thousand" in recs["r1"] and "express" in recs["r1"] and "alpha" in recs["r2"])
    check("numbers inside lists / dicts are included (fee tiers)", "13800" in recs["r1"])
    check("top-level numeric / boolean fields are not", "987654" not in recs["r1"] and "4321" not in recs["r1"] and "True" not in recs["r1"])
    check("duplicate ids are merged, not overwritten", "second" in recs["r2"] and "second again" in recs["r2"] and "dup" in recs["r2"])
    check("malformed lines, id-less rows and non-jsonl files are skipped",
          "skipped" not in " ".join(recs.values()) and "not a jsonl file" not in " ".join(recs.values()))
    check("values are strings", all(isinstance(v, str) for v in recs.values()))
    # factcheck over the loaded records, end to end
    check("factcheck over loaded records: nested fee is sourced", factcheck("express ১৩,৮০০", ["r1"], "q", recs) == [])
    check("load_records accepts a str path too", set(load_records(str(d))) == {"r1", "r2", "r3"})
real = load_records(Path("data/chunks"))
check("real corpus loads (hundreds of records)", len(real) > 300, str(len(real)))
check("real corpus: every value is a non-empty string", all(isinstance(v, str) and v.strip() for v in real.values()))

# ------------------------------------------------------------------ apply_env_bat
section("apply_env_bat")
SECRET = "FAKE-secret-value-for-test-7421"
with tempfile.TemporaryDirectory() as td:
    bat = Path(td) / "fake_env.bat"
    bat.write_text(
        "@echo off\r\n"
        "rem a comment mentioning set X=ignored\r\n"
        f"set EVAL_TEST_KEY={SECRET}\r\n"
        'set "EVAL_TEST_QUOTED=quoted value"\r\n'
        "SET EVAL_TEST_UPPER=up\r\n"
        "  set   EVAL_TEST_SPACED  =  spaced \r\n"
        f"set EVAL_TEST_EQ=a=b={SECRET}\r\n"
        "set EVAL_TEST_EMPTY\r\n"
        "setlocal\r\n"
        "echo hello\r\n",
        encoding="utf-8")
    for k in ("EVAL_TEST_KEY", "EVAL_TEST_QUOTED", "EVAL_TEST_UPPER", "EVAL_TEST_SPACED", "EVAL_TEST_EQ", "EVAL_TEST_EMPTY", "X"):
        os.environ.pop(k, None)
    names = apply_env_bat(bat)
    check("returns the variable names, in order",
          names == ["EVAL_TEST_KEY", "EVAL_TEST_QUOTED", "EVAL_TEST_UPPER", "EVAL_TEST_SPACED", "EVAL_TEST_EQ"], str(names))
    check("returned names contain no value", SECRET not in " ".join(names))
    check("value applied to os.environ", os.environ.get("EVAL_TEST_KEY") == SECRET)
    check('quoted form set "NAME=VALUE" works', os.environ.get("EVAL_TEST_QUOTED") == "quoted value")
    check("SET is case-insensitive", os.environ.get("EVAL_TEST_UPPER") == "up")
    check("only the first '=' splits name and value", os.environ.get("EVAL_TEST_EQ") == f"a=b={SECRET}")
    check("lines that are not set NAME=VALUE are ignored", "X" not in os.environ and "EVAL_TEST_EMPTY" not in os.environ)
    for k in ("EVAL_TEST_KEY", "EVAL_TEST_QUOTED", "EVAL_TEST_UPPER", "EVAL_TEST_SPACED", "EVAL_TEST_EQ"):
        os.environ.pop(k, None)

# ------------------------------------------------------------------ run_suite / scorecard
section("run_suite / scorecard")
MINI = [
    {"id": "A", "title": "first", "turns": [
        {"q": "q1", "tag": "other", "kind": ["answer"], "services": ["nid"], "must_contain_any": ["230"], "must_not_contain": []},
        {"q": "q2", "tag": "followup", "kind": ["answer"], "services": ["nid"], "must_contain_any": [], "must_not_contain": []},
        {"q": "q3", "tag": "random", "kind": ["answer"], "services": None, "must_contain_any": [], "must_not_contain": []},
        {"q": "q6", "tag": "other", "kind": ["answer"], "services": None, "must_contain_any": [], "must_not_contain": []},
    ]},
    {"id": "B", "title": "second", "turns": [
        {"q": "q4", "tag": "other", "kind": ["clarify"], "services": None, "must_contain_any": [], "must_not_contain": []},
        {"q": "q5", "tag": "followup", "kind": ["answer"], "services": ["nid"], "must_contain_any": [], "must_not_contain": []},
    ]},
]
MREC = {"h1": "fee 230 taka https://services.nidw.gov.bd"}
seen_states = []


def fake_handle(q, state):
    seen_states.append((q, dict(state)))
    new = dict(state, history=state.get("history", []) + [q])
    if q == "q1":
        return ({"kind": "answer", "answer": "fee 230 taka", "services": ["nid"], "hits": [{"id": "h1", "service": "nid", "sim": 0.9}],
                 "route": "llm", "understanding": {"type": "service_question"}}, new)
    if q == "q2":
        return ({"kind": "answer", "answer": "total 9,999 taka see https://brta.gov.bd", "services": ["nid"],
                 "hits": [{"id": "h1", "service": "nid", "sim": 0.8}], "route": "registry",
                 "understanding": {"error": "boom"}}, new)
    if q == "q3":
        raise RuntimeError("API 429 overloaded")
    if q == "q4":
        return ({"kind": "clarify", "question": "Which service?"}, new)
    if q == "q5":
        return ({"kind": "answer", "answer": "ok", "services": ["bdris"], "hits": []}, new)
    if q == "q6":
        return ({"kind": "answer", "answer": "fine", "services": ["nid"], "hits": []}, new)
    raise AssertionError(q)


rows_cb = []
rows = run_suite(MINI, fake_handle, MREC, route="llm", on_row=rows_cb.append)
check("one row per turn, in order", [r["question"] for r in rows] == ["q1", "q2", "q3", "q6", "q4", "q5"])
check("on_row callback gets every row", rows_cb == rows)
check("conversation id and 1-based turn index recorded",
      [(r["conv"], r["turn"]) for r in rows] == [("A", 1), ("A", 2), ("A", 3), ("A", 4), ("B", 1), ("B", 2)])
check("first turn passes with no problems", rows[0]["problems"] == [] and rows[0]["passed"] is True)
check("fact-check flags recorded (number then url)", rows[1]["factcheck"] == ["9999", "https://brta.gov.bd"], str(rows[1]["factcheck"]))
check("sourced answer has no fact-check flags", rows[0]["factcheck"] == [])
check("a raising turn is a FAIL with the error text", rows[2]["passed"] is False and any("429" in p for p in rows[2]["problems"]))
check("the error row says it was an error", rows[2]["kind"] == "error" and "429" in rows[2]["text"])
check("the run continues after a failing turn", rows[3]["question"] == "q6" and rows[3]["passed"] is True)
check("a clarify turn in the next conversation passes", rows[4]["kind"] == "clarify" and rows[4]["passed"] is True)
check("error turn is not fact-checked and has empty services/hits", rows[2]["factcheck"] == [] and rows[2]["services"] == [] and rows[2]["hit_ids"] == [])
check("wrong-service turn fails", rows[5]["passed"] is False and any("bdris" in p for p in rows[5]["problems"]))
check("hit ids recorded", rows[0]["hit_ids"] == ["h1"])
check("route comes from the response, else the requested route",
      rows[0]["route"] == "llm" and rows[1]["route"] == "registry" and rows[4]["route"] == "llm")
check("understanding kept for the log", rows[0]["understanding"] == {"type": "service_question"} and rows[4]["understanding"] is None)
check("text is the answer, or the clarify question",
      rows[0]["text"] == "fee 230 taka" and rows[4]["text"] == "Which service?")
check("tag recorded", [r["tag"] for r in rows] == ["other", "followup", "random", "other", "other", "followup"])
check("wall seconds recorded", all(isinstance(r["wall_s"], float) and r["wall_s"] >= 0 for r in rows))
check("rows are JSON-serialisable with Bangla kept",
      json.loads(json.dumps(dict(rows[0], text="৮,০৫০"), ensure_ascii=False))["text"] == "৮,০৫০")
init = {"pending": None, "last": None}
check("state starts fresh in conversation A", seen_states[0][1] == init, str(seen_states[0][1]))
check("state carries between turns of a conversation", seen_states[1][1].get("history") == ["q1"])
check("a failed turn leaves the state as it was for the next turn", seen_states[3][1].get("history") == ["q1", "q2"],
      str(seen_states[3][1]))
check("state is reset for conversation B", seen_states[4][1] == init, str(seen_states[4][1]))
check("state carries within conversation B", seen_states[5][1] == {"pending": None, "last": None, "history": ["q4"]},
      str(seen_states[5][1]))
check("run_suite does not mutate the initial state shape", init == {"pending": None, "last": None})

card = scorecard(rows)
check("scorecard is a list of text lines", isinstance(card, list) and all(isinstance(x, str) for x in card))
check("scorecard: followup 1/2", "followup 1/2" in card, str(card))
check("scorecard: random 0/1", "random 0/1" in card, str(card))
check("scorecard: other 3/3", "other 3/3" in card, str(card))
check("scorecard: total 4/6", "total 4/6" in card, str(card))
check("scorecard: factcheck_flagged 1/4 answers", "factcheck_flagged 1/4" in card, str(card))
check("scorecard: one route fallback (llm requested, registry used)", "route_fallbacks 1/6" in card, str(card))
check("scorecard: median seconds line", any(x.startswith("median seconds per turn: ") for x in card), str(card))
fixed = [dict(r, wall_s=w) for r, w in zip(rows, (1.0, 2.0, 3.0, 4.0, 5.0, 100.0))]
check("median is over all turns", "median seconds per turn: 3.5" in scorecard(fixed), str(scorecard(fixed)))
check("scorecard of nothing does not crash", isinstance(scorecard([]), list))
check("scorecard: no lang-set turns -> language 0/0", "language 0/0" in card, str(card))

LANG_SUITE = [{"id": "L", "title": "lang", "turns": [
    {"q": "l1", "tag": "other", "kind": ["answer"], "services": None, "must_contain_any": [], "must_not_contain": [], "lang": "bn"},
    {"q": "l2", "tag": "other", "kind": ["answer"], "services": None, "must_contain_any": [], "must_not_contain": [], "lang": "bn"},
    {"q": "l3", "tag": "other", "kind": ["answer"], "services": None, "must_contain_any": [], "must_not_contain": [], "lang": None},
    {"q": "l4", "tag": "other", "kind": ["answer"], "services": None, "must_contain_any": [], "must_not_contain": [], "lang": "en"},
    {"q": "l5", "tag": "other", "kind": ["answer"], "services": None, "must_contain_any": [], "must_not_contain": [], "lang": "en"},
]}]


def lang_handle(q, state):
    if q == "l1":
        return ({"kind": "answer", "answer": BN_TXT, "services": [], "hits": []}, state)
    if q == "l2":
        return ({"kind": "answer", "answer": EN_TXT, "services": [], "hits": []}, state)
    if q == "l3":
        return ({"kind": "answer", "answer": EN_TXT, "services": [], "hits": []}, state)
    if q == "l4":
        return ({"kind": "answer", "answer": EN_TXT, "services": [], "hits": []}, state)
    raise RuntimeError("API 429 overloaded")


lrows = run_suite(LANG_SUITE, lang_handle, {}, route="llm")
check("row records the expected lang", [r["lang"] for r in lrows] == ["bn", "bn", None, "en", "en"], str([r["lang"] for r in lrows]))
check("row records whether the language matched (None when no lang)",
      [r["lang_ok"] for r in lrows] == [True, False, None, True, False], str([r["lang_ok"] for r in lrows]))
check("a language miss is a failing turn with the message",
      lrows[1]["passed"] is False and "answer language 'en' expected 'bn'" in lrows[1]["problems"], str(lrows[1]["problems"]))
check("a turn with no lang is not counted for language", lrows[2]["passed"] is True)
check("an error turn with a lang set counts as a language miss", lrows[4]["lang_ok"] is False and lrows[4]["passed"] is False)
lcard = scorecard(lrows)
check("scorecard: language 2/4 (turns with lang set)", "language 2/4" in lcard, str(lcard))
check("scorecard: language line sits with the other counts", lcard.index("language 2/4") < lcard.index(next(x for x in lcard if x.startswith("median"))), str(lcard))
check("scorecard: language line is built from rows without lang_ok too", "language 0/0" in scorecard([{k: v for k, v in r.items() if k not in ("lang", "lang_ok")} for r in rows]))

# --- the denominators come from the loaded cases; a tag with no case prints no line
section("scorecard follows the loaded cases")
TWO = [{"id": "X", "title": "two cases", "turns": [
    {"q": "x1", "tag": "other", "kind": ["answer"], "services": None, "must_contain_any": [], "must_not_contain": [], "lang": "en"},
    {"q": "x2", "tag": "random", "kind": ["answer"], "services": None, "must_contain_any": [], "must_not_contain": [], "lang": "en"},
]}]
with tempfile.TemporaryDirectory() as td:
    two_path = Path(td) / "two.json"
    two_path.write_text(json.dumps(TWO, ensure_ascii=False), encoding="utf-8")
    two_loaded = load_cases(two_path)
    check("load_cases reads the given file", two_loaded == TWO)
    check("load_cases accepts a str path too", load_cases(str(two_path)) == TWO)
    bad = Path(td) / "bad.json"
    bad.write_text('{"id": "not a list"}', encoding="utf-8")
    for label, p in (("missing file", Path(td) / "nope.json"), ("JSON object instead of a list", bad)):
        try:
            load_cases(p)
            raised = False
        except SystemExit as e:
            raised = "nope.json" in str(e) or "bad.json" in str(e)
        check(f"load_cases: {label} -> SystemExit naming the file", raised)


def two_handle(q, state):
    return ({"kind": "answer", "answer": EN_TXT if q == "x1" else BN_TXT, "services": [], "hits": []}, state)


trows = run_suite(two_loaded, two_handle, {}, route="llm")
tcard = scorecard(trows)
check("a 2-case file prints total 1/2 (denominator = cases in the file)", "total 1/2" in tcard, str(tcard))
check("tags present in the file print their line", "other 1/1" in tcard and "random 0/1" in tcard, str(tcard))
check("a tag with no case prints no line", not any(x.startswith("followup") for x in tcard), str(tcard))
check("language denominator = turns with lang set in the file", "language 1/2" in tcard, str(tcard))
check("scorecard of nothing prints no tag lines", not any(x.split(" ")[0] in ("followup", "random", "other") for x in scorecard([])), str(scorecard([])))
check("scorecard of nothing: total 0/0", "total 0/0" in scorecard([]))
check("default file keeps all three tag lines", all(any(x.startswith(t + " ") for x in scorecard(
    [{"tag": t, "passed": True, "kind": "answer", "text": "x", "factcheck": [], "wall_s": 1.0, "route": "llm", "requested_route": "llm"}
     for t in ("followup", "random", "other")])) for t in ("followup", "random", "other")))

section("--cases")
check("--cases defaults to None (-> conversation_cases.json)", parse_args(["--route", "llm"]).cases is None)
check("--cases FILE is parsed", parse_args(["--route", "registry", "--cases", "x.json"]).cases == "x.json")
check("--cases does not disturb the other options",
      (lambda a: (a.route, a.out, a.env_bat, a.cases))(parse_args(["--route", "llm", "--out", "o.jsonl", "--cases", "h.json"])) == ("llm", "o.jsonl", None, "h.json"))
check("load_cases() with no path reads the default suite", load_cases() == json.load(open("conversation_cases.json", encoding="utf-8")))
check("load_cases(None) reads the default suite", load_cases(None) == load_cases(CASES_PATH))


# ------------------------------------------------------------------ the suite file
section("conversation_cases.json")
raw_bytes = Path(CASES_PATH).read_bytes()
check("file is valid UTF-8 without BOM", not raw_bytes.startswith(b"\xef\xbb\xbf") and raw_bytes.decode("utf-8") is not None)
convs = json.load(open("conversation_cases.json", encoding="utf-8"))
# NOTE: the brief's sample test says 41 turns, but its own suite table (and the prototype harness it
# was transcribed from) has 40 rows: 11 followup + 13 random + 16 other. The table is authoritative.
check("suite has 40 turns: 11 followup, 13 random", counts(convs) == (40, 11, 13), str(counts(convs)))
check("suite has 16 'other' turns", sum(t["tag"] == "other" for c in convs for t in c["turns"]) == 16)
check("8 conversations C1..C8", [c["id"] for c in convs] == [f"C{i}" for i in range(1, 9)])
check("turns per conversation 5,5,5,5,5,5,4,6", [len(c["turns"]) for c in convs] == [5, 5, 5, 5, 5, 5, 4, 6])
check("every conversation has a title", all(isinstance(c["title"], str) and c["title"] for c in convs))
KEYS = {"q", "tag", "kind", "services", "must_contain_any", "must_not_contain", "lang"}
LIVE = {"nid", "epassport", "ereturn", "bdris"}
turns = [t for c in convs for t in c["turns"]]
check("every case has exactly the schema keys", all(set(t) == KEYS for t in turns))
check("tags are followup/random/other", all(t["tag"] in ("followup", "random", "other") for t in turns))
check("kind is a non-empty list of answer/clarify", all(isinstance(t["kind"], list) and t["kind"] and set(t["kind"]) <= {"answer", "clarify"} for t in turns))
check("services is null or a non-empty list of live services",
      all(t["services"] is None or (isinstance(t["services"], list) and t["services"] and set(t["services"]) <= LIVE) for t in turns))
check("content lists are lists of non-empty strings",
      all(isinstance(t[k], list) and all(isinstance(s, str) and s for s in t[k]) for t in turns for k in ("must_contain_any", "must_not_contain")))
check("every question is a non-empty string", all(isinstance(t["q"], str) and t["q"].strip() for t in turns))
check("every turn has lang bn / en / null", all("lang" in t and t["lang"] in ("bn", "en", None) for t in turns))
check("lang counts: bn 27, en 11, null 2",
      (sum(t["lang"] == "bn" for t in turns), sum(t["lang"] == "en" for t in turns), sum(t["lang"] is None for t in turns)) == (27, 11, 2),
      str([t["lang"] for t in turns]))
LANGS = {"C1": "bn bn bn bn bn", "C2": "en en en en en", "C3": "bn bn bn bn bn", "C4": "en en en bn en",
         "C5": "bn bn bn bn bn", "C6": "en bn bn bn bn", "C7": "bn en null bn", "C8": "bn bn bn bn bn null"}
check("lang per turn as in the brief",
      {c["id"]: " ".join("null" if t["lang"] is None else t["lang"] for t in c["turns"]) for c in convs} == LANGS,
      str({c["id"]: [t["lang"] for t in c["turns"]] for c in convs}))
check("random and follow-up service expectations as in the brief",
      all(t["services"] is None for t in turns if t["tag"] == "random")
      and all(t["services"] for t in turns if t["tag"] == "followup"))
FEES = ["৮,০৫০", "8,050", "১০,৩৫০", "10,350", "১৩,৮০০", "13,800"]
check("C1 weather/thanks turns forbid the fee figures",
      convs[0]["turns"][2]["must_not_contain"] == FEES and convs[0]["turns"][4]["must_not_contain"] == FEES)
check("C7 turn 1 expects a clarify, turn 3 answer-or-clarify",
      convs[6]["turns"][0]["kind"] == ["clarify"] and convs[6]["turns"][2]["kind"] == ["answer", "clarify"])
check("Bangla text kept verbatim (C3 turn 1)", convs[2]["turns"][0]["q"] == "জন্ম নিবন্ধন সংশোধন করতে কী কী কাগজ লাগে?")
check("CASES_PATH points at the repo file", Path(CASES_PATH).name == "conversation_cases.json" and Path(CASES_PATH).exists())

section("conversation_cases_heldout.json")
HELD_PATH = Path(CASES_PATH).with_name("conversation_cases_heldout.json")
check("held-out file exists next to the main suite", HELD_PATH.exists())
held_bytes = HELD_PATH.read_bytes()
check("held-out file is valid UTF-8 without BOM", not held_bytes.startswith(b"\xef\xbb\xbf") and held_bytes.decode("utf-8") is not None)
held = load_cases(HELD_PATH)
held_turns = [t for c in held for t in c["turns"]]
check("held-out: 7 conversations H1..H7, one turn each",
      [c["id"] for c in held] == [f"H{i}" for i in range(1, 8)] and [len(c["turns"]) for c in held] == [1] * 7 and len(held_turns) == 7)
check("held-out: every conversation has a title", all(isinstance(c["title"], str) and c["title"] for c in held))
check("held-out: every case has exactly the schema keys (incl. lang)", all(set(t) == KEYS for t in held_turns))
HELD_EXPECT = [
    ("renew er fee koto?", "other", ["clarify"], None, "bn"),
    ("How much is the correction fee?", "other", ["clarify"], None, "en"),
    ("pasport er meyad shesh, notun banate chai", "other", ["answer"], ["epassport"], "bn"),
    ("jonmo nibondhoner bhul shongshodhon er niyom bolen", "other", ["answer"], ["bdris"], "bn"),
    ("baba mara gechen, mrittu nibondhon dorkar", "other", ["answer"], ["bdris"], "bn"),
    ("NID ar passport duitar fee koto?", "other", ["answer"], ["nid", "epassport"], "bn"),
    ("cricket match er score janen?", "random", ["answer"], None, "bn"),
]
check("held-out: q / tag / kind / services / lang exactly as specified",
      [(t["q"], t["tag"], t["kind"], t["services"], t["lang"]) for t in held_turns] == HELD_EXPECT,
      str([(t["q"], t["tag"], t["kind"], t["services"], t["lang"]) for t in held_turns]))
check("held-out: must_contain_any / must_not_contain are empty lists",
      all(t["must_contain_any"] == [] and t["must_not_contain"] == [] for t in held_turns))
check("held-out: services are live ids or null", all(t["services"] is None or set(t["services"]) <= LIVE for t in held_turns))
check("held-out: no question is also in the main suite",
      not ({t["q"].lower() for t in held_turns} & {t["q"].lower() for t in turns}))
check("held-out: lang counts bn 6, en 1", (sum(t["lang"] == "bn" for t in held_turns), sum(t["lang"] == "en" for t in held_turns)) == (6, 1))
check("held-out file runs through run_suite with a fresh state per conversation",
      [r["conv"] for r in run_suite(held, lambda q, s: ({"kind": "clarify", "question": "Which?"}, dict(s, seen=True)), {}, route="llm")] == [f"H{i}" for i in range(1, 8)])

# ------------------------------------------------------------------ importing must stay light
section("import hygiene")
check("heavy modules were not imported by eval_conversations",
      not any(m in sys.modules for m in ("chromadb", "sentence_transformers", "answer", "llm_backends")),
      str([m for m in ("chromadb", "sentence_transformers", "answer", "llm_backends") if m in sys.modules]))

print("\n" + "=" * 74)
print("Result:", "ALL CHECKS PASS" if not FAILS else f"{len(FAILS)} FAIL — " + ", ".join(FAILS))
if FAILS:
    sys.exit(1)
