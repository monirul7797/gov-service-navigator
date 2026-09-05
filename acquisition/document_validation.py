"""
acquisition/document_validation.py — PDF content validation before trust
(P01, R8).

Public helper:

    validate_pdf(body: bytes, content_type: str) -> tuple[bool, str]

Returns (True, '') for a valid, parseable PDF and (False, stable_reason) for
rejected input, distinguishing wrong MIME type, invalid signature, structural
parse failure and unavailable parser.

Policy (P01 fixed decisions):
  * a `%PDF-` prefix or a .pdf extension alone is insufficient — the bytes
    must structurally parse with the existing pdfplumber parser and expose at
    least one accessible page;
  * MIME is normalized (case, parameters); explicit conflicting HTML MIME is
    rejected even if the bytes would parse; application/pdf,
    application/octet-stream and an absent MIME are allowed subject to the
    signature and structural checks;
  * encrypted/inaccessible documents fail the structural parse and are
    rejected with the parser's diagnostic;
  * a missing parser is an unavailable-validation rejection — never automatic
    acceptance and never an installation attempt;
  * no network access, no rendering, no artifact writes.
"""
from __future__ import annotations

import io

_ALLOWED_MIME = ("application/pdf", "application/octet-stream")


def validate_pdf(body: bytes, content_type: str) -> tuple[bool, str]:
    """Validate PDF bytes and their declared MIME type.

    Returns (True, '') when `body` is a structurally valid, parseable PDF
    acceptable under `content_type`; otherwise (False, reason) with a stable
    diagnostic reason.
    """
    mime = (content_type or "").split(";", 1)[0].strip().lower()

    if mime and mime not in _ALLOWED_MIME:
        # explicit conflicting type (e.g. text/html) — rejected even if the
        # bytes themselves would parse
        return False, f"conflicting_mime_type: declared {content_type!r}"

    if not body or not body.startswith(b"%PDF-"):
        return False, "invalid_signature: body does not start with %PDF-"

    try:
        import pdfplumber
    except Exception as exc:  # ImportError and anything else blocking the parser
        return False, f"parser_unavailable: {type(exc).__name__}: {exc}"

    try:
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            # P01-C2: successful opening does not mean unencrypted — a
            # genuinely encrypted PDF with an empty user password opens and
            # parses fine, so encryption metadata is inspected explicitly
            # before acceptance.
            encryption = getattr(getattr(pdf, "doc", None), "encryption", None)
            if encryption is not None:
                return False, (f"encrypted_pdf: document reports encryption "
                               f"metadata ({type(encryption).__name__})")
            page_count = len(pdf.pages)
    except Exception as exc:
        # malformed/truncated content, encrypted or otherwise inaccessible
        # documents surface here as structural parse failures
        detail = f"{type(exc).__name__}: {exc}"[:200]
        return False, f"parse_failure: {detail}"

    if page_count < 1:
        return False, "parse_failure: no accessible pages"

    return True, ""
