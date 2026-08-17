"""
test_continuation_failsafe.py — quick, throwaway check that is_continuation() fails
CLOSED (False / NEW) on a backend exception, instead of the old fail-OPEN (True / FOLLOWUP).

Not part of the real test_multiturn.py suite — just a one-off to exercise the exception
branch specifically, since the hermetic suite mocks a working backend and never triggers it.

Run: python test_continuation_failsafe.py
"""

from normalize_query import is_continuation


class RaisingBackend:
    """Simulates a backend whose .complete() call fails (timeout, API error, etc.)."""
    name = "raising-stub"

    def complete(self, system_prompt, user_prompt, *, max_tokens=8, temperature=0.0):
        raise RuntimeError("simulated backend failure")


class ScriptedBackend:
    """Simulates a healthy backend that returns a fixed classification, to confirm the
    normal (non-exception) path is untouched by this patch."""
    name = "scripted-stub"

    def __init__(self, reply: str):
        self.reply = reply

    def complete(self, system_prompt, user_prompt, *, max_tokens=8, temperature=0.0):
        return self.reply


def check(label, got, expected):
    status = "PASS" if got == expected else "FAIL"
    print(f"[{status}] {label}: got={got!r} expected={expected!r}")
    return got == expected


def main():
    results = []

    # The case this patch is actually about: backend raises -> must fail to False (NEW).
    results.append(check(
        "backend exception -> fails to NEW (False)",
        is_continuation("gold price today?", "correction fee koto?", RaisingBackend()),
        False,
    ))

    # Sanity: a healthy backend saying FOLLOWUP still returns True.
    results.append(check(
        "healthy backend, 'FOLLOWUP' -> True",
        is_continuation("64 page ta", "10 year passport fee koto?", ScriptedBackend("FOLLOWUP")),
        True,
    ))

    # Sanity: a healthy backend saying NEW still returns False.
    results.append(check(
        "healthy backend, 'NEW' -> False",
        is_continuation("gold price today?", "correction fee koto?", ScriptedBackend("NEW")),
        False,
    ))

    # Sanity: ambiguous/garbled output still biases toward continuation (unchanged behavior).
    results.append(check(
        "healthy backend, ambiguous output -> True (unchanged bias)",
        is_continuation("hmm okay", "10 year passport fee koto?", ScriptedBackend("sure, FOLLOWUP i guess")),
        True,
    ))

    print()
    if all(results):
        print(f"All {len(results)} checks passed.")
    else:
        failed = len(results) - sum(results)
        print(f"{failed} of {len(results)} checks FAILED.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
