"""
acquisition — shared, provenance-aware acquisition layer (Phase 3.5).

One acquisition pipeline for freshness checks and new-service corpus work:

    strict normal fetch (TLS verified)
        ↓ on failure / JS-only content
    headless Chromium, strict context (still certificate-validated)
        ↓ only on TLS-invalid
    Chromium evidence context (ignore_https_errors=True) → capture_only,
        never treated as verified
        ↓ only if the browser itself is blocked
    needs_manual_screenshot (last fallback, never the first)

See docs/ACQUISITION_LAYER.md and the addendum in
gsn-freshness-and-new-service-spec.md for the full architecture note.
"""
from .urls import classify_url, normalize_url          # noqa: F401
from .safeclick import SAFE, UNSAFE, AMBIGUOUS, classify_element  # noqa: F401
from .robots import RobotsCache, robots_check          # noqa: F401
