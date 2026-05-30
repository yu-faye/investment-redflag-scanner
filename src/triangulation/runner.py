"""Hypothesis runner: glue between hypotheses.json, taps, ledger, engine.

Given a list of hypothesis dicts and a list of EvidenceTap instances,
run every applicable (hypothesis, tap) pair, append rows to the ledger,
then derive triangulation states for all hypotheses (two-pass so peer
controls work). Return both the derived states and a translated list of
v9 findings ready to slot into the existing dashboard payloads.

This is the only place that knows about all four (hypotheses, taps,
ledger, engine). The orchestrator calls into here once per run.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.ingest.external.base import EvidenceTap
from src.triangulation.engine import TriangulationEngine, derive_all
from src.triangulation.ledger import LedgerStore


# ----- ledger driver -------------------------------------------------------


def run_taps_for_hypotheses(
    hypotheses: Sequence[Dict[str, Any]],
    taps: Sequence[EvidenceTap],
    ledger_store: LedgerStore,
    *,
    external_cache_root: Path,
    company_id_field: str = "source_company",
    skip_if_ledger_fresh: bool = False,
) -> Dict[str, List[str]]:
    """For each hypothesis, find every applicable tap and call gather().
    Each EvidenceEntry is appended to the hypothesis's ledger.

    Returns a per-hypothesis log of which taps fired (for orchestrator
    print output).

    If `skip_if_ledger_fresh` is True, a (hypothesis_id, tap_id) pair is
    skipped when the ledger already has an entry from that tap dated
    within ~12 hours -- useful for fast re-runs that should not re-hit
    APIs.
    """
    log: Dict[str, List[str]] = {}
    for hyp in hypotheses:
        hyp_id = hyp["id"]
        log[hyp_id] = []
        company_id = hyp.get(company_id_field) or "unknown_company"
        for tap in taps:
            addressed = tap.can_address(hyp)
            if not addressed:
                continue
            if skip_if_ledger_fresh and _ledger_has_fresh_entry(
                ledger_store, hyp_id, tap.tap_id
            ):
                log[hyp_id].append(f"{tap.tap_id}=cached")
                continue
            tap_cache_dir = external_cache_root / tap.tap_id / company_id
            tap_cache_dir.mkdir(parents=True, exist_ok=True)
            entry = tap.gather(
                hyp, cache_dir=tap_cache_dir, addressed_question_ids=addressed
            )
            ledger_store.append(entry)
            log[hyp_id].append(f"{tap.tap_id}={entry.get('verdict')}")
    return log


def _ledger_has_fresh_entry(
    ledger_store: LedgerStore, hyp_id: str, tap_id: str, max_age_hours: int = 12
) -> bool:
    from datetime import datetime, timezone, timedelta

    rows = ledger_store.read(hyp_id)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    for r in rows:
        if r.get("tap_id") != tap_id:
            continue
        ts = r.get("gathered_at_utc")
        try:
            t = datetime.fromisoformat(ts)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t >= cutoff:
                return True
        except (TypeError, ValueError):
            continue
    return False


# ----- v9 -> v8 finding translation ---------------------------------------


def state_to_finding(
    hypothesis: Dict[str, Any], state: Dict[str, Any]
) -> Dict[str, Any]:
    """Translate a TriangulationState back into a finding dict shape the
    existing v6-v8 pipeline knows how to display (priority_score,
    headline, claim_excerpt, evidence_snippet via the PDF locator, etc.)
    plus the new `triangulation` block for the v9 dashboard panels.

    The headline includes a short verdict landscape so the leaderboard
    line is self-explanatory.
    """
    sev_to_priority = {"info": 1.0, "warning": 5.0, "critical": 10.0}
    derived = state["derived_severity"]
    entity = hypothesis.get("entity", "?")
    company_name = hypothesis.get("source_company_name") or hypothesis.get(
        "source_company"
    )

    # Compose a one-line landscape for the headline.
    summary_parts = []
    for tap_id, entry in (state.get("latest_per_tap") or {}).items():
        summary_parts.append(f"{tap_id}={entry.get('verdict')}")
    landscape = ", ".join(summary_parts) or "no taps fired"

    headline = (
        f"{company_name}: claim '{hypothesis.get('claim')}' -- triangulation = "
        f"{landscape}; derived severity = {derived}."
    )

    return {
        "rule_id": "triangulated_hypothesis",
        "subtype": hypothesis.get("claim_category"),
        "company": hypothesis.get("source_company"),
        "company_name": company_name,
        "hypothesis_id": hypothesis["id"],
        "entity": entity,
        "claim": hypothesis.get("claim"),
        "severity": derived,
        "priority_score": sev_to_priority.get(derived, 1.0),
        "headline": headline,
        # Keep the PDF claim_excerpt field name so the v6 evidence_snippet
        # pipeline still cuts a PNG and the dashboards still highlight
        # the right sentence.
        "claim_excerpt": (
            hypothesis.get("source_pdf_anchor", {}).get("narrative_anchor")
        ),
        "claim_excerpt_matched_term": (
            hypothesis.get("source_pdf_anchor", {}).get("narrative_anchor")
        ),
        # Full v9 triangulation block -- rendered by the new dashboard panels.
        "triangulation": state,
    }


def build_triangulation_matrix(
    hypotheses: Sequence[Dict[str, Any]],
    states: Dict[str, Dict[str, Any]],
    known_tap_kinds: Sequence[str] = (
        "public_procurement",
        "company_registry",
        "financial_filings",
        "insider_trading",
        "subcontractor_directory",
        "employee_signal",
        "media",
        "derived_analysis",
    ),
) -> Dict[str, Any]:
    """Build a dashboard-renderable matrix: rows = hypotheses,
    cols = tap_kinds, cells = {verdict, tap_id, confidence}."""
    rows = []
    for hyp in hypotheses:
        state = states.get(hyp["id"], {}) or {}
        latest = state.get("latest_per_tap") or {}
        # cells_by_kind keeps the legacy single-cell-per-kind shape so
        # existing dashboard.js renders without modification. cells_by_tap
        # carries every tap individually so no data is lost when multiple
        # taps share one tap_kind (e.g. doffin + ted are both
        # public_procurement; derived_revenue_support + derived_explanatory
        # _slippage are both derived_analysis).
        per_kind: Dict[str, Optional[Dict[str, Any]]] = {k: None for k in known_tap_kinds}
        per_kind_all: Dict[str, List[Dict[str, Any]]] = {k: [] for k in known_tap_kinds}
        per_tap: Dict[str, Dict[str, Any]] = {}
        for tap_id, entry in latest.items():
            kind = entry.get("tap_kind")
            cell = {
                "tap_id": tap_id,
                "tap_kind": kind,
                "verdict": entry.get("verdict"),
                "confidence": entry.get("confidence"),
                "narrative": entry.get("narrative"),
                "addresses_questions": entry.get("addresses_questions"),
                "raw_payload_ref": entry.get("raw_payload_ref"),
                "payload_summary": entry.get("payload_summary"),
            }
            per_tap[tap_id] = cell
            if kind in per_kind:
                # Keep the first hit for legacy single-cell display.
                if per_kind[kind] is None:
                    per_kind[kind] = cell
                per_kind_all[kind].append(cell)
        rows.append(
            {
                "hypothesis_id": hyp["id"],
                "claim_category": hyp.get("claim_category"),
                "entity": hyp.get("entity"),
                "source_company": hyp.get("source_company"),
                "claim": hyp.get("claim"),
                "derived_severity": state.get("derived_severity"),
                "cells_by_kind": per_kind,
                "cells_by_kind_all": per_kind_all,
                "cells_by_tap": per_tap,
                "next_recommended_taps": state.get("next_recommended_taps", []),
                "blockers_for_critical": state.get("blockers_for_critical", []),
                "peer_control_status": state.get("peer_control_status", {}),
                "category_rule_notes": state.get("category_rule_notes", []),
            }
        )
    return {
        "tap_kinds": list(known_tap_kinds),
        "rows": rows,
    }


def build_audit_roadmap(
    states: Dict[str, Dict[str, Any]],
    hypotheses: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Aggregate `next_recommended_taps` across all hypotheses, ranked
    by total expected information gain."""
    hyp_by_id = {h["id"]: h for h in hypotheses}
    bucket: Dict[str, Dict[str, Any]] = {}
    for hyp_id, st in states.items():
        for rec in st.get("next_recommended_taps", []) or []:
            kind = rec["tap_kind"]
            b = bucket.setdefault(
                kind,
                {
                    "tap_kind": kind,
                    "covers_hypotheses": [],
                    "total_information_gain": 0.0,
                    "would_unblock_critical_for": [],
                },
            )
            b["covers_hypotheses"].append(
                {
                    "hypothesis_id": hyp_id,
                    "entity": hyp_by_id.get(hyp_id, {}).get("entity"),
                    "question_id": rec["addresses_question_id"],
                    "blocking_for_critical": rec["blocking_for_critical"],
                }
            )
            b["total_information_gain"] += rec["expected_information_gain"]
            if rec["blocking_for_critical"]:
                b["would_unblock_critical_for"].append(hyp_id)
    out = sorted(
        bucket.values(),
        key=lambda r: r["total_information_gain"],
        reverse=True,
    )
    return out
