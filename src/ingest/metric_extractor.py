"""Regex-based extraction of headline metrics from report text, with per-metric
provenance so each number can be traced back to the exact PDF row that yielded
it.

Pragmatic, transparent, and tunable. Not a replacement for XBRL or audited
tagging, but enough to support deterministic red-flag detection on Nordic
interim reports.

Public surface
--------------
- extract_headline_metrics(text)
      -> dict of metric_key -> float (unchanged for backwards compat callers).
- extract_headline_metrics_with_provenance(text)
      -> (metrics: dict, provenance: dict)
         provenance[metric_key] = {
             "value": float,
             "regex_id": str,            # which pattern fired
             "raw_match": str,           # full matched substring (~100 chars)
             "char_span": [start, end],  # span of `raw_match` in text
             "source": "auto_regex",
             "snippet_anchor": str,      # short phrase suitable for PyMuPDF search
         }

Versioning
----------
v0.4: every helper now returns (value, provenance_dict) instead of just value;
      the orchestrator uses the provenance dicts to crop per-metric snippets.
"""
import re


NUMBER = r"(-?\d{1,4}(?:[.,]\d+)?)"
LARGE_NUMBER = r"(-?\d{1,3}(?:[ \u00a0]?\d{3})*(?:[.,]\d+)?)"
CURRENCY_TOKEN = r"(?:MSEK|MNOK|MEUR|MUSD|SEK\s*m|NOK\s*m|EUR\s*m|USD\s*m|SEK\s*million|NOK\s*million|EUR\s*million|USD\s*million|MSEK|MNOK|MEUR)"


def _to_float(raw):
    if raw is None:
        return None
    cleaned = raw.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _safe_div(num, denom):
    if num is None or denom is None:
        return None
    if denom == 0:
        return None
    return num / denom


def _trim_anchor(raw_match, max_len=80):
    """Build a short, unambiguous search phrase from the raw regex match.
    Strips leading/trailing whitespace and collapses internal newlines so
    PyMuPDF's search_for() can find it in one shot."""
    if not raw_match:
        return None
    cleaned = " ".join(raw_match.split()).strip()
    return cleaned[:max_len] if cleaned else None


def _provenance(regex_id, value, raw_match, span):
    """Build a uniform provenance record for a single metric."""
    return {
        "value": value,
        "regex_id": regex_id,
        "raw_match": raw_match,
        "char_span": [span[0], span[1]] if span else None,
        "source": "auto_regex",
        "snippet_anchor": _trim_anchor(raw_match),
    }


def _find_first_with_prov(regex_id, pattern, text, group=1, flags=re.IGNORECASE):
    """Search pattern; return (value, provenance) or (None, None)."""
    m = re.search(pattern, text, flags)
    if not m:
        return None, None
    raw = m.group(group)
    value = _to_float(raw)
    if value is None:
        return None, None
    return value, _provenance(regex_id, value, m.group(0), m.span())


def _largest_in_window_with_prov(
    regex_id, anchor_pattern, text, window_chars=120, flags=re.IGNORECASE
):
    """Find anchor; return the largest numeric token within `window_chars` AND
    its provenance (anchor + window text)."""
    m = re.search(anchor_pattern, text, flags)
    if not m:
        return None, None
    start = m.end()
    snippet = text[start:start + window_chars]
    nums = list(re.finditer(LARGE_NUMBER, snippet))
    if not nums:
        return None, None
    best = None
    for n in nums:
        v = _to_float(n.group(1))
        if v is None:
            continue
        if best is None or abs(v) > abs(best[0]):
            best = (v, n)
    if best is None:
        return None, None
    value, num_match = best
    raw = text[m.start():start + num_match.end()]
    return value, _provenance(regex_id, value, raw, (m.start(), start + num_match.end()))


def _absolute_first_with_prov(
    regex_id, anchor_pattern, text, window_chars=120
):
    """First numeric token after the anchor."""
    m = re.search(anchor_pattern, text, re.IGNORECASE)
    if not m:
        return None, None
    start = m.end()
    snippet = text[start:start + window_chars]
    num_match = re.search(LARGE_NUMBER, snippet)
    if not num_match:
        return None, None
    value = _to_float(num_match.group(1))
    if value is None:
        return None, None
    raw = text[m.start():start + num_match.end()]
    return value, _provenance(regex_id, value, raw, (m.start(), start + num_match.end()))


# ---------------------------------------------------------------------------
# Detector-facing helpers
# ---------------------------------------------------------------------------


def extract_leverage_metrics_with_provenance(text):
    """Return (metrics_dict, provenance_dict) for leverage / coverage block."""
    metrics = {}
    prov = {}

    nd_v, nd_p = _absolute_first_with_prov(
        "net_debt_anchor",
        r"\b(?:net\s+interest[- ]bearing\s+debt|net\s+debt|netto\s+rantebarande\s+skulder|"
        r"netto\s+rentebaerende\s+gjeld|netto\s+gjeld)\b[^\n]{0,30}",
        text,
    )
    metrics["net_debt_msek_raw"] = nd_v
    if nd_p:
        prov["net_debt_msek_raw"] = nd_p

    ie_v, ie_p = _absolute_first_with_prov(
        "interest_expense_anchor",
        r"\b(?:interest\s+expense|interest\s+costs|interest\s+paid|"
        r"finance\s+costs|net\s+finance\s+costs|finansiella\s+kostnader|"
        r"rantekostnader|rentekostnader)\b[^\n]{0,30}",
        text,
    )
    metrics["interest_expense_msek_raw"] = ie_v
    if ie_p:
        prov["interest_expense_msek_raw"] = ie_p

    eb_v, eb_p = _largest_in_window_with_prov(
        "ebitda_for_leverage_anchor",
        r"\b(?:ltm\s+ebitda|ebitda\s+ltm|adjusted\s+ebitda|ebitda)\b[^\n]{0,20}",
        text,
        window_chars=80,
    )
    metrics["ebitda_for_leverage_msek_raw"] = eb_v
    if eb_p:
        prov["ebitda_for_leverage_msek_raw"] = eb_p

    cov_v, cov_p = _find_first_with_prov(
        "interest_coverage_explicit",
        r"(?:interest\s+coverage(?:\s+ratio)?|rantetackningsgrad|rentedekningsgrad)"
        r"[^.\n]{0,40}?(-?\d+(?:[.,]\d+)?)\s*x?",
        text,
    )
    if cov_v is None:
        # Derive from raw inputs if explicit ratio was not stated.
        cov_v = _safe_div(eb_v, ie_v)
        if cov_v is not None:
            cov_p = {
                "value": round(cov_v, 2),
                "regex_id": "interest_coverage_derived",
                "raw_match": "(derived) EBITDA / Interest expense",
                "char_span": None,
                "source": "auto_derived",
                "snippet_anchor": None,
                "derived_from": ["ebitda_for_leverage_msek_raw", "interest_expense_msek_raw"],
            }
    if cov_v is not None:
        metrics["interest_coverage_ratio_auto"] = round(cov_v, 2)
        if cov_p:
            prov["interest_coverage_ratio_auto"] = cov_p

    lev_v, lev_p = _find_first_with_prov(
        "leverage_explicit",
        r"(?:net\s+debt\s*/\s*ebitda|net\s+debt\s+to\s+ebitda|nettoskuld\s*/\s*ebitda|"
        r"leverage\s+ratio)[^.\n]{0,40}?(-?\d+(?:[.,]\d+)?)\s*x?",
        text,
    )
    if lev_v is None:
        lev_v = _safe_div(nd_v, eb_v)
        if lev_v is not None:
            lev_p = {
                "value": round(lev_v, 2),
                "regex_id": "leverage_derived",
                "raw_match": "(derived) Net debt / EBITDA",
                "char_span": None,
                "source": "auto_derived",
                "snippet_anchor": None,
                "derived_from": ["net_debt_msek_raw", "ebitda_for_leverage_msek_raw"],
            }
    if lev_v is not None:
        metrics["net_debt_to_ebitda_auto"] = round(lev_v, 2)
        if lev_p:
            prov["net_debt_to_ebitda_auto"] = lev_p

    return metrics, prov


def extract_headline_metrics_with_provenance(text):
    """Headline metric extraction with per-metric provenance.

    Returns:
        (metrics_dict, provenance_dict)
        metrics_dict: metric_key -> numeric value (or None)
        provenance_dict: metric_key -> provenance record (only when value found)
    """
    metrics = {}
    prov = {}

    headline_specs = [
        ("net_sales_msek", "net_sales", r"Net sales[^.\n]{0,40}?" + NUMBER),
        ("ebitda_msek", "ebitda", r"\bEBITDA[^.\n]{0,40}?" + NUMBER),
        ("adjusted_ebitda_msek", "adjusted_ebitda", r"Adjusted EBITDA[^.\n]{0,40}?" + NUMBER),
        ("ebita_msek", "ebita", r"\bEBITA[^.\n]{0,40}?" + NUMBER),
        ("adjusted_ebita_msek", "adjusted_ebita", r"Adjusted EBITA[^.\n]{0,40}?" + NUMBER),
        ("cash_flow_operating_msek", "ocf", r"Cash flow from operating activities[^.\n]{0,40}?" + NUMBER),
        ("ebita_margin_pct", "ebita_margin",
         r"EBITA margin[^.\n]{0,40}?(-?\d+(?:[.,]\d+)?)\s*%"),
        ("ebitda_margin_pct", "ebitda_margin",
         r"EBITDA margin[^.\n]{0,40}?(-?\d+(?:[.,]\d+)?)\s*%"),
    ]
    for key, regex_id, pattern in headline_specs:
        v, p = _find_first_with_prov(regex_id, pattern, text)
        metrics[key] = v
        if p is not None:
            prov[key] = p

    growth_match = re.search(
        r"Net sales[^.\n]{0,80}?(up|down)\s+(\d+(?:[.,]\d+)?)\s*%",
        text, re.IGNORECASE,
    )
    if growth_match:
        sign = -1.0 if growth_match.group(1).lower() == "down" else 1.0
        value = sign * _to_float(growth_match.group(2))
        metrics["revenue_yoy_pct"] = value
        prov["revenue_yoy_pct"] = _provenance(
            "net_sales_yoy_arrow",
            value,
            growth_match.group(0),
            growth_match.span(),
        )

    leverage_metrics, leverage_prov = extract_leverage_metrics_with_provenance(text)
    metrics.update(leverage_metrics)
    prov.update(leverage_prov)

    cov = leverage_metrics.get("interest_coverage_ratio_auto")
    if cov is not None and 0 < cov < 50:
        metrics["interest_coverage_ratio"] = cov
        metrics["interest_coverage_ratio_source"] = "auto_regex"
        if "interest_coverage_ratio_auto" in leverage_prov:
            prov["interest_coverage_ratio"] = leverage_prov["interest_coverage_ratio_auto"]

    lev = leverage_metrics.get("net_debt_to_ebitda_auto")
    if lev is not None and -10 < lev < 25:
        metrics["net_debt_to_ebitda"] = lev
        metrics["net_debt_to_ebitda_source"] = "auto_regex"
        if "net_debt_to_ebitda_auto" in leverage_prov:
            prov["net_debt_to_ebitda"] = leverage_prov["net_debt_to_ebitda_auto"]

    return metrics, prov


# ---------------------------------------------------------------------------
# Backwards-compat shims (callers that only need the metrics dict)
# ---------------------------------------------------------------------------


def extract_leverage_metrics(text):
    m, _ = extract_leverage_metrics_with_provenance(text)
    return m


def extract_headline_metrics(text):
    m, _ = extract_headline_metrics_with_provenance(text)
    return m
