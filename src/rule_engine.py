def _severity_from_bounds(value, lower_warning=None, lower_critical=None, upper_warning=None, upper_critical=None):
    if lower_critical is not None and value < lower_critical:
        return "critical"
    if lower_warning is not None and value < lower_warning:
        return "warning"
    if upper_critical is not None and value > upper_critical:
        return "critical"
    if upper_warning is not None and value > upper_warning:
        return "warning"
    return "ok"


def run_rules(financial_inputs):
    """
    Simple, explainable checks on a financial inputs dict.
    Returns list of rule results for downstream reporting.
    """
    checks = []

    cash_conv = financial_inputs.get("cash_conversion_ratio", 1.0)
    severity = _severity_from_bounds(cash_conv, lower_warning=0.85, lower_critical=0.70)
    checks.append({
        "rule_id": "cash_conversion_gap",
        "value": cash_conv,
        "severity": severity,
        "message": "Cash conversion below expected quality band." if severity != "ok" else "Cash conversion healthy."
    })

    margin_delta = financial_inputs.get("gross_margin_yoy_change_pct", 0.0)
    severity = _severity_from_bounds(margin_delta, lower_warning=-1.5, lower_critical=-3.0)
    checks.append({
        "rule_id": "margin_narrative_dissonance",
        "value": margin_delta,
        "severity": severity,
        "message": "Margin deterioration conflicts with optimistic efficiency narrative." if severity != "ok" else "Margin trend aligned."
    })

    related_party_share = financial_inputs.get("related_party_revenue_share_pct", 0.0)
    severity = _severity_from_bounds(related_party_share, upper_warning=8.0, upper_critical=15.0)
    checks.append({
        "rule_id": "related_party_dependency",
        "value": related_party_share,
        "severity": severity,
        "message": "Related-party concentration requires deeper transparency checks." if severity != "ok" else "Related-party share within normal range."
    })

    interest_coverage = financial_inputs.get("interest_coverage_ratio", 99.0)
    severity = _severity_from_bounds(interest_coverage, lower_warning=3.0, lower_critical=2.0)
    checks.append({
        "rule_id": "debt_refinancing_pressure",
        "value": interest_coverage,
        "severity": severity,
        "message": "Debt service headroom is tightening." if severity != "ok" else "Debt service capacity acceptable."
    })

    inventory_gap = financial_inputs.get("inventory_growth_minus_revenue_growth_pct", 0.0)
    severity = _severity_from_bounds(inventory_gap, upper_warning=8.0, upper_critical=15.0)
    checks.append({
        "rule_id": "inventory_build_up_risk",
        "value": inventory_gap,
        "severity": severity,
        "message": "Inventory builds faster than demand signal." if severity != "ok" else "Inventory trend appears balanced."
    })

    return checks
