"""Detector: Time-lag causality break.

Hypothesis: Management claims an action (acquisition, cost program, launch)
and asserts results. If reported metric change appears earlier than the
business-realistic transmission lag for that claim type, flag a causality break.

This detector is purposely conservative: it only flags when we can find
BOTH a claim with a period anchor AND a metric movement with a different
period anchor in the same document or a sibling document.

v0.2:
  - IFRS / accounting consolidation guardrail. Sentences that combine a claim
    (acquisition_completed, divestment_initiated) with an IFRS-style mechanical
    consolidation phrase ("fully consolidated as of", "from the acquisition date",
    "business combination", "purchase price allocation", "IFRS 3", etc.) are not
    treated as a real causal claim about *operating performance*; severity is
    downgraded to "info" and tagged with `consolidation_caveat=True`, because the
    reported revenue/EBITDA bump in that period is the mechanical inclusion of the
    target company on the consolidated income statement, not a synergy.
"""
import json
import os
import re

CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "configs", "lag_rules.json"
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12
}
_QUARTER_OF_MONTH = {m: ((idx - 1) // 3) + 1 for m, idx in _MONTHS.items()}


_CONSOLIDATION_GUARDRAIL = re.compile(
    r"(?i)("
    r"\bfully consolidated (?:as of|from)\b|"
    r"\bfirst[- ]time consolidation\b|"
    r"\bconsolidated (?:into|from|as of|with effect from)\b|"
    r"\bconsolidation of\s+\w+\s+(?:from|as of|with effect from)\b|"
    r"\bconsolidation of\s+\w+\s+(?:from\s+(?:late|early|the\s+end\s+of)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december))\b|"
    r"\bfrom the (?:acquisition|effective) date\b|"
    r"\bbusiness combination\b|"
    r"\bpurchase price allocation\b|"
    r"\bPPA\b|"
    r"\bIFRS\s*3\b|"
    r"\bincluded in the consolidated (?:income statement|financial statements)\b|"
    r"\bacquisition accounting\b|"
    r"\bgoodwill arising on (?:the )?acquisition\b"
    r")"
)


def _load_rules():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_period(token):
    """Return (year, quarter) tuple if parsable, else None."""
    if not token:
        return None
    token_lower = token.lower().strip()
    m = re.match(r"q([1-4])\s*(20\d{2})", token_lower)
    if m:
        return int(m.group(2)), int(m.group(1))
    parts = token_lower.split()
    if len(parts) == 2 and parts[0] in _MONTHS:
        try:
            year = int(parts[1])
            return year, _QUARTER_OF_MONTH[parts[0]]
        except ValueError:
            return None
    if token_lower in {"first quarter", "second quarter", "third quarter", "fourth quarter"}:
        idx = ["first", "second", "third", "fourth"].index(token_lower.split()[0]) + 1
        return None, idx
    return None


def _quarter_distance(p1, p2):
    if p1 is None or p2 is None:
        return None
    y1, q1 = p1
    y2, q2 = p2
    if y1 is None or y2 is None:
        return None
    return (y2 - y1) * 4 + (q2 - q1)


_CLAIM_TYPE_PATTERNS = [
    ("acquisition_completed", re.compile(
        r"(?i)acquired|acquisition (?:of|was|completed)|merged with|merger between|"
        r"consolidat(?:ion of|ed into|ing)|integration of|integrated into|"
        r"completed the (?:acquisition|merger)|completion of the (?:acquisition|merger)"
    )),
    ("listing_completed", re.compile(r"(?i)listed (?:on|its shares)|listing on Nasdaq|listing on Euronext|stock market listing")),
    ("cost_program_launched", re.compile(r"(?i)cost (?:program|reduction|saving|cutting)|efficiency program|restructuring program|optimi[sz]ation program")),
    ("divestment_initiated", re.compile(r"(?i)divest(?:ed|ment|ing)|held for sale|disposal of|sold .* subsidiary|sale of .* business")),
    ("product_launched", re.compile(r"(?i)launched (?:the |a |our |its )?(?:new |next-generation )?product|launched our new")),
    ("bond_issued", re.compile(r"(?i)issued (?:senior |subordinated )?(?:unsecured )?bonds?|bond issuance|note offering|raised .* bond")),
]

_CLAIM_TYPES_SUBJECT_TO_CONSOLIDATION = {"acquisition_completed", "divestment_initiated"}


def _classify_claim(sentence):
    for ctype, pattern in _CLAIM_TYPE_PATTERNS:
        if pattern.search(sentence):
            return ctype
    return None


def _extract_period_anchor(sentence, period_regex):
    m = re.search(period_regex, sentence)
    if not m:
        return None, None
    raw = m.group(0)
    parsed = _parse_period(raw)
    return raw, parsed


def _is_consolidation_mechanical(sentence):
    return bool(_CONSOLIDATION_GUARDRAIL.search(sentence))


def detect_lag_breaks(report_text, report_period_label):
    """Return list of causality candidates with explicit verdict.

    A causality break is raised when:
      - A claim is found and assigned to a known claim_type
      - The claim mentions a period anchor (action timing)
      - The reporting period itself shows a metric impact attributed to that action
      - The realized lag is shorter than the min_lag for that claim_type

    Sentences carrying IFRS-style consolidation language are downgraded because the
    short lag is a *mechanical* accounting effect, not a synergy promise.
    """
    rules = _load_rules()
    period_regex = rules["period_anchor_regex"]
    claim_types_cfg = rules["claim_types"]

    report_parsed = _parse_period(report_period_label)
    findings = []

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", report_text) if s.strip()]
    for idx, sentence in enumerate(sentences):
        if len(sentence) > 400:
            continue
        ctype = _classify_claim(sentence)
        if not ctype:
            continue
        neighborhood = " ".join(sentences[max(0, idx - 1): idx + 2])
        raw, parsed = _extract_period_anchor(sentence, period_regex)
        if not parsed and report_parsed:
            parsed = report_parsed
            raw = raw or report_period_label

        impact_phrase = re.search(
            r"(?i)\b(driving|drove|delivered|contributed to|resulted in|leading to|leads to|"
            r"attributable to|attributable primarily to|due (?:primarily )?to|as a result of|"
            r"thanks to|reflects|reflecting)\b[^.\n]{0,200}",
            sentence)
        if not impact_phrase:
            continue

        if not parsed or not report_parsed:
            continue

        lag = _quarter_distance(parsed, report_parsed)
        if lag is None:
            continue

        rule = claim_types_cfg.get(ctype, {})
        min_lag = rule.get("min_lag_quarters", rules.get("default_min_lag_quarters", 1))
        max_lag = rule.get("max_lag_quarters", rules.get("default_max_lag_quarters", 6))

        if lag < min_lag:
            verdict = "causality_break_too_fast"
            severity = "critical" if lag < 0 else "warning"
        elif lag > max_lag:
            verdict = "causality_window_expired"
            severity = "warning"
        else:
            verdict = "ok_within_window"
            severity = "ok"

        if verdict.startswith("ok"):
            continue

        consolidation_caveat = False
        if (
            ctype in _CLAIM_TYPES_SUBJECT_TO_CONSOLIDATION
            and (_is_consolidation_mechanical(sentence) or _is_consolidation_mechanical(neighborhood))
        ):
            consolidation_caveat = True
            severity = "info"
            verdict = "mechanical_consolidation_effect"

        findings.append({
            "rule_id": "lag_causality",
            "claim_type": ctype,
            "claim_excerpt": sentence[:240],
            "claim_period_raw": raw,
            "report_period_label": report_period_label,
            "lag_quarters": lag,
            "min_lag_quarters_required": min_lag,
            "verdict": verdict,
            "severity": severity,
            "consolidation_caveat": consolidation_caveat
        })

    return findings


if __name__ == "__main__":
    sample = (
        "In November 2024 we acquired ININ Group, which drove revenue growth in the fourth quarter of 2024. "
        "ININ Group was fully consolidated as of November 2024 in accordance with IFRS 3, contributing to Q4 2024 net sales. "
        "In December 2024 we issued senior unsecured bonds, contributing to strengthened liquidity in Q4 2024."
    )
    out = detect_lag_breaks(sample, "Q4 2024")
    print(json.dumps(out, indent=2, ensure_ascii=False))
