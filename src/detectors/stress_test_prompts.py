"""Stress-test follow-up prompt generator.

Deterministic. Maps each red flag to 3-5 English follow-up questions covering:
- Cause probing
- Cost / trade-off probing
- Peer benchmarking
- Cash flow cross-check
- Disclosure completeness

These are designed to be pasted into an interview, an analyst memo, or a
Claude/GPT prompt to extend the audit attack surface.

v7: the bilingual (en + zh) prompt schema was retired. All output is English
only across detectors and dashboards.
"""
from copy import deepcopy


_TEMPLATES = {
    "lag_causality": [
        {
            "axis": "cause_probing",
            "text": "If '{claim_type}' was completed at {claim_period_raw}, what specific operational mechanism produced the metric impact already by {report_period_label}? Provide the transmission steps.",
        },
        {
            "axis": "cost_probing",
            "text": "Quantify the integration, restructuring, or transition costs absorbed during the same period; reconcile them against the asserted positive impact.",
        },
        {
            "axis": "peer_benchmarking",
            "text": "How long did comparable Nordic peers take to realise similar impact from the same action type? Please cite at least one precedent.",
        },
        {
            "axis": "cash_cross_check",
            "text": "Show the cash flow path supporting this impact: working capital change, receivables, and operating cash flow for the same period.",
        },
    ],
    "narrative_dissonance": [
        {
            "axis": "cause_probing",
            "text": "Management describes '{family}' qualitatively, yet supporting metrics ({metric_list}) move against it. Which specific events explain the gap?",
        },
        {
            "axis": "cost_probing",
            "text": "Was the narrative engineered by reclassification or by adjustments (e.g., 'continuing operations', 'adjusted EBITDA')? Provide the reconciliation.",
        },
        {
            "axis": "peer_benchmarking",
            "text": "Compare the same metric YoY change against at least two closest listed peers. Is the gap company-specific or industry-wide?",
        },
        {
            "axis": "disclosure_completeness",
            "text": "List the underlying segment metrics that would prove the narrative. Were they disclosed at the same granularity as last year?",
        },
    ],
    "selective_disclosure": [
        {
            "axis": "disclosure_completeness",
            "text": "Why was '{kpi_id}' (previously emphasised in {previous_period}) not reported in {current_period}? Please disclose the current value or the cessation rationale.",
        },
        {
            "axis": "cause_probing",
            "text": "Did the KPI deteriorate materially? Provide the current value and the YoY change you would have reported.",
        },
        {
            "axis": "peer_benchmarking",
            "text": "How do peers handle this KPI in their latest filings? Is omission consistent with industry practice?",
        },
        {
            "axis": "disclosure_completeness",
            "text": "Will this KPI be restored in the next filing under a standardised definition? Please commit a timeline.",
        },
    ],
    "cash_conversion_gap": [
        {
            "axis": "cause_probing",
            "text": "Decompose the cash-profit gap into receivables, inventory, accrued expenses, and tax items. Which sub-account dominates?",
        },
        {
            "axis": "cost_probing",
            "text": "If receivables are the driver, what is the change in days sales outstanding by major customer segment?",
        },
    ],
}


def _format_safe(template, ctx):
    try:
        return template.format(**ctx)
    except (KeyError, IndexError):
        return template


def attach_follow_ups(red_flag):
    """Return red_flag with follow_up_questions populated (English-only)."""
    rule_id = red_flag.get("rule_id")
    base = _TEMPLATES.get(rule_id, [])
    ctx = deepcopy(red_flag)

    if rule_id == "narrative_dissonance":
        metric_list = ", ".join(
            f"{m['metric']}={m['value']}" for m in red_flag.get("metric_alignment", [])
            if m.get("value") is not None
        )
        ctx["metric_list"] = metric_list or "n/a"

    questions = []
    for tpl in base:
        questions.append({
            "axis": tpl["axis"],
            "text": _format_safe(tpl["text"], ctx),
        })

    enriched = deepcopy(red_flag)
    enriched["follow_up_questions"] = questions
    return enriched


def attach_follow_ups_to_all(red_flags):
    return [attach_follow_ups(rf) for rf in red_flags]


if __name__ == "__main__":
    import json
    sample_rf = {
        "rule_id": "narrative_dissonance",
        "family": "growth",
        "metric_alignment": [{"metric": "revenue_yoy_pct", "value": -2.0, "score": -1}],
        "severity": "warning",
        "verdict": "dissonance_detected",
    }
    print(json.dumps(attach_follow_ups(sample_rf), indent=2))
