"""Locate a numeric metric inside a PDF using PyMuPDF (`fitz`).

Two locating strategies, in priority order:

1. `locate_by_anchor(pdf, anchor)` -- if the metric extractor handed us a
   short text snippet (e.g. "EBITDA margin 5.1 %"), feed it directly to
   `page.search_for()`. This is the unambiguous, auto-extracted case.

2. `locate_by_value(pdf, value, context_keywords)` -- for *manual_metrics*
   curated in `validation/companies.json`, we have only a bare number
   (e.g. -6.7). We generate likely string renderings of that value
   ("-6.7", "6.7%", "(6.7)", "-6.7 %", ...), call `page.search_for()` for
   each, then **score each hit by its distance to the nearest context
   keyword** ("revenue", "net sales", "growth", ...) on the same page.
   The closest-on-the-same-row hit wins.

Both strategies ultimately return a dict in the same shape:

    {
        "page": int,            # 1-indexed
        "bbox": {x0, top, x1, bottom, width, height},
        "matched_str": str,     # exact substring PyMuPDF matched
        "strategy": "anchor" | "value",
        "context_distance": float | None,
        "context_term": str | None,
    }

Designed to never raise on dirty input -- returns None on any failure so the
orchestrator can keep going even when PyMuPDF isn't available or the PDF is
image-only.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Iterable, Optional


def _try_import_fitz():
    try:
        import fitz  # type: ignore

        return fitz
    except ImportError:
        return None


@lru_cache(maxsize=16)
def _open_pdf(pdf_path: str):
    """Cached PDF open. Returns the fitz.Document or None on failure."""
    fitz_mod = _try_import_fitz()
    if fitz_mod is None:
        return None
    try:
        return fitz_mod.open(pdf_path)
    except Exception:
        return None


def _rect_to_bbox(rect) -> dict:
    return {
        "x0": float(rect.x0),
        "top": float(rect.y0),
        "x1": float(rect.x1),
        "bottom": float(rect.y1),
        "width": float(rect.width),
        "height": float(rect.height),
    }


def _candidate_value_strings(value: float) -> list:
    """Render a numeric value as the strings most likely to appear in the PDF."""
    if value is None:
        return []
    candidates = []
    abs_v = abs(value)
    looks_int = abs_v == int(abs_v)
    is_negative = value < 0
    formats = [
        ("{:.1f}", value),
        ("{:.2f}", value),
        ("{:+.1f}", value),
        ("{:.1f}%", value),
        ("{:.1f} %", value),
        # Parens convention for negatives: "(6.7)" or "(6.7%)"
        ("({:.1f})", abs_v) if is_negative else None,
        ("({:.1f}%)", abs_v) if is_negative else None,
        # Integer renderings -- when narrative rounds e.g. 6.7 -> 7%
        ("{:.0f}", value) if looks_int else None,
        ("{:.0f}%", value) if looks_int else None,
        # Bare absolute value -- tables often suppress the sign and rely on
        # the column header (e.g. "% YoY" or "Decline").
        ("{:.1f}", abs_v) if is_negative else None,
        ("{:.1f}%", abs_v) if is_negative else None,
        # Nearest-integer narrative rounding for non-integer values.
        ("{:.0f}", round(abs_v)) if not looks_int else None,
        ("{:.0f}%", round(abs_v)) if not looks_int else None,
        ("decrease of {:.0f}%", round(abs_v)) if is_negative else None,
        ("declined {:.0f}%", round(abs_v)) if is_negative else None,
    ]
    for spec in formats:
        if spec is None:
            continue
        fmt, v = spec
        try:
            s = fmt.format(v)
        except (ValueError, TypeError):
            continue
        if s not in candidates:
            candidates.append(s)
        # Nordic comma-decimal variant
        if "." in s and "%" not in fmt and "+" not in fmt:
            alt = s.replace(".", ",")
            if alt not in candidates:
                candidates.append(alt)
    # Drop overly short / ambiguous candidates (single digits and bare integers
    # match thousands of times in any financial PDF and would dominate the
    # context-distance race).
    candidates = [c for c in candidates if len(c.strip()) >= 3]
    # Prefer specific (long) candidates first.
    candidates.sort(key=lambda s: (-len(s), s))
    return candidates


def _page_search(page, needle: str, limit: int = 8) -> list:
    """Wrap page.search_for() to be defensive against API drift.

    Note: PyMuPDF 1.24+ dropped the legacy `hit_max` keyword; we just take the
    first `limit` hits here instead."""
    try:
        rects = page.search_for(needle, quads=False)
    except Exception:
        rects = []
    return list(rects)[:limit]


def _nearest_context(page, rect, context_keywords: Iterable[str]):
    """Return (term, distance_in_pt) of the closest context keyword on the
    same page, or (None, None) if no context keyword is found.

    Distance is Manhattan-distance between bbox centres, but we strongly
    prefer matches on the *same y-row* (so values inside the same table row
    as their label score best). Same-row matches use only |dx|."""
    best = (None, float("inf"))
    cx_v = (rect.x0 + rect.x1) / 2.0
    cy_v = (rect.y0 + rect.y1) / 2.0
    row_tolerance = max(rect.height * 0.7, 6.0)
    for term in context_keywords:
        if not term:
            continue
        try:
            hits = page.search_for(term, quads=False)[:20]
        except Exception:
            hits = []
        for h in hits:
            cx_h = (h.x0 + h.x1) / 2.0
            cy_h = (h.y0 + h.y1) / 2.0
            dy = abs(cy_h - cy_v)
            if dy <= row_tolerance:
                dist = abs(cx_h - cx_v) * 0.5  # same-row bonus
            else:
                dist = abs(cx_h - cx_v) + dy * 3.0  # penalise vertical jumps
            if dist < best[1]:
                best = (term, dist)
    if best[0] is None:
        return None, None
    return best[0], best[1]


def locate_by_anchor(pdf_path: str, anchor: str) -> Optional[dict]:
    """Search PDF for `anchor`. Returns the first hit, with bbox/page."""
    if not anchor or not pdf_path:
        return None
    doc = _open_pdf(pdf_path)
    if doc is None:
        return None
    try:
        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            rects = _page_search(page, anchor)
            if rects:
                return {
                    "page": page_idx + 1,
                    "bbox": _rect_to_bbox(rects[0]),
                    "matched_str": anchor,
                    "strategy": "anchor",
                    "context_distance": None,
                    "context_term": None,
                }
    except Exception:
        return None
    return None


def locate_by_value(
    pdf_path: str,
    value: float,
    context_keywords: Iterable[str],
    max_pages: Optional[int] = None,
    require_context: bool = True,
) -> Optional[dict]:
    """Search PDF for any string rendering of `value`.

    With `require_context=True` (default): only accept hits that share a page
    with at least one context keyword; pick the one closest (Manhattan distance,
    same-row preferred). With `require_context=False`: accept the first hit when
    no context keyword is found anywhere, but flag it via `confidence`."""
    if value is None or not pdf_path:
        return None
    candidates = _candidate_value_strings(value)
    if not candidates:
        return None
    doc = _open_pdf(pdf_path)
    if doc is None:
        return None
    pages_to_scan = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
    first_uncontextual = None
    try:
        # Iterate candidates in specificity order. Stop at the first candidate
        # that yields ANY context-anchored hit -- that one wins.
        for cand in candidates:
            best_for_cand = None
            for page_idx in range(pages_to_scan):
                page = doc[page_idx]
                rects = _page_search(page, cand)
                for r in rects:
                    term, dist = _nearest_context(page, r, context_keywords)
                    if term is None:
                        if first_uncontextual is None and not require_context:
                            first_uncontextual = {
                                "page": page_idx + 1,
                                "bbox": _rect_to_bbox(r),
                                "matched_str": cand,
                                "strategy": "value",
                                "context_distance": None,
                                "context_term": None,
                                "confidence": "low_no_context",
                            }
                        continue
                    if best_for_cand is None or dist < best_for_cand["context_distance"]:
                        best_for_cand = {
                            "page": page_idx + 1,
                            "bbox": _rect_to_bbox(r),
                            "matched_str": cand,
                            "strategy": "value",
                            "context_distance": float(dist),
                            "context_term": term,
                            "confidence": "high" if dist < 50 else "medium",
                        }
            if best_for_cand is not None:
                return best_for_cand
    except Exception:
        return None
    return first_uncontextual


def metric_to_context_keywords(metric_key: str) -> list:
    """Heuristic mapping from metric key to context keywords used for
    disambiguation when only the value is known."""
    base = {
        "revenue_yoy_pct": ["revenue", "net sales", "Net sales", "Revenue", "turnover"],
        "organic_growth_pct": ["organic growth", "organic", "underlying growth"],
        "gross_margin_yoy_change_pct": ["gross margin", "Gross margin"],
        "ebita_margin_pct": ["EBITA margin", "EBITA-margin", "EBITA margin"],
        "ebitda_margin_pct": ["EBITDA margin", "EBITDA-margin"],
        "interest_coverage_ratio": ["interest coverage", "Interest coverage", "rentedekning"],
        "net_debt_to_ebitda": ["net debt / EBITDA", "Net debt / EBITDA", "leverage ratio", "Leverage ratio"],
        "working_capital_to_revenue_pct": ["working capital", "Working capital"],
        "capex_to_depreciation_ratio": ["capex", "Capex", "depreciation"],
        "order_intake_yoy_pct": ["order intake", "Order intake", "ordreinngang"],
        "equity_msek": ["equity", "Equity", "total equity"],
        "negative_equity_flag": ["negative equity", "Negative equity"],
    }
    return base.get(metric_key, [metric_key.replace("_", " ")])
