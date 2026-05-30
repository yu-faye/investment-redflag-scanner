"""Priority scorer: rank red flags by review-queue priority.

A deterministic ranking heuristic that combines severity, evidence magnitude,
and novelty (reclassifications, negative equity) into a single sortable score
and a bilingual headline. The score is *not* a statistical significance test;
it is a triage key so the most consequential findings surface first.

Per-rule scoring weights live in this file so they remain auditable.
The previous name for this score was `drama_score`; that label has been
retired in favour of the more analyst-neutral `priority_score`.
"""
from copy import deepcopy


_SEVERITY_WEIGHT = {"critical": 3.0, "warning": 1.5, "info": 0.5}


def _score_narrative(rf):
    mentions = rf.get("mention_count", 0) or 0
    align = abs(rf.get("alignment_score") or 0)
    sev = _SEVERITY_WEIGHT.get(rf.get("severity"), 0.5)
    return sev * (1 + min(mentions, 50) / 10.0) * (0.5 + align)


def _score_selective(rf):
    sev = _SEVERITY_WEIGHT.get(rf.get("severity"), 0.5)
    if rf.get("verdict") == "reclassification_introduced":
        return sev * 3.5
    emphasis = rf.get("previous_emphasis_score", 0) or 0
    weight = rf.get("weight", 0) or 0
    return sev * (1 + emphasis) * (0.5 + weight)


def _score_lag(rf):
    sev = _SEVERITY_WEIGHT.get(rf.get("severity"), 0.5)
    lag = rf.get("lag_quarters", 0) or 0
    min_lag = rf.get("min_lag_quarters_required", 1) or 1
    deficit = max(0, min_lag - lag)
    return sev * (1.0 + deficit)


def _score_external_collision(rf):
    """v8: external_collision priority. Boost when a specialist claim has
    zero confirmed public-record awards (the killer narrative), penalise
    when activity is consistent with the claim."""
    sev = _SEVERITY_WEIGHT.get(rf.get("severity"), 0.5)
    ee = (rf.get("external_evidence") or [{}])[0]
    confirmed = int(ee.get("confirmed_award_count") or 0)
    role = (rf.get("subsidiary_claimed_role") or "").lower()
    is_specialist = any(
        kw in role for kw in ["specialist", "established player", "key player", "leader"]
    )
    # Gap multiplier: 0 confirmed awards for a claimed specialist = max payoff.
    gap_boost = 0.0
    if is_specialist and confirmed == 0:
        gap_boost = 4.0
    elif confirmed == 0:
        gap_boost = 2.0
    elif confirmed <= 2:
        gap_boost = 1.0
    return round(sev * (1.0 + gap_boost), 2)


def _score_triangulated(rf):
    """v9: triangulated_hypothesis priority.

    Inputs from the TriangulationState attached to the finding under
    rf['triangulation']:
      - derived_severity (info / warning / critical)
      - refuting_taps + confirming_taps
      - peer_control_status.taps_with_passing_peer
      - blockers_for_critical

    Heuristic: base from severity (info 1.5, warning 5.0, critical 12.0),
    then small boosts for evidence completeness so a fully-triangulated
    warning ranks ahead of a barely-triangulated warning.
    """
    sev = rf.get("severity") or "info"
    base = {"info": 1.5, "warning": 5.0, "critical": 12.0}.get(sev, 1.0)
    state = rf.get("triangulation") or {}
    refuting = len(state.get("refuting_taps", []) or [])
    confirming = len(state.get("confirming_taps", []) or [])
    distinct = len(state.get("distinct_taps", []) or [])
    peer_ok = bool((state.get("peer_control_status") or {}).get("taps_with_passing_peer"))
    blockers = len(state.get("blockers_for_critical", []) or [])

    # Boosts: more taps that disagree with the claim ramp the warning up;
    # a passing peer control raises confidence in the signal; outstanding
    # blockers pull priority down (those findings need work before pitching).
    boost = 1.0
    if refuting >= 2:
        boost += 0.4
    elif refuting == 1:
        boost += 0.15
    if peer_ok:
        boost += 0.2
    if blockers:
        boost -= 0.15 * blockers
    boost = max(0.4, boost)
    return round(base * boost, 2)


def priority_score(rf):
    rid = rf.get("rule_id")
    if rid == "narrative_dissonance":
        return round(_score_narrative(rf), 2)
    if rid == "selective_disclosure":
        return round(_score_selective(rf), 2)
    if rid == "lag_causality":
        return round(_score_lag(rf), 2)
    if rid == "external_collision":
        return _score_external_collision(rf)
    if rid == "triangulated_hypothesis":
        return _score_triangulated(rf)
    return 0.5


def _short_metric_summary(rf):
    out = []
    for m in rf.get("metric_alignment", []) or []:
        if m.get("value") is None:
            continue
        out.append(f"{m['metric']}={m['value']}")
    return ", ".join(out) or "n/a"


def make_headline(company_name, rf):
    """Return a plain English headline string.

    NB: the bilingual (en + zh) headline tuple was removed in v7 -- the project
    is now English-only across detectors and dashboards."""
    rid = rf.get("rule_id")
    if rid == "narrative_dissonance":
        family = rf.get("family")
        mc = rf.get("mention_count")
        metrics = _short_metric_summary(rf)
        return (
            f"{company_name}: '{family}' narrative cited {mc} times, but "
            f"supporting metrics move the opposite way ({metrics})."
        )
    if rid == "selective_disclosure":
        kpi = rf.get("kpi_id") or ""
        if rf.get("verdict") == "reclassification_introduced":
            return (
                f"{company_name}: new '{kpi.replace('reclassification:', '')}' "
                f"framing introduced in {rf.get('current_period')}, absent in "
                f"{rf.get('previous_period')}."
            )
        return (
            f"{company_name}: KPI '{kpi}' disclosed in {rf.get('previous_period')} "
            f"disappeared in {rf.get('current_period')}."
        )
    if rid == "lag_causality":
        ctype = rf.get("claim_type")
        lag = rf.get("lag_quarters")
        if rf.get("consolidation_caveat"):
            return (
                f"{company_name}: '{ctype}' impact attributed to current period "
                f"appears mechanical (IFRS consolidation language detected); "
                f"lag={lag}Q is accounting, not synergy."
            )
        return (
            f"{company_name}: claim of '{ctype}' credited to current-period "
            f"results with implausible {lag}-quarter transmission lag."
        )
    # Detectors that emit their own headline (e.g. v8 external_collision)
    # win over the generic per-rule template.
    if rf.get("headline"):
        return rf["headline"]
    return f"{company_name}: red flag of type {rid}."


def enrich_with_priority(red_flags, company_name):
    out = []
    for rf in red_flags:
        copy = deepcopy(rf)
        copy["priority_score"] = priority_score(rf)
        copy["headline"] = make_headline(company_name, rf)
        out.append(copy)
    return out


def top_headlines(all_findings, k=10):
    """all_findings is list of dicts each containing company_name + finding fields."""
    ranked = sorted(all_findings, key=lambda x: x.get("priority_score", 0), reverse=True)
    return ranked[:k]
