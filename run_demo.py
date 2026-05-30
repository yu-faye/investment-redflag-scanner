import json
import os
from datetime import datetime, timezone

from src.question_framework import build_audit_questions
from src.related_party_parser import extract_related_entities
from src.rule_engine import run_rules


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs", "mock")


def _mock_data():
    data_2024 = {
        "year": 2024,
        "revenue_msek": 1820,
        "operating_cash_flow_msek": 1650,
        "cash_conversion_ratio": 0.91,
        "gross_margin_yoy_change_pct": -0.4,
        "working_capital_to_revenue_pct": 20.5,
        "related_party_revenue_share_pct": 6.2,
        "interest_coverage_ratio": 3.6,
        "inventory_growth_minus_revenue_growth_pct": 4.5
    }

    data_2025 = {
        "year": 2025,
        "revenue_msek": 2015,
        "operating_cash_flow_msek": 1490,
        "cash_conversion_ratio": 0.74,
        "gross_margin_yoy_change_pct": -2.4,
        "working_capital_to_revenue_pct": 26.7,
        "related_party_revenue_share_pct": 10.8,
        "interest_coverage_ratio": 2.7,
        "inventory_growth_minus_revenue_growth_pct": 11.9
    }

    note_text_2025 = (
        "Management notes resilient demand and disciplined execution. "
        "Transactions with NordBridge Holding AB and Fjord Capital Partners AS increased during the year. "
        "Several procurement arrangements were renewed under strategic collaboration terms."
    )

    return data_2024, data_2025, note_text_2025


def _build_dashboard_payload(rule_results):
    severity_count = {"critical": 0, "warning": 0, "ok": 0}
    for item in rule_results:
        sev = item["severity"]
        severity_count[sev] = severity_count.get(sev, 0) + 1

    return {
        "severity_count": severity_count,
        "top_alerts": [r for r in rule_results if r["severity"] in ("critical", "warning")][:5]
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    data_2024, data_2025, note_text_2025 = _mock_data()

    questions = build_audit_questions()
    rule_results = run_rules(data_2025)
    entities = extract_related_entities(note_text_2025)
    dashboard_payload = _build_dashboard_payload(rule_results)

    report = {
        "project": "qben_redflag_scanner",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "periods_compared": [data_2024["year"], data_2025["year"]],
        "questions": questions,
        "financial_snapshot": {
            "2024": data_2024,
            "2025": data_2025
        },
        "rule_results_2025": rule_results,
        "related_party_extraction_2025": entities
    }

    report_path = os.path.join(OUTPUT_DIR, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    payload_path = os.path.join(OUTPUT_DIR, "dashboard_payload.json")
    with open(payload_path, "w", encoding="utf-8") as f:
        json.dump(dashboard_payload, f, ensure_ascii=False, indent=2)

    print("Demo completed.")
    print(f"Generated: {report_path}")
    print(f"Generated: {payload_path}")


if __name__ == "__main__":
    main()
