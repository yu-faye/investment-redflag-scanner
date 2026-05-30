"""Sankey data builder for v10 Phase B.

Builds a single Sankey diagram dataset showing the global reasoning
landscape across every finding:

  issuer -> claim/rule -> falsification question -> tap (with verdict) -> severity

A single flow unit (value=1) is created per finding so the Sankey shows
*counts* of independent claims passing through each lane. For
triangulated findings, the flow may split across multiple questions and
taps -- in that case the original 1.0 unit is divided so the total
through any lane equals the number of findings that touch it.

Output schema
-------------
{
  "generated_at_utc": "...",
  "nodes": [
    {"id": "issuer:Qben Infra AB", "name": "Qben Infra AB", "layer": 0,
     "category": "issuer", "color": "#888"},
    ...
  ],
  "links": [
    {"source": "issuer:Qben Infra AB",
     "target": "claim:hyp_qben_q1_2026_revenue_pipeline",
     "value": 1.0, "color": "#f44", "verdict": "refutes",
     "finding_keys": ["qben_infra|triangulated_hypothesis|hyp_qben_..."]},
    ...
  ]
}

Both Plotly and D3-sankey consume this shape with trivial adapters: the
static dashboard JS converts `source`/`target` strings to node indices
before passing to D3-sankey.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple


# Colour palette by category -- ColourBrewer 'Set1'-ish for accessibility.
_CAT_COLORS = {
    "issuer": "#7f8c8d",
    "claim": "#3498db",
    "question": "#9b59b6",
    "evidence": "#1abc9c",
    "severity": "#34495e",
}

_VERDICT_COLORS = {
    "confirms": "#2ecc71",
    "partial": "#f1c40f",
    "refutes": "#e74c3c",
    "not_found": "#e67e22",
    "neutral": "#95a5a6",
    "error": "#7f0000",
}

_SEVERITY_COLORS = {
    "info": "#3498db",
    "warning": "#f39c12",
    "critical": "#c0392b",
}


def build_sankey(findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a Sankey dataset across all findings."""
    nodes: Dict[str, Dict[str, Any]] = {}
    links: List[Dict[str, Any]] = []

    def add_node(node_id: str, name: str, layer: int, category: str,
                 color: str | None = None) -> None:
        if node_id not in nodes:
            nodes[node_id] = {
                "id": node_id,
                "name": name,
                "layer": layer,
                "category": category,
                "color": color or _CAT_COLORS.get(category, "#888"),
            }

    def add_link(source: str, target: str, value: float,
                 verdict: str | None, finding_keys: List[str]) -> None:
        color = _VERDICT_COLORS.get(verdict or "", "#bdc3c7")
        links.append(
            {
                "source": source,
                "target": target,
                "value": value,
                "verdict": verdict,
                "color": color,
                "finding_keys": finding_keys,
            }
        )

    for f in findings:
        rule_id = f.get("rule_id") or "unknown"
        composite_key = f.get("composite_key") or (
            f"{f.get('company','?')}|{rule_id}|"
            f"{f.get('hypothesis_id') or (f.get('headline') or '?')[:80]}"
        )
        severity = f.get("severity") or "info"
        issuer_name = f.get("company_name") or f.get("company") or "?"
        issuer_id = f"issuer:{issuer_name}"

        add_node(issuer_id, issuer_name, 0, "issuer")
        sev_id = f"severity:{severity}"
        add_node(sev_id, severity.upper(), 4, "severity",
                 color=_SEVERITY_COLORS.get(severity, _CAT_COLORS["severity"]))

        if rule_id == "triangulated_hypothesis":
            _add_triangulated_paths(
                f, issuer_id, sev_id, composite_key, add_node, add_link
            )
        else:
            _add_simple_path(
                f, issuer_id, sev_id, composite_key, rule_id, add_node, add_link
            )

    # Aggregate parallel links (same source, target, verdict) into one.
    agg: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for ln in links:
        key = (ln["source"], ln["target"], ln.get("verdict") or "")
        if key in agg:
            agg[key]["value"] += ln["value"]
            agg[key]["finding_keys"].extend(ln["finding_keys"])
        else:
            agg[key] = dict(ln)
    final_links = [
        {
            **v,
            "finding_keys": sorted(set(v["finding_keys"])),
            "value": round(v["value"], 3),
        }
        for v in agg.values()
    ]

    # Final node order is a stable ordering: by layer then by name.
    final_nodes = sorted(
        nodes.values(), key=lambda n: (n["layer"], n["name"])
    )
    return {
        "nodes": final_nodes,
        "links": final_links,
        "layer_labels": ["Issuer", "Claim / Rule", "Falsification question",
                         "Tap evidence (verdict)", "Derived severity"],
        "verdict_palette": _VERDICT_COLORS,
        "severity_palette": _SEVERITY_COLORS,
        "category_palette": _CAT_COLORS,
    }


# -------------------------- Path builders ---------------------------------


def _add_triangulated_paths(
    finding: Dict[str, Any],
    issuer_id: str,
    sev_id: str,
    composite_key: str,
    add_node,
    add_link,
) -> None:
    state = finding.get("triangulation") or {}
    hyp_id = finding.get("hypothesis_id") or "?"
    entity = finding.get("entity") or "?"
    claim_node_id = f"claim:{hyp_id}"
    add_node(claim_node_id, f"{entity} | {hyp_id}", 1, "claim")

    latest = state.get("latest_per_tap") or {}
    # Map question -> list of tap entries addressing it.
    q_to_taps: Dict[str, List[Dict[str, Any]]] = {}
    for tap_id, entry in latest.items():
        for qid in entry.get("addresses_questions") or []:
            q_to_taps.setdefault(qid, []).append(entry)
    resolved = state.get("resolved_falsification_questions") or []
    if not resolved:
        # No taps addressed any question -- single link issuer -> claim -> severity.
        add_link(issuer_id, claim_node_id, 1.0, None, [composite_key])
        add_link(claim_node_id, sev_id, 1.0, None, [composite_key])
        return

    # Split the unit flow across questions.
    n_q = max(len(resolved), 1)
    per_q = 1.0 / n_q
    add_link(issuer_id, claim_node_id, 1.0, None, [composite_key])
    for qid in resolved:
        q_node = f"question:{qid}"
        add_node(q_node, qid, 2, "question")
        add_link(claim_node_id, q_node, per_q, None, [composite_key])
        taps = q_to_taps.get(qid, [])
        if not taps:
            add_link(q_node, sev_id, per_q, None, [composite_key])
            continue
        per_t = per_q / max(len(taps), 1)
        for entry in taps:
            tap_id = entry.get("tap_id") or "?"
            verdict = entry.get("verdict") or "neutral"
            evid_id = f"evidence:{tap_id}|{verdict}"
            add_node(evid_id, f"{tap_id} = {verdict}", 3, "evidence",
                     color=_VERDICT_COLORS.get(verdict, _CAT_COLORS["evidence"]))
            add_link(q_node, evid_id, per_t, verdict, [composite_key])
            add_link(evid_id, sev_id, per_t, verdict, [composite_key])


def _add_simple_path(
    finding: Dict[str, Any],
    issuer_id: str,
    sev_id: str,
    composite_key: str,
    rule_id: str,
    add_node,
    add_link,
) -> None:
    claim_id = f"claim:rule|{rule_id}"
    add_node(claim_id, rule_id, 1, "claim")
    add_link(issuer_id, claim_id, 1.0, None, [composite_key])
    # Synthetic "question" representing the detector's primary signal.
    sig_id = f"question:{rule_id}_signal"
    add_node(sig_id, f"{rule_id} signal", 2, "question")
    add_link(claim_id, sig_id, 1.0, None, [composite_key])
    # Synthetic evidence: the detector's verdict.
    verdict = finding.get("verdict") or "neutral"
    evid_id = f"evidence:{rule_id}|{verdict}"
    add_node(evid_id, f"{rule_id} = {verdict}", 3, "evidence",
             color=_VERDICT_COLORS.get(verdict, _CAT_COLORS["evidence"]))
    add_link(sig_id, evid_id, 1.0, verdict, [composite_key])
    add_link(evid_id, sev_id, 1.0, verdict, [composite_key])
