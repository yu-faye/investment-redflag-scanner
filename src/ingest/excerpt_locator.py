"""Locate a sentence-level excerpt inside a PDF.

Given a PDF path and a text fragment (e.g. `claim_excerpt` from a finding),
return:
  - page number (1-indexed)
  - bbox in pdf coords (x0, top, x1, bottom) covering the matched word run
  - sentence-context window (previous / matched / next sentence) for the
    dashboard drawer
  - normalised search query suitable for `#search=...` URL hashes that the
    Chrome / Firefox built-in PDF viewer will pick up

Falls back gracefully when the PDF is image-only or pdfplumber cannot
extract words: returns `None`.
"""
from __future__ import annotations

import functools
import os
import re
from typing import Dict, List, Optional

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|(?<=[.!?])\n+")
_WHITESPACE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WHITESPACE.sub(" ", text or "").strip()


def _normalise_for_search(text: str, max_words: int = 8) -> str:
    """Pick a high-signal phrase the browser PDF viewer can search for."""
    cleaned = _norm(text)
    cleaned = re.sub(r"[\"'`()\[\]{}<>]", "", cleaned)
    words = cleaned.split()
    if not words:
        return ""
    # PDF viewers ignore overlong queries; cap to ~8 words / ~80 chars
    truncated = " ".join(words[:max_words])
    return truncated[:80]


@functools.lru_cache(maxsize=64)
def _load_pdf_pages(pdf_path: str):
    """Return list of (page_no, words, full_text) tuples for `pdf_path`.
    LRU-cached so re-running the locator for many findings of the same report
    only pays the pdfplumber tax once."""
    try:
        import pdfplumber
    except ImportError:
        return None
    if not os.path.isfile(pdf_path):
        return None
    pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for idx, page in enumerate(pdf.pages, start=1):
                try:
                    words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
                except Exception:
                    words = []
                text = " ".join(w.get("text", "") for w in words)
                pages.append((idx, words, text))
    except Exception:
        return None
    return pages


def _locate_in_words(words: List[dict], needle: str) -> Optional[List[dict]]:
    """Find the consecutive word run whose joined text contains `needle`.
    Returns the matching word dicts (with x0/x1/top/bottom) or None.
    """
    if not words or not needle:
        return None
    target = _norm(needle).lower()
    if not target or len(target) < 8:
        return None
    target_tokens = target.split()
    if not target_tokens:
        return None

    flat = [(w, _norm(w.get("text", "")).lower()) for w in words]
    n = len(flat)
    head = target_tokens[0]
    for i in range(n):
        if flat[i][1] != head:
            continue
        # greedy expand to cover the target
        joined = []
        run = []
        for j in range(i, min(n, i + max(len(target_tokens) * 4, 24))):
            run.append(flat[j][0])
            joined.append(flat[j][1])
            joined_str = " ".join(joined)
            if target in joined_str:
                return run
            if len(joined_str) > len(target) * 3:
                break
    return None


def _bbox_for_run(run: List[dict]) -> Optional[Dict[str, float]]:
    if not run:
        return None
    x0 = min(w.get("x0", 0) for w in run)
    x1 = max(w.get("x1", 0) for w in run)
    top = min(w.get("top", 0) for w in run)
    bottom = max(w.get("bottom", 0) for w in run)
    return {
        "x0": float(x0),
        "top": float(top),
        "x1": float(x1),
        "bottom": float(bottom),
        "width": float(max(0, x1 - x0)),
        "height": float(max(0, bottom - top)),
    }


def _sentence_context(full_text: str, excerpt: str) -> Optional[Dict[str, str]]:
    """Return (prev, match, next) sentences around the excerpt in full_text."""
    target = _norm(excerpt).lower()
    if not target or not full_text:
        return None
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(full_text) if s.strip()]
    for idx, s in enumerate(sentences):
        if target[:60] in _norm(s).lower():
            return {
                "prev": sentences[idx - 1] if idx > 0 else "",
                "match": s,
                "next": sentences[idx + 1] if idx + 1 < len(sentences) else "",
            }
    return None


def locate_excerpt(pdf_path: str, excerpt: str) -> Optional[Dict]:
    """Public entry. Returns a dict suitable for serialising into provenance.

    Schema:
      {
        "page": int,            # 1-indexed
        "bbox": {x0, top, x1, bottom, width, height},
        "sentence_context": {prev, match, next},
        "normalised_search": str  # short phrase for #search= URL hash
      }
    """
    if not excerpt:
        return None
    excerpt_norm = _norm(excerpt)
    if len(excerpt_norm) < 12:
        return None

    pages = _load_pdf_pages(pdf_path)
    if not pages:
        return None
    for page_no, words, full_text in pages:
        run = _locate_in_words(words, excerpt_norm)
        if not run:
            continue
        bbox = _bbox_for_run(run)
        if not bbox:
            continue
        context = _sentence_context(full_text, excerpt_norm)
        return {
            "page": page_no,
            "bbox": bbox,
            "sentence_context": context,
            "normalised_search": _normalise_for_search(excerpt_norm),
        }
    # If no word-level run found but we know the page from the text,
    # fall back to a page-only locator with sentence context if possible.
    for page_no, _, full_text in pages:
        if excerpt_norm.lower()[:40] in _norm(full_text).lower():
            return {
                "page": page_no,
                "bbox": None,
                "sentence_context": _sentence_context(full_text, excerpt_norm),
                "normalised_search": _normalise_for_search(excerpt_norm),
            }
    return None
