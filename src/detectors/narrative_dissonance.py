"""Detector: Narrative vs numbers dissonance.

For each keyword family in claim_metric_map.json, count claim mentions in the
report text and compare against the YoY direction of supporting metrics from
the structured input. Emit a dissonance score for each family.
"""
import json
import os
import re


CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "configs", "claim_metric_map.json"
)


def _load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _count_terms(text, terms):
    text_lower = text.lower()
    total = 0
    hits = []
    for term in terms:
        n = text_lower.count(term.lower())
        if n > 0:
            hits.append({"term": term, "count": n})
            total += n
    return total, hits


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def _first_sentence_with_any_term(text, terms):
    """Return the first sentence that contains any of `terms` (case insensitive)
    along with the term that matched. Used to give narrative_dissonance findings
    a concrete `claim_excerpt` so the evidence-snippet pipeline can crop it."""
    if not text:
        return None, None
    lower_terms = [t.lower() for t in terms]
    for raw in _SENTENCE_SPLIT.split(text):
        sentence = raw.strip()
        if not sentence or len(sentence) > 400:
            continue
        sl = sentence.lower()
        for t in lower_terms:
            if t in sl:
                return sentence, t
    return None, None


def _alignment(direction, metric_value, neutral_band=None):
    """Return alignment in {-1, 0, 1} for a single metric with direction semantics.

      up      -> higher is better (claim supported when value > 0 / above band)
      down    -> lower is better (claim supported when value below band)
      neutral -> claim supported when value inside band, else -1
    """
    if metric_value is None:
        return 0
    if direction == "up":
        if neutral_band:
            lo, hi = neutral_band
            return 1 if metric_value >= lo else -1
        if metric_value > 0:
            return 1
        if metric_value < 0:
            return -1
        return 0
    if direction == "down":
        if neutral_band:
            lo, hi = neutral_band
            return 1 if metric_value <= hi else -1
        return 1 if metric_value <= 0 else -1
    if direction == "neutral":
        if not neutral_band:
            return 0
        lo, hi = neutral_band
        return 1 if lo <= metric_value <= hi else -1
    return 0


def detect_narrative_dissonance(report_text, metrics):
    config = _load_config()
    families = config["families"]
    findings = []

    text_length = max(len(report_text), 1)
    for fam_id, fam in families.items():
        count, hits = _count_terms(report_text, fam["claim_terms"])
        if count == 0:
            continue
        mention_density = count / text_length * 10000

        per_metric_alignment = []
        for spec in fam["supporting_metrics"]:
            metric_key = spec["metric"]
            value = metrics.get(metric_key)
            score = _alignment(
                spec.get("expected_direction", "up"),
                value,
                neutral_band=spec.get("neutral_band")
            )
            per_metric_alignment.append({
                "metric": metric_key,
                "value": value,
                "expected_direction": spec.get("expected_direction", "up"),
                "score": score
            })
        usable = [m for m in per_metric_alignment if m["value"] is not None]
        if not usable:
            verdict = "insufficient_metric_data"
            severity = "info"
            alignment = None
        else:
            alignment = sum(m["score"] for m in usable) / len(usable)
            if alignment < -0.25:
                verdict = "dissonance_detected"
                severity = "critical" if count >= 3 and alignment <= -0.5 else "warning"
            elif alignment < 0.25:
                verdict = "weak_support"
                severity = "warning" if count >= 3 else "info"
            else:
                verdict = "aligned"
                severity = "ok"

        if verdict in ("aligned", "insufficient_metric_data"):
            continue

        sample_sentence, matched_term = _first_sentence_with_any_term(
            report_text, fam["claim_terms"]
        )
        findings.append({
            "rule_id": "narrative_dissonance",
            "family": fam_id,
            "mention_count": count,
            "mention_density_per_10k_chars": round(mention_density, 2),
            "term_hits": hits,
            "metric_alignment": per_metric_alignment,
            "alignment_score": round(alignment, 2) if alignment is not None else None,
            "verdict": verdict,
            "severity": severity,
            "claim_excerpt": sample_sentence,
            "claim_excerpt_matched_term": matched_term,
        })

    return findings


if __name__ == "__main__":
    text = "We delivered strong growth and operational discipline. Resilient demand supported expansion."
    metrics = {
        "revenue_yoy_pct": -2.0,
        "organic_growth_pct": -1.0,
        "gross_margin_yoy_change_pct": -3.0,
        "ebita_margin_pct": -14.0
    }
    out = detect_narrative_dissonance(text, metrics)
    print(json.dumps(out, indent=2, ensure_ascii=False))
