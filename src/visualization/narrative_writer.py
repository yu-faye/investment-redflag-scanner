"""Deterministic, zero-LLM auditor paragraph generator (v10 Phase B).

For each finding the writer emits a 2-5 sentence paragraph that reads
like an analyst's working note. Every claim of fact in the paragraph
carries an inline citation marker [1], [2], ... that the dashboards
turn into clickable anchors pointing at the underlying evidence
(PDF page, cache JSON file, engine rule note).

All wording is template-driven; the same input produces the same output
byte-for-byte. This is critical for an "auditable" pipeline -- a reader
can re-derive every word from the structured data.

Paragraph shape
---------------
{
  "headline":   "[CRITICAL] Qben Infra Q1 2026 revenue claim refuted",
  "body":       "Sentence one [1]. Sentence two [2]. Sentence three [3].",
  "sentences":  [{text, citations}, ...],
  "citations":  {"1": {label, href, kind}, "2": {...}, ...}
}
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


# ---- Severity prefixes used in the headline ------------------------------
_SEVERITY_PREFIX = {
    "info": "[INFO]",
    "warning": "[WARNING]",
    "critical": "[CRITICAL]",
}


def write_paragraph(finding: Dict[str, Any]) -> Dict[str, Any]:
    """Top-level dispatch. Returns a paragraph dict; never raises."""
    rule_id = finding.get("rule_id")
    if rule_id == "triangulated_hypothesis":
        category = (finding.get("triangulation") or {}).get("claim_category") \
            or finding.get("subtype")
        if category == "revenue_pipeline_support":
            return _write_revenue_pipeline(finding)
        return _write_subsidiary_specialist(finding)
    if rule_id == "narrative_dissonance":
        return _write_narrative_dissonance(finding)
    if rule_id == "selective_disclosure":
        return _write_selective_disclosure(finding)
    if rule_id == "lag_causality":
        return _write_lag_causality(finding)
    return _write_generic(finding)


# ---------------- Builder helpers -----------------------------------------


def _new_paragraph(
    headline: str, sentences: List[Tuple[str, List[Dict[str, Any]]]]
) -> Dict[str, Any]:
    """Take a list of (sentence_text, [citation_dicts]) tuples and build
    the final paragraph dict with deduplicated numbered citations."""
    sentences_out: List[Dict[str, Any]] = []
    citations: Dict[str, Dict[str, Any]] = {}
    cit_label_to_num: Dict[str, str] = {}
    counter = 0

    body_parts: List[str] = []
    for sent_text, sent_cits in sentences:
        cit_marks: List[str] = []
        for c in sent_cits:
            key = (c.get("label") or "") + "|" + (c.get("href") or "")
            if key not in cit_label_to_num:
                counter += 1
                num = str(counter)
                cit_label_to_num[key] = num
                citations[num] = {
                    "label": c.get("label") or "evidence",
                    "href": c.get("href"),
                    "kind": c.get("kind") or "evidence",
                }
            cit_marks.append(cit_label_to_num[key])
        joined_marks = " ".join(f"[{n}]" for n in cit_marks)
        full_sentence = sent_text.rstrip(".") + (" " + joined_marks if joined_marks else "") + "."
        sentences_out.append(
            {"text": sent_text, "citations": cit_marks}
        )
        body_parts.append(full_sentence)

    return {
        "headline": headline,
        "body": " ".join(body_parts),
        "sentences": sentences_out,
        "citations": citations,
    }


# ---------------- Revenue-pipeline paragraph ------------------------------


def _write_revenue_pipeline(finding: Dict[str, Any]) -> Dict[str, Any]:
    state = finding.get("triangulation") or {}
    severity = state.get("derived_severity") or finding.get("severity") or "info"
    company = finding.get("company_name") or finding.get("entity") or "Issuer"
    hyp_id = finding.get("hypothesis_id") or "?"
    latest = state.get("latest_per_tap") or {}

    rev_entry = latest.get("derived_revenue_support") or {}
    slip_entry = latest.get("derived_explanatory_slippage") or {}
    rev_summary = rev_entry.get("payload_summary") or {}
    slip_summary = slip_entry.get("payload_summary") or {}

    ext_pct = rev_summary.get("external_support_pct")
    thresh = rev_summary.get("external_support_threshold_pct", 50.0)
    n_peers = rev_summary.get("peers_evaluated", 0)
    total_post = rev_summary.get("total_post_acq_awards", 0)
    reported_rev = rev_summary.get("reported_revenue_msek")

    kpi_inputs = slip_summary.get("kpi_inputs") or {}
    rev_yoy = kpi_inputs.get("reported_revenue_yoy_pct")
    ebita_margin = kpi_inputs.get("reported_period_ebita_margin_pct")
    pos_count = slip_summary.get("positive_framing_match_count")
    ack_count = slip_summary.get("acknowledgement_match_count")

    rule_notes = state.get("category_rule_notes") or []

    headline = (
        f"{_SEVERITY_PREFIX.get(severity, '[NOTE]')} {company}: Q1 2026 "
        f"revenue claim {('REFUTED on both axes' if severity == 'critical' else 'under triangulation review')}."
    )

    sentences: List[Tuple[str, List[Dict[str, Any]]]] = []

    if reported_rev is not None and ext_pct is not None:
        sentences.append(
            (
                f"Reported continuing-operations revenue of {reported_rev:g} "
                f"MSEK is grounded in external order flow at only "
                f"{ext_pct:g}% of operating subsidiaries (threshold {thresh:g}%): "
                f"{total_post} confirmed post-acquisition prime contracts "
                f"across {n_peers} peer-subsidiary hypotheses",
                [
                    {
                        "label": "derived_revenue_support cache",
                        "href": rev_entry.get("raw_payload_ref"),
                        "kind": "cache",
                    }
                ],
            )
        )

    if rev_yoy is not None and ebita_margin is not None and pos_count is not None:
        sentences.append(
            (
                f"Yet the CEO-comment region contains {pos_count} positive-"
                f"framing tokens against {ack_count or 0} acknowledgement "
                f"tokens, while revenue declined {rev_yoy:+.1f}% YoY and "
                f"EBITA margin sits at {ebita_margin:+.1f}%",
                [
                    {
                        "label": "derived_explanatory_slippage cache",
                        "href": slip_entry.get("raw_payload_ref"),
                        "kind": "cache",
                    },
                    {
                        "label": "source PDF",
                        "href": slip_summary.get("source_pdf"),
                        "kind": "pdf",
                    },
                ],
            )
        )

    if rule_notes:
        sentences.append(
            (
                rule_notes[0].split(":", 1)[-1].strip().rstrip(".") if ":" in rule_notes[0] else rule_notes[0],
                [
                    {
                        "label": "engine R7 derivation",
                        "href": f"#engine-rule-r7-{hyp_id}",
                        "kind": "engine_rule",
                    }
                ],
            )
        )

    if not sentences:
        sentences.append(
            (
                "Insufficient tap evidence to compose a substantive paragraph",
                [],
            )
        )

    return _new_paragraph(headline, sentences)


# ---------------- Subsidiary-specialist paragraph -------------------------


def _write_subsidiary_specialist(finding: Dict[str, Any]) -> Dict[str, Any]:
    state = finding.get("triangulation") or {}
    severity = state.get("derived_severity") or finding.get("severity") or "info"
    entity = finding.get("entity") or "?"
    claim = finding.get("claim") or ""
    latest = state.get("latest_per_tap") or {}
    company_name = finding.get("company_name") or finding.get("company")

    sub_assessment = {
        "info": "supported by external evidence",
        "warning": "partially supported by external evidence",
        "critical": "REFUTED by external evidence",
    }.get(severity, "under triangulation review")

    headline = (
        f"{_SEVERITY_PREFIX.get(severity, '[NOTE]')} {company_name}: "
        f"{entity} -- claim {sub_assessment}."
    )

    sentences: List[Tuple[str, List[Dict[str, Any]]]] = []
    sentences.append(
        (
            f"Issuer claim: '{claim[:200]}'",
            [
                {
                    "label": "issuer PDF anchor",
                    "href": (finding.get("provenance") or {}).get("local_pdf"),
                    "kind": "pdf",
                }
            ],
        )
    )

    # One sentence per tap that reported.
    for tap_id, entry in latest.items():
        verdict = entry.get("verdict")
        narrative = entry.get("narrative") or ""
        raw_ref = entry.get("raw_payload_ref")
        sentences.append(
            (
                f"{tap_id} ({entry.get('tap_kind')}) reports {verdict}: "
                f"{narrative[:240].rstrip('. ')}",
                [
                    {
                        "label": f"{tap_id} cache",
                        "href": raw_ref,
                        "kind": "cache",
                    }
                ],
            )
        )

    # Peer-control story.
    peer_status = state.get("peer_control_status") or {}
    if peer_status.get("checked"):
        peers_passing = peer_status.get("taps_with_passing_peer") or []
        if peers_passing:
            sentences.append(
                (
                    "Peer-control evidence confirms that the tap landscape "
                    f"reaches this entity class (taps {', '.join(peers_passing)} "
                    "returned `confirms` on at least one peer hypothesis)",
                    [
                        {
                            "label": "peer hypotheses",
                            "href": f"#peer-controls-{finding.get('hypothesis_id','?')}",
                            "kind": "engine_rule",
                        }
                    ],
                )
            )

    return _new_paragraph(headline, sentences)


# ---------------- Narrative dissonance paragraph --------------------------


def _write_narrative_dissonance(finding: Dict[str, Any]) -> Dict[str, Any]:
    severity = finding.get("severity") or "info"
    company = finding.get("company_name") or finding.get("company") or "Issuer"
    metric_align = finding.get("metric_alignment") or {}
    snippet = finding.get("claim_excerpt") or ""
    prov = (finding.get("provenance") or {}).get("current") or {}

    headline = (
        f"{_SEVERITY_PREFIX.get(severity, '[NOTE]')} {company}: narrative-"
        f"numerical dissonance flagged."
    )

    sentences: List[Tuple[str, List[Dict[str, Any]]]] = []
    if snippet:
        sentences.append(
            (
                f"Issuer narrative excerpt: '{snippet[:200]}'",
                [
                    {
                        "label": "PDF snippet",
                        "href": prov.get("evidence_snippet")
                        or prov.get("pdf_url"),
                        "kind": "pdf",
                    }
                ],
            )
        )
    metric_pieces = [f"{k}={v}" for k, v in metric_align.items()]
    if metric_pieces:
        sentences.append(
            (
                f"Measured KPIs move against the narrative: "
                f"{'; '.join(metric_pieces)}",
                [{"label": "metric audit", "href": None, "kind": "calculation"}],
            )
        )
    sentences.append(
        (
            "Detector verdict: dissonance_detected; the positive language is "
            "not supported by the directional change in KPIs",
            [{"label": "narrative_dissonance detector", "href": None, "kind": "engine_rule"}],
        )
    )
    return _new_paragraph(headline, sentences)


# ---------------- Selective disclosure paragraph --------------------------


def _write_selective_disclosure(finding: Dict[str, Any]) -> Dict[str, Any]:
    severity = finding.get("severity") or "info"
    company = finding.get("company_name") or finding.get("company") or "Issuer"
    kpi_id = finding.get("kpi_id") or "?"
    verdict = finding.get("verdict") or "?"

    if verdict == "reclassification_introduced":
        action_word = (
            f"newly introduces the '{kpi_id}' framing absent from the "
            "prior-period report"
        )
    elif verdict == "disclosure_drop":
        action_word = (
            f"drops the previously-disclosed '{kpi_id}' KPI"
        )
    else:
        action_word = f"changes emphasis on the '{kpi_id}' KPI"

    headline = (
        f"{_SEVERITY_PREFIX.get(severity, '[NOTE]')} {company}: selective "
        f"disclosure ({verdict})."
    )

    sentences: List[Tuple[str, List[Dict[str, Any]]]] = [
        (
            f"Compared with the prior reporting period, the current report "
            f"{action_word}",
            [
                {
                    "label": "selective_disclosure detector",
                    "href": None,
                    "kind": "engine_rule",
                }
            ],
        )
    ]
    if finding.get("kpi_weight") is not None:
        sentences.append(
            (
                f"The KPI carries weight {finding['kpi_weight']:g} in the "
                "internal registry, so the change materially shifts the "
                "investor's KPI menu",
                [{"label": "configs/kpi_registry.json", "href": None, "kind": "calculation"}],
            )
        )
    return _new_paragraph(headline, sentences)


# ---------------- Lag causality paragraph ---------------------------------


def _write_lag_causality(finding: Dict[str, Any]) -> Dict[str, Any]:
    severity = finding.get("severity") or "info"
    company = finding.get("company_name") or finding.get("company") or "Issuer"
    headline = (
        f"{_SEVERITY_PREFIX.get(severity, '[NOTE]')} {company}: lag-causality "
        "break flagged."
    )
    sentences: List[Tuple[str, List[Dict[str, Any]]]] = [
        (
            finding.get("headline") or "Lag causality concern detected",
            [{"label": "lag_causality detector", "href": None, "kind": "engine_rule"}],
        )
    ]
    if finding.get("consolidation_caveat"):
        sentences.append(
            (
                f"Caveat: {finding['consolidation_caveat']}",
                [{"label": "consolidation caveat", "href": None, "kind": "engine_rule"}],
            )
        )
    return _new_paragraph(headline, sentences)


# ---------------- Generic fallback ----------------------------------------


def _write_generic(finding: Dict[str, Any]) -> Dict[str, Any]:
    severity = finding.get("severity") or "info"
    company = finding.get("company_name") or finding.get("company") or "Issuer"
    rid = finding.get("rule_id") or "unknown_rule"
    headline = (
        f"{_SEVERITY_PREFIX.get(severity, '[NOTE]')} {company}: {rid}."
    )
    sentences: List[Tuple[str, List[Dict[str, Any]]]] = [
        (
            finding.get("headline") or "Finding emitted with no structured body",
            [],
        )
    ]
    return _new_paragraph(headline, sentences)


# ---------------- Batch helper --------------------------------------------


def write_paragraphs_for_payload(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Build {composite_key: paragraph} for every finding in a leaderboard
    or dashboard payload."""
    out: Dict[str, Dict[str, Any]] = {}
    items = []
    items.extend(payload.get("top_findings") or [])
    for v in (payload.get("categories") or {}).values():
        items.extend(v)
    for f in items:
        key = f.get("composite_key") or (
            f"{f.get('company','?')}|{f.get('rule_id','?')}|"
            f"{f.get('hypothesis_id') or (f.get('headline') or '?')[:80]}"
        )
        if key not in out:
            out[key] = write_paragraph(f)
    return out
