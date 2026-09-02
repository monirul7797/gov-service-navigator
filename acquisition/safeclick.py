"""
acquisition/safeclick.py — the safe interaction policy (Phase 3.5 §7).

Decides whether the crawler may activate an interactive element found on a
page. Write/transactional/identity actions and anything PII-adjacent are
NEVER activated; ambiguous elements are skipped and surfaced as
`TODO(Monirul): review interaction` by the caller.

Pure classification only — the caller performs (or withholds) the click.
"""
from __future__ import annotations

import re

# Actions that write, transact, authenticate, reveal personal data, or advance
# an application flow. NEVER auto-activated (Phase 3.5 §7 list, verbatim intent).
UNSAFE_TEXT_RE = re.compile(
    r"\b(submit|apply|login|log[- ]?in|sign[- ]?in|continue|save|delete|pay|"
    r"payment|confirm|book|schedule|upload|send|next|reprint|view[- ]?status|"
    r"incomplete|register|checkout|download\b.+\bform\b|proceed|verify|otp|"
    r"password|resend|search)\b",
    re.I,
)

# Attributes that mark write/transactional or identity controls regardless of
# visible text.
UNSAFE_ATTR_RE = re.compile(
    r"type=[\"']?(submit|file|password)[\"']?|\bonclick\b.*\b(submit|post|pay|"
    r"login|delete|save)\b",
    re.I,
)

# Verdicts
SAFE = "safe"
UNSAFE = "unsafe"
AMBIGUOUS = "ambiguous"


def classify_element(tag: str, text: str, attributes: str, *,
                     inside_form: bool = False) -> tuple[str, str]:
    """Classify a candidate interactive element.

    tag        — lowercase tag name ('a', 'button', 'input', …)
    text       — visible label / innerText (may be empty)
    attributes — the element's attribute string (id, class, type, onclick…)
    inside_form— True when the element sits inside a <form>

    Returns (verdict, reason). Verdict is SAFE, UNSAFE, or AMBIGUOUS.
    Policy: any UNSAFE signal wins; then ambiguity; only explicit survivors
    are SAFE.
    """
    tag = (tag or "").lower()
    text = (text or "").strip()
    attributes = attributes or ""

    if tag in {"input", "textarea", "select"}:
        return UNSAFE, "form input control"
    if tag == "form":
        return UNSAFE, "form element"
    if inside_form:
        # A real form control inside a form can submit or mutate state — never
        # auto-activated. But NOTE: ASP.NET/WebForms sites wrap the ENTIRE page
        # in one <form>, so a plain anchor inside a form is still just a link
        # (its default action is navigation, never form submission) and is
        # judged normally below. Buttons / role-buttons inside forms stay
        # unsafe regardless of their label.
        if tag == "button" or 'role="button"' in attributes.lower():
            return UNSAFE, "button inside a <form> — could submit the form"
    if UNSAFE_ATTR_RE.search(attributes):
        return UNSAFE, "write/transactional/identity attribute signal"
    if UNSAFE_TEXT_RE.search(text):
        return UNSAFE, "write/transactional/identity label"
    if tag == "button" and not text:
        return AMBIGUOUS, "button with no visible label"
    if not text and not re.search(r"(button|toggle|dialog|expand|collapse|accordion|tab|modal|more|detail|fee|countr)", attributes, re.I):
        return AMBIGUOUS, "no label and no informational-control signal"
    return SAFE, "informational interaction (no write/identity signals)"
