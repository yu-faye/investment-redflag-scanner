"""Argument-tree builder for v10 logic-chain visualization.

Given a finding (triangulated_hypothesis or any v6-v8 detector finding),
build a nested dict representing the reasoning chain from claim down to
source-anchored evidence. The static dashboard renders this with nested
`<details>` elements and the Streamlit app renders it with nested
`st.expander` calls. Both share the same data shape so the two ends stay
in lockstep -- one of the Phase B acceptance criteria.

Node shape
----------
Every node has:
  id          str   -- stable, deterministic id for HTML anchor + state
  label       str   -- short display label (becomes <summary> in HTML)
  kind        str   -- one of NODE_KINDS (drives icon + color)
  verdict     str|None    -- for evidence / question nodes
  severity    str|None    -- for the root claim node
  glyph       str   -- single-char icon for the dashboards
  detail      str   -- expanded body text (rendered after <summary>)
  metadata    dict  -- structured key-value pairs (rendered as <dl>)
  links       list  -- [{label, href}] external/internal click targets
  children    list  -- nested nodes

We never store HTML or markup in the data -- only data. Rendering is the
dashboard's responsibility.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional


NODE_KINDS = (
    "claim",          # root: the hypothesis or detector claim
    "engine_rule",    # severity-derivation rule note
    "question",       # falsification question
    "evidence",       # one tap's verdict on that question
    "source",         # raw artifact (PDF page, cache JSON file, URL)
    "calculation",    # derived calculation (e.g. external_support_pct)
    "summary",        # top-line one-liner
)

VERDICT_GLYPH = {
    "confirms": "✓",
    "partial": "≈",
    "refutes": "✗",
    "not_found": "?",
    "neutral": "·",
    "error": "!",
}

SEVERITY_GLYPH = {
    "info": "ⓘ",
    "warning": "⚠",
    "critical": "‼",
}


def _node(
    *,
    id_seed: str,
    label: str,
    kind: str,
    verdict: Optional[str] = None,
    severity: Optional[str] = None,
    detail: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    links: Optional[List[Dict[str, str]]] = None,
    children: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if kind not in NODE_KINDS:
        raise ValueError(f"kind must be one of {NODE_KINDS}, got {kind!r}")
    glyph = ""
    if verdict:
        glyph = VERDICT_GLYPH.get(verdict, "·")
    elif severity:
        glyph = SEVERITY_GLYPH.get(severity, "ⓘ")
    return {
        "id": _stable_id(id_seed),
        "label": label,
        "kind": kind,
        "verdict": verdict,
        "severity": severity,
        "glyph": glyph,
        "detail": detail,
        "metadata": metadata or {},
        "links": links or [],
        "children": children or [],
    }


def _stable_id(seed: str) -> str:
    h = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
    return f"argnode-{h}"


# --------------------------- Public API -----------------------------------


def build_argument_tree(finding: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Dispatch on finding shape and return the root tree node (or None
    when the finding lacks enough structure to build a tree)."""
    rule_id = finding.get("rule_id")
    if rule_id == "triangulated_hypothesis":
        return _build_for_triangulated(finding)
    if rule_id == "narrative_dissonance":
        return _build_for_narrative_dissonance(finding)
    if rule_id == "selective_disclosure":
        return _build_for_selective_disclosure(finding)
    if rule_id == "lag_causality":
        return _build_for_lag_causality(finding)
    return _build_generic_tree(finding)


# ---------------------- Triangulated hypothesis ---------------------------


def _build_for_triangulated(finding: Dict[str, Any]) -> Dict[str, Any]:
    state = finding.get("triangulation") or {}
    hyp_id = finding.get("hypothesis_id") or "unknown_hypothesis"
    entity = finding.get("entity") or "?"
    claim = finding.get("claim") or finding.get("headline") or ""
    severity = state.get("derived_severity") or finding.get("severity")
    claim_category = state.get("claim_category") or finding.get("subtype")

    latest_per_tap: Dict[str, Dict[str, Any]] = state.get("latest_per_tap") or {}
    resolved = state.get("resolved_falsification_questions") or []
    pending = state.get("pending_falsification_questions") or []
    blockers = state.get("blockers_for_critical") or []
    category_rule_notes = state.get("category_rule_notes") or []

    # Map question_id -> list of evidence rows that address it.
    question_to_taps: Dict[str, List[Dict[str, Any]]] = {}
    for tap_id, entry in latest_per_tap.items():
        for qid in entry.get("addresses_questions") or []:
            question_to_taps.setdefault(qid, []).append(entry)

    children: List[Dict[str, Any]] = []

    # 1) Summary node (top-line one-liner).
    summary_text = _triangulation_summary(state)
    children.append(
        _node(
            id_seed=f"{hyp_id}/summary",
            label="Summary verdict landscape",
            kind="summary",
            detail=summary_text,
            metadata={
                "claim_category": claim_category,
                "derived_severity": severity,
                "distinct_taps": state.get("distinct_taps", []),
                "verdict_summary": state.get("verdict_summary", {}),
            },
        )
    )

    # 2) One sub-tree per falsification question (resolved + pending).
    question_nodes: List[Dict[str, Any]] = []
    for qid in resolved:
        q_evidence_nodes = [
            _build_evidence_node(hyp_id, qid, entry)
            for entry in question_to_taps.get(qid, [])
        ]
        question_nodes.append(
            _node(
                id_seed=f"{hyp_id}/q/{qid}",
                label=f"Q: {qid}",
                kind="question",
                verdict=_aggregate_q_verdict(question_to_taps.get(qid, [])),
                detail=f"Falsification question addressed by "
                f"{len(question_to_taps.get(qid, []))} tap(s).",
                children=q_evidence_nodes,
            )
        )
    for pq in pending:
        children_pq: List[Dict[str, Any]] = []
        question_nodes.append(
            _node(
                id_seed=f"{hyp_id}/q/{pq['question_id']}",
                label=f"Q (pending): {pq['question_id']}",
                kind="question",
                verdict=None,
                detail=(pq.get("text") or "") + " | This question has no tap "
                "evidence yet; the audit roadmap may recommend a tap.",
                metadata={
                    "blocking_for_critical": pq.get("blocking_for_critical"),
                    "relevant_tap_kinds": pq.get("relevant_tap_kinds"),
                },
            )
        )
    children.extend(question_nodes)

    # 3) Engine rule notes -- the category-specific R7 rationale, etc.
    if category_rule_notes:
        rule_children = []
        for i, note in enumerate(category_rule_notes):
            rule_children.append(
                _node(
                    id_seed=f"{hyp_id}/engine_rule/{i}",
                    label=f"Rule #{i + 1}",
                    kind="engine_rule",
                    detail=note,
                )
            )
        children.append(
            _node(
                id_seed=f"{hyp_id}/engine_rules",
                label="Engine derivation rules applied",
                kind="engine_rule",
                detail=(
                    "The TriangulationEngine applied the following category-"
                    "specific rules to arrive at the final severity."
                ),
                children=rule_children,
            )
        )

    # 4) Blockers for critical (what would graduate this) -- useful even
    # when severity is already critical, because it explains why other
    # rules did NOT degrade it.
    if blockers:
        b_children = [
            _node(
                id_seed=f"{hyp_id}/blocker/{i}",
                label=b[:120],
                kind="engine_rule",
                detail=b,
            )
            for i, b in enumerate(blockers)
        ]
        children.append(
            _node(
                id_seed=f"{hyp_id}/blockers",
                label="Blockers for critical / engine guardrails fired",
                kind="engine_rule",
                detail=(
                    "Generic engine guardrails that capped or qualified the "
                    "severity. Resolve these to graduate the hypothesis."
                ),
                children=b_children,
            )
        )

    # 5) Root claim node, severity glyph already attached.
    return _node(
        id_seed=f"{hyp_id}/root",
        label=f"{entity}: {claim[:140]}",
        kind="claim",
        severity=severity,
        detail=claim,
        metadata={
            "hypothesis_id": hyp_id,
            "entity": entity,
            "claim_category": claim_category,
            "total_ledger_entries": state.get("total_ledger_entries"),
        },
        children=children,
    )


def _triangulation_summary(state: Dict[str, Any]) -> str:
    parts = []
    for tap_id, entry in (state.get("latest_per_tap") or {}).items():
        parts.append(f"{tap_id}={entry.get('verdict')}")
    sev = state.get("derived_severity")
    return f"Severity: {sev}. Taps reporting: " + ", ".join(parts) if parts \
        else f"Severity: {sev}. No taps have reported."


def _aggregate_q_verdict(entries: List[Dict[str, Any]]) -> Optional[str]:
    """Pick a single 'worst case' verdict for a question, used as the
    question node's glyph."""
    if not entries:
        return None
    # Order: refutes > not_found > partial > neutral > confirms (worst-first).
    severity_order = ["refutes", "not_found", "partial", "neutral", "confirms"]
    verdicts = [e.get("verdict") for e in entries]
    for v in severity_order:
        if v in verdicts:
            return v
    return verdicts[0]


def _build_evidence_node(
    hyp_id: str, qid: str, entry: Dict[str, Any]
) -> Dict[str, Any]:
    tap_id = entry.get("tap_id") or "?"
    verdict = entry.get("verdict") or "?"
    narrative = entry.get("narrative") or ""
    summary = entry.get("payload_summary") or {}
    raw_ref = entry.get("raw_payload_ref")
    query_url = entry.get("query_url")
    confidence = entry.get("confidence")

    links: List[Dict[str, str]] = []
    if raw_ref:
        links.append({"label": f"raw payload: {raw_ref}", "href": raw_ref})
    if query_url:
        links.append({"label": f"upstream query: {query_url}", "href": query_url})

    # If the tap exposes confirmed_awards in payload_summary, surface each
    # award as a child source node so the user can click through.
    source_children: List[Dict[str, Any]] = []
    for i, award in enumerate((summary or {}).get("confirmed_awards") or []):
        url = award.get("public_url")
        label = (
            f"{award.get('notice_id', '?')} | "
            f"{award.get('publication_date', '?')} | "
            f"{(award.get('heading') or '')[:80]}"
        )
        source_children.append(
            _node(
                id_seed=f"{hyp_id}/{qid}/{tap_id}/award/{i}",
                label=label,
                kind="source",
                metadata={
                    "buyer_names": award.get("buyer_names"),
                    "winner_names": award.get("winner_names")
                    or award.get("awarded_names"),
                },
                links=[{"label": "Open notice", "href": url}] if url else [],
            )
        )

    return _node(
        id_seed=f"{hyp_id}/{qid}/{tap_id}",
        label=f"{tap_id}: {verdict}"
        + (f" (confidence {confidence:.2f})" if isinstance(confidence, (int, float)) else ""),
        kind="evidence",
        verdict=verdict,
        detail=narrative,
        metadata={
            "tap_kind": entry.get("tap_kind"),
            "addresses_questions": entry.get("addresses_questions"),
            "payload_sha256": entry.get("payload_sha256"),
            "gathered_at_utc": entry.get("gathered_at_utc"),
            "payload_summary": summary,
        },
        links=links,
        children=source_children,
    )


# ---------------------- Narrative dissonance ------------------------------


def _build_for_narrative_dissonance(finding: Dict[str, Any]) -> Dict[str, Any]:
    severity = finding.get("severity")
    company = finding.get("company_name") or finding.get("company") or "?"
    headline = finding.get("headline") or ""
    metric_align = finding.get("metric_alignment") or {}
    snippet = finding.get("claim_excerpt") or ""
    prov = (finding.get("provenance") or {}).get("current") or {}

    children: List[Dict[str, Any]] = []

    # Narrative-side: the claim sentence found in the PDF.
    if snippet:
        links = []
        if prov.get("evidence_snippet"):
            links.append(
                {"label": "Snippet image", "href": prov["evidence_snippet"]}
            )
        if prov.get("pdf_url"):
            links.append({"label": "Open PDF", "href": prov["pdf_url"]})
        children.append(
            _node(
                id_seed=f"{company}/narrative/sentence",
                label="Narrative claim excerpt",
                kind="source",
                detail=snippet,
                metadata={
                    "pdf": prov.get("local_pdf"),
                    "matched_term": finding.get("claim_excerpt_matched_term"),
                },
                links=links,
            )
        )

    # Metric-side: KPIs that contradict the narrative.
    metric_children = []
    for metric_id, val in metric_align.items():
        metric_children.append(
            _node(
                id_seed=f"{company}/metric/{metric_id}",
                label=f"{metric_id} = {val}",
                kind="calculation",
                detail=f"Metric value {val} contradicts the positive narrative.",
            )
        )
    if metric_children:
        children.append(
            _node(
                id_seed=f"{company}/metric_block",
                label="Contradicting metrics",
                kind="calculation",
                detail=(
                    "These metrics move in the opposite direction to the "
                    "narrative claim."
                ),
                children=metric_children,
            )
        )

    return _node(
        id_seed=f"{company}/narrative_dissonance",
        label=f"{company}: narrative_dissonance",
        kind="claim",
        severity=severity,
        detail=headline,
        metadata={
            "rule_id": "narrative_dissonance",
            "priority_score": finding.get("priority_score"),
        },
        children=children,
    )


# ---------------------- Selective disclosure ------------------------------


def _build_for_selective_disclosure(finding: Dict[str, Any]) -> Dict[str, Any]:
    severity = finding.get("severity")
    company = finding.get("company_name") or finding.get("company") or "?"
    headline = finding.get("headline") or ""
    kpi_id = finding.get("kpi_id") or "?"

    children: List[Dict[str, Any]] = [
        _node(
            id_seed=f"{company}/sd/{kpi_id}/kpi",
            label=f"KPI: {kpi_id}",
            kind="calculation",
            detail=(
                f"Verdict: {finding.get('verdict')}. KPI emphasis changed "
                f"between prior and current reports."
            ),
            metadata={
                "verdict": finding.get("verdict"),
                "weight": finding.get("kpi_weight"),
                "prior_emphasis": finding.get("prior_emphasis"),
                "current_emphasis": finding.get("current_emphasis"),
            },
        )
    ]

    return _node(
        id_seed=f"{company}/sd/{kpi_id}",
        label=f"{company}: selective_disclosure ({kpi_id})",
        kind="claim",
        severity=severity,
        detail=headline,
        metadata={"rule_id": "selective_disclosure"},
        children=children,
    )


# ---------------------- Lag causality -------------------------------------


def _build_for_lag_causality(finding: Dict[str, Any]) -> Dict[str, Any]:
    severity = finding.get("severity")
    company = finding.get("company_name") or finding.get("company") or "?"
    headline = finding.get("headline") or ""

    return _node(
        id_seed=f"{company}/lag_causality",
        label=f"{company}: lag_causality",
        kind="claim",
        severity=severity,
        detail=headline,
        metadata={
            "rule_id": "lag_causality",
            "lag_quarters": finding.get("lag_quarters"),
            "consolidation_caveat": finding.get("consolidation_caveat"),
        },
    )


# ---------------------- Generic fallback ----------------------------------


def _build_generic_tree(finding: Dict[str, Any]) -> Dict[str, Any]:
    severity = finding.get("severity")
    company = finding.get("company_name") or finding.get("company") or "?"
    rid = finding.get("rule_id") or "unknown"
    headline = finding.get("headline") or ""
    return _node(
        id_seed=f"{company}/{rid}",
        label=f"{company}: {rid}",
        kind="claim",
        severity=severity,
        detail=headline,
        metadata={"rule_id": rid},
    )


# --------------------------- Batch helpers --------------------------------


def build_argument_trees_for_payload(
    leaderboard_or_dashboard_payload: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Build argument trees for every finding in a leaderboard or
    dashboard payload. Returns {node_id_of_root: root_node_dict}."""
    out: Dict[str, Dict[str, Any]] = {}
    candidates = []
    candidates.extend(leaderboard_or_dashboard_payload.get("top_findings") or [])
    for items in (leaderboard_or_dashboard_payload.get("categories") or {}).values():
        candidates.extend(items)
    for f in candidates:
        tree = build_argument_tree(f)
        if tree:
            out.setdefault(tree["id"], tree)
    return out
