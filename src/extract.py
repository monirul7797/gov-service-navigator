"""
extract.py — cached raw page -> list of clean text blocks.

HTML : BeautifulSoup, drop nav/script/style/footer boilerplate, keep headings
       with their following content so context survives chunking.
PDF  : pdfplumber, page by page.
TXT  : injected/cleaned content used as-is (offline mode).

Every block is normalized through bangla.normalize_text so mojibake repair and
NFC composition happen exactly once, at the boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .bangla import normalize_text
from .fetch import Fetched

_DROP_TAGS = ("script", "style", "nav", "footer", "header", "form",
              "noscript", "svg", "button", "input")


@dataclass
class Block:
    text: str
    heading: str      # nearest preceding heading, for context
    order: int


def extract(fetched: Fetched) -> List[Block]:
    if fetched.kind == "pdf":
        return _from_pdf(fetched.path)
    if fetched.kind == "txt":
        return _from_txt(fetched.path)
    return _from_html(fetched.path)


def _from_html(path: str) -> List[Block]:
    from bs4 import BeautifulSoup
    with open(path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    for t in soup(list(_DROP_TAGS)):
        t.decompose()

    blocks: List[Block] = []
    heading = ""
    order = 0
    # Walk block-level elements in document order.
    for el in soup.find_all(["h1", "h2", "h3", "h4", "li", "p", "td", "th",
                             "dt", "dd", "caption"]):
        raw = el.get_text(" ", strip=True)
        text = normalize_text(raw)
        if not text:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            heading = text
            continue
        if len(text) < 3:
            continue
        blocks.append(Block(text=text, heading=heading, order=order))
        order += 1
    return blocks


def _from_pdf(path: str) -> List[Block]:
    import pdfplumber
    blocks: List[Block] = []
    order = 0
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            raw = page.extract_text() or ""
            for para in raw.split("\n\n"):
                text = normalize_text(para)
                if len(text) >= 3:
                    blocks.append(Block(text=text, heading=f"page {i+1}", order=order))
                    order += 1
    return blocks


def _from_txt(path: str) -> List[Block]:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    blocks: List[Block] = []
    heading = ""
    order = 0
    for para in content.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        # Lines that are ALL-CAPS-ish or end with ':' act as headings.
        if len(para) < 80 and (para.endswith(":") or para.startswith("#")):
            heading = normalize_text(para.lstrip("#").rstrip(":").strip())
            continue
        text = normalize_text(para)
        if len(text) >= 3:
            blocks.append(Block(text=text, heading=heading, order=order))
            order += 1
    return blocks
