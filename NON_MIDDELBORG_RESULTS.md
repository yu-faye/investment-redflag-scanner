# Non-Middelborg Companies — Separate Results

This file isolates benchmark / non-portfolio companies so they do not mix with the Middelborg-focused narrative.

## Scope

- `otovo` — Otovo ASA (OTOVO, Euronext Oslo Bors) — solar marketplace
- `norwegian_air` — Norwegian Air Shuttle ASA (NAS, Oslo Bors) — low-cost airline
- `norse_atlantic` — Norse Atlantic Airways ASA (NORSE, Euronext Expand Oslo) — long-haul low-cost airline
- `mowi` — Mowi ASA (MOWI, Oslo Bors) — aquaculture (clean baseline)

## Run summary (refreshed 2026-05-24)

Counts come from `outputs/validation_summary.json` and `outputs/companies/<id>/report.json`.

| Company ID | Findings | Severity tally | Report path |
| --- | ---: | --- | --- |
| `otovo` | 4 | critical=3, warning=1, info=0 | `outputs/companies/otovo/report.json` |
| `norwegian_air` | 2 | critical=1, warning=1, info=0 | `outputs/companies/norwegian_air/report.json` |
| `norse_atlantic` | 2 | critical=2, warning=0, info=0 | `outputs/companies/norse_atlantic/report.json` |
| `mowi` | 0 | clean baseline | `outputs/companies/mowi/report.json` |

## Top eye-catching findings (Non-Middelborg)

| # | Company | Severity | Priority | Headline |
| ---: | --- | --- | ---: | --- |
| 1 | Otovo ASA | critical | 25.2 | "growth" narrative cited 46 times, but supporting metrics move opposite (`revenue_yoy_pct=-42.0`, `organic_growth_pct=-42.0`). |
| 2 | Otovo ASA | critical | 8.55 | "efficiency" narrative cited 9 times, but supporting metrics move opposite (`gross_margin_yoy_change_pct=-8.0`, `ebita_margin_pct=-51.0`). |
| 3 | Norse Atlantic Airways ASA | critical | 8.1 | "efficiency" narrative cited 8 times, but supporting metrics move opposite. |
| 4 | Otovo ASA | critical | 7.2 | "resilience" narrative cited 6 times, but `interest_coverage_ratio=0.5`, `net_debt_to_ebitda=9.9`. |
| 5 | Norwegian Air Shuttle ASA | critical | 5.85 | "resilience" narrative cited 3 times, but `interest_coverage_ratio=2.4`, `net_debt_to_ebitda=4.8`. |
| 6 | Norse Atlantic Airways ASA | critical | 5.85 | "resilience" narrative cited 3 times, but `interest_coverage_ratio=0.3`, `net_debt_to_ebitda=99.0`. |

## Source registry — non-Middelborg

These targets are kept as TXT-only caches today; canonical URLs are in `data/sources.json`.

| Company | Period | Local TXT | Issuer page |
| --- | --- | --- | --- |
| Otovo ASA | FY 2024 | `data/raw/otovo/2024/otovo_annual_2024.txt` | https://www.otovo.com/investor-relations/ |
| Otovo ASA | Q4 2024 | `data/raw/otovo/2024/otovo_q4_2024_report.txt` | https://www.otovo.com/investor-relations/ |
| Norwegian Air Shuttle ASA | FY 2024 | `data/raw/norwegian_air/2024/norwegian_air_annual_2024.txt` | https://www.norwegian.com/no/about/company/investor-relations/reports-and-presentations/ |
| Norwegian Air Shuttle ASA | Q4 2024 | `data/raw/norwegian_air/2024/norwegian_air_q4_2024.txt` | https://www.norwegian.com/no/about/company/investor-relations/reports-and-presentations/ |
| Norse Atlantic Airways ASA | FY 2024 | `data/raw/norse_atlantic/2024/norse_atlantic_annual_2024.txt` | https://norseatlantic.com/en/investor/reports-presentations/ |
| Mowi ASA | FY 2024 | `data/raw/mowi/2024/mowi_annual_2024.txt` | https://mowi.com/investors/financial-reports/ |

## Notes

- These companies are only for stress-testing pipeline robustness and interview contrast (Mowi is the clean baseline; Otovo / Norse Atlantic / Norwegian Air should and do flag heavily because they are genuinely distressed during 2024).
- Middelborg-only outputs remain in:
  - `outputs/middelborg_validation_summary.json`
  - `outputs/middelborg_dashboard_payload.json`
  - `outputs/middelborg_leaderboard.json`

## Click-through to source (v3)

The v3 dashboard treats non-Middelborg rows exactly like Middelborg rows: every finding carries a `provenance` block with `issuer_url`, `github_blob_url`, `local_path`, `sha256`, `git_sha`, and best-effort `pdf_page_hit`. Toggle the "All companies" tab in [`dashboard/index.html`](dashboard/index.html) or the "All companies" scope in [`app/streamlit_app.py`](app/streamlit_app.py) to inspect non-Middelborg rows with the same three click destinations:

1. Issuer PDF at the matched page
2. Immutable GitHub commit permalink
3. Local `file://` PDF for offline review

Non-Middelborg targets are currently TXT-only caches; their provenance blocks therefore carry `github_blob_url` / `github_head_url` for the cached text file (not a PDF). To upgrade any of them to PDF-anchored provenance, drop the source PDF under `data/raw/<co>/<period>/` and re-run `scripts/refresh_provenance.py`.
