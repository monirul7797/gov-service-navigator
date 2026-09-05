"""
freshness/quality.py — deterministic capture-quality quarantine gate
(Freshness V2 §7).

An HTTP 200 / successful navigation is NOT automatically trustworthy content.
Before any capture may become a trusted baseline, a candidate change, or a
trusted discovery-inventory resource, these conservative explicit heuristics
run. A failing capture becomes `suspect_capture` (or a specific quality code),
keeps its evidence for diagnostics, and NEVER advances trusted state.

No probabilistic scoring — explicit heuristics with tests only.
"""
from __future__ import annotations

import re

# quality verdicts
OK = "ok"
EMPTY = "empty_content"
BOT_WALL = "bot_wall_or_access_denied"
MAINTENANCE = "maintenance_or_error_page"
LOGIN_SHELL = "login_shell"
SUSPECT_COLLAPSE = "massive_collapse_vs_baseline"
UNUSABLE_ENCODING = "unusable_encoding"

MIN_MEANINGFUL_CHARS = 60

# deterministic bot-wall / access-denied markers (case-insensitive substrings)
_BOT_WALL_RE = re.compile(
    r"access denied|you don't have permission|attention required|"
    r"request unsuccessful\. incapsula|ddos protection by|"
    r"just a moment\.\.\.|checking your browser|verify you are (?:a )?human|"
    r"captcha|unusual traffic|pardon our interruption",
    re.I,
)
_MAINTENANCE_RE = re.compile(
    r"site under maintenance|under maintenance|service unavailable|"
    r"temporarily unavailable|503 service unavailable|"
    r"problem loading page|server error|this page isn'?t working",
    re.I,
)
_LOGIN_SHELL_RE = re.compile(
    r"please (?:sign|log) ?in to continue|login required|"
    r"sign in to (?:view|access|continue)",
    re.I,
)


def classify_capture(text: str, *, baseline_text: str | None = None) -> tuple[str, str]:
    """Return (quality_code, reason). quality_code == OK means the capture may
    be trusted onward (subject to the normal drift comparison).

    baseline_text: the currently trusted snapshot text, when one exists —
    used only for the conservative collapse check (§7 'massive unexpected
    collapse versus trusted baseline').
    """
    text = text or ""
    stripped = text.strip()

    if len(stripped) < MIN_MEANINGFUL_CHARS:
        return EMPTY, f"only {len(stripped)} normalized chars (minimum {MIN_MEANINGFUL_CHARS})"

    m = _BOT_WALL_RE.search(stripped)
    if m:
        return BOT_WALL, f"bot-wall/access-denied marker: {m.group(0)!r}"

    m = _MAINTENANCE_RE.search(stripped)
    if m and len(stripped) < 2000:
        # a maintenance notice as the dominant content of a short page
        return MAINTENANCE, f"error/maintenance page marker: {m.group(0)!r}"

    m = _LOGIN_SHELL_RE.search(stripped)
    if m and len(stripped) < 800:
        return LOGIN_SHELL, f"login shell marker: {m.group(0)!r}"

    if stripped.count("\ufffd") > max(20, len(stripped) * 0.05):
        return UNUSABLE_ENCODING, "high replacement-character ratio (broken decoding)"

    if baseline_text and len(baseline_text.strip()) >= 500:
        if len(stripped) < len(baseline_text.strip()) * 0.2:
            return SUSPECT_COLLAPSE, (
                f"capture has {len(stripped)} chars vs trusted baseline "
                f"{len(baseline_text.strip())} (>80% collapse)")

    return OK, ""
