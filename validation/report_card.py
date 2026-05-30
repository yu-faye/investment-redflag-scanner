"""Per-company report card writer. Produces a structured JSON with severity tallies."""
import json
import os


def _tally(findings):
    counts = {"critical": 0, "warning": 0, "info": 0}
    for f in findings:
        sev = f.get("severity", "info")
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def write_company_report(out_dir, company_id, payload):
    company_dir = os.path.join(out_dir, "companies", company_id)
    os.makedirs(company_dir, exist_ok=True)

    findings = payload.get("findings", [])
    payload["severity_tally"] = _tally(findings)
    payload["findings_count"] = len(findings)

    report_path = os.path.join(company_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    follow_ups = [
        {
            "rule_id": rf["rule_id"],
            "severity": rf.get("severity"),
            "summary": rf.get("verdict") or rf.get("family") or rf.get("kpi_id"),
            "questions": rf.get("follow_up_questions", [])
        }
        for rf in findings if rf.get("follow_up_questions")
    ]
    fu_path = os.path.join(company_dir, "follow_ups.json")
    with open(fu_path, "w", encoding="utf-8") as f:
        json.dump({"company": company_id, "follow_ups": follow_ups},
                  f, ensure_ascii=False, indent=2)

    return report_path, fu_path
