"""Detector: Selective disclosure (KPI vanishing year-on-year).

Compare which KPIs were disclosed in last period's report vs current period's
report. KPIs that disappear, especially historically emphasised ones, are
flagged with weight-adjusted severity.
"""
import json
import os
import re
import unicodedata


CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "configs", "kpi_registry.json"
)

MIN_COMPARABLE_CHARS = 4000
RELATIVE_LENGTH_MIN_RATIO = 0.6


_NORDIC_FOLD = {"å": "a", "ä": "a", "ö": "o", "ø": "o", "æ": "ae", "ß": "ss"}


def _fold(text):
    text = text.lower()
    text = "".join(_NORDIC_FOLD.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text


def _load_registry():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _kpi_hits(text_folded, kpi):
    counts = []
    for alias in kpi["aliases"]:
        pattern = r"\b" + re.escape(_fold(alias)) + r"\b"
        n = len(re.findall(pattern, text_folded))
        if n:
            counts.append((alias, n))
    return counts


def _kpi_disclosure_profile(text, registry):
    folded = _fold(text)
    profile = {}
    text_len = max(len(folded), 1)
    for kpi in registry["kpis"]:
        hits = _kpi_hits(folded, kpi)
        total = sum(n for _, n in hits)
        if total == 0:
            continue
        emphasis = total / text_len * 10000
        profile[kpi["id"]] = {
            "weight": kpi["weight"],
            "hits": total,
            "aliases_matched": [a for a, _ in hits],
            "emphasis_score": round(emphasis, 3)
        }
    return profile


RECLASS_PATTERNS = [
    ("continuing_operations", re.compile(r"(?i)continuing operations|kvarvarande verksamhet|kvarverande virksomhet")),
    ("held_for_sale", re.compile(r"(?i)held for sale|innehas for forsaljning|innehas for salg|holdes for salg")),
    ("discontinued_operations", re.compile(r"(?i)discontinued operations|avveckl(?:ade|ad) verksamhet|avviklet virksomhet"))
]


def _detect_reclassification(prev_text, curr_text):
    prev_folded = _fold(prev_text)
    curr_folded = _fold(curr_text)
    new_frames = []
    for label, pattern in RECLASS_PATTERNS:
        prev_hits = len(pattern.findall(prev_folded))
        curr_hits = len(pattern.findall(curr_folded))
        if curr_hits > 0 and prev_hits == 0:
            new_frames.append({"frame": label, "current_hits": curr_hits})
        elif curr_hits >= 5 * max(prev_hits, 1) and curr_hits >= 3:
            new_frames.append({"frame": label, "current_hits": curr_hits, "prev_hits": prev_hits})
    return new_frames


def detect_disclosure_drops(prev_text, prev_period_label,
                            curr_text, curr_period_label):
    registry = _load_registry()
    prev_profile = _kpi_disclosure_profile(prev_text, registry)
    curr_profile = _kpi_disclosure_profile(curr_text, registry)

    len_curr = len(curr_text)
    len_prev = len(prev_text)
    short_doc_caveat = (
        len_curr < MIN_COMPARABLE_CHARS
        or len_prev < MIN_COMPARABLE_CHARS
        or (max(len_curr, len_prev) > 0
            and min(len_curr, len_prev) / max(len_curr, len_prev) < RELATIVE_LENGTH_MIN_RATIO)
    )

    findings = []

    new_frames = _detect_reclassification(prev_text, curr_text)
    for frame in new_frames:
        findings.append({
            "rule_id": "selective_disclosure",
            "kpi_id": f"reclassification:{frame['frame']}",
            "previous_period": prev_period_label,
            "current_period": curr_period_label,
            "previous_hits": frame.get("prev_hits", 0),
            "current_hits": frame["current_hits"],
            "weight": 1.0,
            "status": "new_classification_introduced",
            "comparability_warning": False,
            "verdict": "reclassification_introduced",
            "severity": "critical"
        })
    for kpi_id, prev_data in prev_profile.items():
        if kpi_id in curr_profile:
            continue
        weight = prev_data["weight"]
        emphasis = prev_data["emphasis_score"]

        if short_doc_caveat:
            severity = "info"
        elif weight >= 0.9 and emphasis >= 0.5:
            severity = "critical"
        elif weight >= 0.7:
            severity = "warning"
        else:
            severity = "info"

        if severity == "info":
            continue

        findings.append({
            "rule_id": "selective_disclosure",
            "kpi_id": kpi_id,
            "previous_period": prev_period_label,
            "current_period": curr_period_label,
            "previous_hits": prev_data["hits"],
            "previous_emphasis_score": emphasis,
            "weight": weight,
            "status": "disclosed_then_missing",
            "comparability_warning": short_doc_caveat,
            "verdict": "disclosure_drop",
            "severity": severity
        })

    return findings


if __name__ == "__main__":
    prev = "We delivered strong order backlog and adjusted EBITDA grew. Like-for-like sales improved."
    curr = "We delivered strong revenue and EBITDA stayed positive. The momentum continues."
    out = detect_disclosure_drops(prev, "FY2024", curr, "FY2025")
    print(json.dumps(out, indent=2, ensure_ascii=False))
