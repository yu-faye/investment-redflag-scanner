import json
from datetime import datetime, timezone


def build_audit_questions():
    """Return structured offensive-audit questions for automated runs."""
    return [
        {
            "id": "Q1",
            "theme": "Revenue quality",
            "question": "Is revenue growth supported by operating cash flow and stable receivables quality?",
            "expected_inputs": ["revenue", "operating_cash_flow", "cash_conversion_ratio"]
        },
        {
            "id": "Q2",
            "theme": "Margin credibility",
            "question": "Do management claims about efficiency align with gross margin movement?",
            "expected_inputs": ["gross_margin_pct", "management_commentary"]
        },
        {
            "id": "Q3",
            "theme": "Related-party risk",
            "question": "Are related-party transactions sufficiently low, transparent, and justified?",
            "expected_inputs": ["related_party_revenue_share_pct", "note_text"]
        },
        {
            "id": "Q4",
            "theme": "Balance-sheet pressure",
            "question": "Is debt service capacity resilient under weaker earnings scenarios?",
            "expected_inputs": ["interest_coverage_ratio", "short_term_debt_share_pct"]
        }
    ]


def render_questions_json(period_tag):
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "period": period_tag,
        "questions": build_audit_questions()
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    print(render_questions_json("demo"))
