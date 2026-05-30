"""Load and organise raw report text into structured records keyed by company and period."""
import os
from .pdf_to_text import load_text_or_pdf


SUPPORTED_EXT = (".txt", ".pdf")


def load_company_reports(data_raw_dir, company_id):
    """Return ordered list of {company, period, source_path, text} for the given company.

    Directory convention:
      data_raw_dir/<company_id>/<period>/<report_file.(txt|pdf)>
    """
    company_dir = os.path.join(data_raw_dir, company_id)
    if not os.path.isdir(company_dir):
        return []

    reports = []
    for period in sorted(os.listdir(company_dir)):
        period_path = os.path.join(company_dir, period)
        if not os.path.isdir(period_path):
            continue
        for fname in sorted(os.listdir(period_path)):
            if not fname.lower().endswith(SUPPORTED_EXT):
                continue
            fpath = os.path.join(period_path, fname)
            text = load_text_or_pdf(fpath)
            reports.append({
                "company": company_id,
                "period": period,
                "source_path": fpath,
                "source_name": fname,
                "text": text
            })
    return reports
