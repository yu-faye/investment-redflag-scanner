# Investment Red-Flag Scanner — v10 Results

A reproducible, deterministic red-flag scanner targeting Middelborg Invest AS portfolio companies. Architecture pivot in v9: from a fixed-set of detectors to a **hypothesis-driven triangulation system** where every audit claim is checked across an extensible set of independent external data taps, and severity is derived from the cross-tap evidence ledger by a single rule-bound engine — never by any individual tap. **v10 extends that system in two orthogonal directions: (a) cross-tap *synthesizing* `derived_analysis` taps that read other taps' output and a third external source (TED, the EU public procurement registry) to attack a current-period (Q1 2026) revenue claim; and (b) a three-layered logic-chain visualization — global Sankey + per-finding auditor narrative paragraph + per-finding argument tree — so a non-technical reader can reproduce the chain of reasoning behind any flag in 30 seconds.**

Evolution: v3 added git-anchored provenance + click-through dashboard; v4 narrowed the click-through to the right sentence; v5 turned each flagged sentence into a pre-cropped PNG of the original PDF row; v6 extended that grounding to every numeric value cited in the headline and retired the informal `drama_score` label in favour of `priority_score`; v7 removes the bilingual (EN/ZH) module entirely and makes every evidence snippet directly clickable to jump to the source PDF; v8 adds the first external collision detector cross-referencing PDF self-reported subsidiary claims against the **Doffin (Norwegian public procurement)** registry; **v9** lifts hypotheses to first-class objects, refactors external data sources into pluggable `EvidenceTap`s with a standard verdict vocabulary, introduces an append-only per-hypothesis evidence ledger under `data/ledger/`, and ships a `TriangulationEngine` that enforces structural rules (single-source absence cannot drive critical, critical requires peer-control passage and all blocking falsification questions addressed). The second tap, **BRREG (Brønnøysundregistrene, the Norwegian central register)**, ships alongside; adding it took ~80 lines and zero changes to the engine, the dashboards, or the existing Doffin code — the proof that the architecture works as a system. **v10 adds the third tap (TED, EU public procurement) and the first two *derived* taps (`derived_revenue_support`, `derived_explanatory_slippage`) that synthesize evidence across primary taps and against the source PDF's CEO-comment region, all behind the same unchanged engine/dashboard contracts; then re-targets the pipeline at Qben Infra's *current* Q1 2026 report (revenue -13.3% YoY, EBITA margin -26.0%, headline profit driven entirely by a Rail-divestment gain) and lets the engine self-derive a critical finding from two independent refuting axes.**

## v10 changes (cross-tap synthesizing + logic-chain visualization)

| Area | v9 | Now (v10) |
| --- | --- | --- |
| Report ingestion | FY2024 standalone attacks; current_report rolled forward only to Q2 2025. | **Qben Infra Q1 2026 interim + FY2025 annual reports ingested.** `data/sources.json` now carries both with sha256 + provenance; `validation/companies.json` rolls `qben_infra.current_report` to Q1 2026 (revenue 169 MSEK vs 195 prior = -13.3% YoY; EBITA margin -26.0%; headline profit 147 MSEK driven by a Rail-divestment one-off, continuing-ops result is -53 MSEK). A new `qben_infra_annual_2025` companies entry anchors the FY2025 vs FY2024 selective-disclosure comparison (both 230k+ chars). |
| Number of independent external data taps | 2 (Doffin + BRREG). | **3 primary** (Doffin, BRREG, **TED** — EU public procurement, no API key, multi-lingual JSON normalised via `_pick_eng_title`) **plus 2 derived** (`derived_revenue_support`, `derived_explanatory_slippage`) that do *not* hit external APIs and instead read other taps' ledger rows and the source PDF to synthesize new evidence. TED carries a calibrated absence-of-evidence note: its coverage is EU-directive-threshold only (~NOK 45M for works), so `not_found` is emitted at confidence 0.45 rather than 0.85+. |
| Hypothesis categories | Implicit (all hypotheses look like subsidiary specialist claims). | **First-class `claim_category` field.** `subsidiary_specialist` (legacy) and **`revenue_pipeline_support`** (new). The new category tests whether reported revenue in a Norway-heavy, public-procurement-exposed segment is grounded in external order flow, *and* whether the CEO narrative is consistent with the directional change in revenue and margin. |
| Engine rule surface | 6 structural rules (R1 single-source, R2 peer-control, R3 falsification coverage, etc.) — all category-agnostic. | **7th rule (R7) is the first category-specific rule.** For `revenue_pipeline_support` hypotheses, two independent refuting *derived* axes (revenue_support + explanatory_slippage) elevate to **critical** even when no primary tap reaches confirms. Justification: two independent analytical axes (numbers vs narrative; external vs internal) failing in the same direction is qualitatively different from one tap contradicting an absence. R7 also skips R2 (peer-control) for this category because peers are subsidiary_specialist claims, not revenue claims. R7 is *additive*; R1–R6 remain in force for every other hypothesis. |
| Severity authority | Engine-only (verdicts in `{confirms, partial, refutes, not_found, neutral, error}`, severity derived). | **Same**, plus the new `derived_analysis` `tap_kind` is treated identically: derived taps emit verdicts, never severity. The engine alone applies R7. |
| Visualization | Per-row drawer (narrative snippet + per-number provenance + external collision + provenance card); top-of-page triangulation matrix + audit roadmap. | **Three new deterministic visualization layers above and below the v9 layer:** (1) Global **Sankey** of the reasoning landscape — `issuer → claim/rule → falsification question → tap evidence (verdict) → derived severity` — top of the page, clickable nodes filter the leaderboard to only findings flowing through that node; (2) per-finding **auditor narrative paragraph** with inline `[N]` citations linking to the cache file, source PDF (page-anchored), or engine derivation row; (3) per-finding **argument tree** as nested `<details>`/`st.expander` with verdict / severity colour coding so a non-technical reader can collapse / expand any reasoning step. All three are pure-template / pure-data and contain zero LLM output — important for auditability. |
| Reproducibility | Per-API response cached with sha256 + fetched_at_utc + query_url; per-hypothesis append-only ledger. | **Same**, plus every derived tap also writes a cache file at `data/external/<derived_tap_id>/<source_company>/<hypothesis_id>_*.json` enumerating the inputs it synthesized over (per-peer post-acquisition awards for `derived_revenue_support`; positive/acknowledgement token spans for `derived_explanatory_slippage`). The derived verdict is reproducible by hand. The three v10 payloads (`outputs/sankey_data.json`, `outputs/argument_trees.json`, `outputs/narrative_paragraphs.json`) are deterministic functions of the run, so the dashboards re-render identically on a fresh clone. |

### v10 worked example — Qben Infra AB's Q1 2026 revenue claim under cross-tap synthesis

The headline of Qben Infra's Q1 2026 interim report (published 2026-05-29) reads `kvartalets resultat 147 MSEK (-113)` — a swing from -113 MSEK to +147 MSEK that could be read as a turnaround. The full claim under audit:

> *"Qben Infra's Q1 2026 reported continuing-operations revenue of 169 MSEK is grounded in real external order flow from Norwegian and EU public-sector buyers, and the CEO narrative description of the quarter is consistent with the measured revenue trajectory (no explanatory slippage between negative numbers and uniformly positive language)."*

The engine refuses to confirm or refute that claim with any single tap. Instead:

| Tap | tap_kind | Verdict | Conf | Why |
| --- | --- | --- | --- | --- |
| `ted` | `public_procurement` | `not_found` | 0.45 | TED returned 0 notices where `Qben Infra AB` is a winner. **Calibrated low confidence** because TED only carries above-EU-threshold awards (~NOK 45M for works); a smaller-scale supplier can legitimately be invisible to TED while still active on Doffin. |
| `derived_revenue_support` | `derived_analysis` | `refutes` | 0.65 | Walks the 4 `peer_controls` (SLAM, Banefjell, Team Bygg, Nordnes Narvik), reads each peer's most recent Doffin+TED entries, finds only **1 of 4 (25%) operating subsidiaries** has any post-acquisition prime-contract footprint (Banefjell). 4 confirmed post-acquisition awards in aggregate. Below the hypothesis's `external_support_threshold_pct = 50.0`. **Refutes** the external-support question. |
| `derived_explanatory_slippage` | `derived_analysis` | `refutes` | 0.75 | Scans the CEO-comment region (first 5000 chars after `VD-ord` / `Comments by the CEO`). Counts **12 positive-framing tokens** (Swedish + English: `growth`, `tillväxt`, `stark`, `förbättring`, `progress`, `momentum`, etc.) against **0 acknowledgement tokens** (`utmaning`, `setback`, `declined`, `weaker`, etc.), while revenue is **-13.3% YoY** and EBITA margin is **-26.0%**. Pure positive framing while the KPIs deteriorate in the opposite direction → **explanatory slippage**. |

**Engine R7 fires:** both derived axes refute → severity = **critical**, even though no primary tap reaches `confirms`. The leaderboard headline reads:

> **[CRITICAL] Qben Infra AB: Q1 2026 revenue claim REFUTED on both axes.** *Reported continuing-operations revenue of 169 MSEK is grounded in external order flow at only 25% of operating subsidiaries (threshold 50%): 4 confirmed post-acquisition prime contracts across 4 peer-subsidiary hypotheses [1]. Yet the CEO-comment region contains 12 positive-framing tokens against 0 acknowledgement tokens, while revenue declined -13.3% YoY and EBITA margin sits at -26.0% [2] [3]. Both derived dimensions refute. Two independent axes failing in the same direction is treated as critical even without peer-tap confirms [4].*

Each `[N]` is a clickable citation in the dashboard:
- `[1]` → cache file `data/external/derived_revenue_support/qben_infra/hyp_qben_q1_2026_revenue_pipeline_revenue_support.json`
- `[2]` → cache file `data/external/derived_explanatory_slippage/qben_infra/hyp_qben_q1_2026_revenue_pipeline_explanatory_slippage.json`
- `[3]` → source PDF `data/raw/qben_infra/2026_q1/qben_infra_q1_2026.pdf` (page-anchored to the CEO-comment region)
- `[4]` → in-page anchor `#engine-rule-r7-hyp_qben_q1_2026_revenue_pipeline` on the argument-tree node where R7 fires.

The killer pitch line that v9 could not honestly say but v10 can: *"My architecture refused to call this critical on any single tap. The engine reached critical only after two independent analytical axes — one numerical (peer subsidiaries' external order flow falls below a pre-registered threshold), one narrative (CEO-comment positive-framing density vs measured KPI direction) — both refuted the claim in the same direction, behind a category-specific rule (R7) registered in the engine before this report was published. The same architecture also independently flagged a selective_disclosure finding on the FY2025 vs FY2024 annual report comparison. And every reader, technical or not, can read the auditor narrative paragraph for any finding in 30 seconds and click through every `[N]` citation to the underlying cache file, source PDF page, or engine derivation row — there is no LLM in the visualization layer."*

### v10 three-layered logic-chain visualization

1. **Global Sankey** (top of page). Layers: `issuer → claim/rule → falsification question → tap evidence (verdict) → derived severity`. One flow unit per finding, split across falsification questions. Node colours follow tap verdict (green=confirms, red=refutes, orange=not_found). **Clicking any node filters the leaderboard** to only findings whose reasoning passes through that node — the analyst can ask "show me every finding where any derived tap refuted" with one click.
2. **Per-finding auditor narrative paragraph**. Pure-template, zero-LLM, deterministic. Templates per `claim_category` (`revenue_pipeline_support` has its own R7-aware template; subsidiary_specialist, narrative_dissonance, selective_disclosure, lag_causality, and a generic fallback). Inline `[N]` citations link to the cache file, page-anchored source PDF, or engine derivation row.
3. **Per-finding argument tree**. Nested `<details>` (static dashboard) or nested `st.expander` (Streamlit). Hierarchy: `claim → engine_rule → falsification question → evidence → source`. Verdict / severity colour coding inherits from the v9 verdict glyphs (`vd-confirms`, `vd-refutes`, etc.). Default depth: root and immediate children expanded; deeper levels collapsed.

All three rendering layers consume deterministic JSON payloads (`outputs/sankey_data.json`, `outputs/argument_trees.json`, `outputs/narrative_paragraphs.json`) emitted by `src/visualization/{sankey_builder,argument_tree,narrative_writer}.py`. The static dashboard (D3 + d3-sankey from CDN) and the Streamlit app (plotly.go.Sankey + st.expander) render the same payloads, so the two surfaces show the same chain of reasoning for any finding.



## v9 changes (hypothesis-driven triangulation system)

| Area | v8 | Now (v9) |
| --- | --- | --- |
| Architecture core | Each external data source = a self-contained detector that emits findings with a self-assigned severity. | **Hypothesis is first-class.** `validation/hypotheses.json` registers audit claims independent of any data source. Each external source becomes an `EvidenceTap` that emits standardised `EvidenceEntry` rows into the hypothesis's append-only ledger. A single `TriangulationEngine` derives severity from the cross-tap evidence — never any single tap. |
| Number of independent external data taps | 1 (Doffin). | **2** (Doffin + BRREG), in the same architecture, with the engine and dashboards completely tap-agnostic. The third (e.g. Newsweb insider trading, Proff financial filings, a subcontractor directory) drops in as another ~80-line tap file with zero downstream changes. |
| Severity authority | Each detector wrote its own severity into the finding. | **Engine-only.** A tap can only emit a verdict in `{confirms, partial, refutes, not_found, neutral, error}`. The engine combines verdicts via three structural invariants: (a) single-source absence cannot drive critical, (b) peer-control rule — refuting evidence is only treated at full weight when at least one peer hypothesis confirmed via the same tap, and (c) every falsification question marked `blocking_for_critical=true` must be addressed before critical is allowed. |
| Falsification rigor | Implicit. | **Explicit.** Each hypothesis declares `falsification_questions[]` with `relevant_tap_kinds` + `blocking_for_critical`. The engine reports `resolved_falsification_questions`, `pending_falsification_questions`, and `blockers_for_critical` per hypothesis — the dashboard surfaces them as the "what would graduate this to critical" panel. |
| Audit roadmap | Implicit / unspoken. | **First-class output.** After each run, the engine emits `outputs/audit_roadmap.json` ranking external data sources by the total expected information gain they would unlock across all pending hypotheses. The static dashboard and Streamlit app each render this as a numbered roadmap — *"the system tells you which data source to add next."* |
| Reproducibility | Per-API response cached with sha256 + fetched_at_utc + query_url. | **Same**, plus the new append-only per-hypothesis ledger at `data/ledger/<hypothesis_id>.jsonl`. Every tap call appends one EvidenceEntry; nothing ever rewrites. Re-running a tap appends a new row. The ledger is the audit trail. |
| Dashboard panels | Per-row "External collision" drawer. | **Two new system-level panels at the top of the dashboard:** *Triangulation matrix* (rows=hypotheses, columns=tap_kinds, cells=verdict glyphs with hover tooltip) and *Audit roadmap* (ranked next-tap recommendations). Per-row drawer still works for finding-level drill-down. |
| Engine self-correction (the proof) | v8 had SLAM Jernbaneteknikk AS at **critical priority 15.0** based on Doffin alone showing 0 awards. | v9 sees BRREG confirm that SLAM is a real 36-employee NAICS 42.12 (railway construction) company founded 2021 — a genuine specialist by registry classification. The engine **automatically downgrades SLAM from critical → warning 6.75** (Doffin says "no direct prime contracts," BRREG says "but the company is real and in the right industry" → most likely subcontractor chain, which sits as the top open falsification question). At the same time, **Team Bygg AS rises from v8 warning 4.5 → v9 critical 19.2** because BRREG independently refutes the claim (NAICS 68.12 = real estate trading, not 41.* = housing construction) AND Doffin independently refutes (0 confirmed awards). Two-source agreement, all blocking questions addressed, peer control passes via Banefjell. |

### v9 worked example — Qben Infra AB's Norwegian growth story under triangulation

| Hypothesis | Doffin verdict | BRREG verdict | Derived severity | Priority | Reason |
| --- | --- | --- | --- | --- | --- |
| **SLAM Jernbaneteknikk AS** is a "specialist in rail signalling and electrification" | `not_found` (conf 0.70) | `confirms` (conf 0.85, orgnr 926465252, founded 2021, 36 employees, NAICS 42.12) | **warning** | 6.75 | Single tap refuting; BRREG confirms entity is real specialist by NAICS. Engine refuses to escalate to critical because the alternative hypothesis (subcontractor chain) is unresolved. |
| **Banefjell AS** is "an established player in track maintenance" | `confirms` (4 awards from Bane NOR SF) | `confirms` (orgnr 914019087, founded 2014, 60 employees, NAICS 42.12) | **info** | 1.50 | Two independent taps confirm. This is the *peer control* for the other three hypotheses — its passage proves Doffin + BRREG reach this entity class, so absence elsewhere isn't a tap coverage gap. |
| **Team Bygg AS** is "comprehensive resources for executing real estate projects in the Norwegian housing market" | `refutes` (5 search hits, 0 actually winning) | `refutes` (orgnr 984738161 IS the top exact name match BUT NAICS 68.12 = real estate trading, NOT 41.* housing construction) | **critical** | 19.2 | Two independent taps refute consistently. Engine awards critical because: (a) >=2 refuting taps, (b) peer control passes via Banefjell, (c) all blocking falsification questions addressed. |
| **Nordnes Narvik AS** is "a key player in railway construction in North Norway" | `partial` (1 award, but pre-acquisition 2021 to Spordrift AS) | `refutes` (NAICS 47.52 = retail hardware/paint/glass, top hit may be wrong entity → identity disambiguation needed) | **warning** | 6.75 | Identity ambiguity blocks critical. Engine queues `company_registry` follow-up via orgnr search of any other "Nordnes Narvik" entities. |

The killer pitch line that v8 could not honestly say but v9 can: *"My architecture refused to call SLAM critical even though Doffin had zero records, because a single source cannot drive critical in this system. When the second tap (BRREG) came in and confirmed SLAM was a real specialist company, the engine independently downgraded the finding from v8's critical to a v9 warning — and at the same moment, **independently re-discovered** that Team Bygg AS, which my v8 detector had only flagged as warning 4.5, is actually the highest-conviction external_collision finding because both taps agree (NAICS mismatch on the registry side + zero awards on the procurement side, with Banefjell as a passing peer control)."*

### v9 audit roadmap (what the system says I should add next)

After the Doffin + BRREG run, `outputs/audit_roadmap.json` ranks the next external data source by total information gain across all open hypotheses:

| # | tap_kind | total info gain | covers | comment |
| --- | --- | --- | --- | --- |
| 1 | `subcontractor_directory` | +0.30 | SLAM (fq_subcontractor_chain) | If SLAM shows up as a named subcontractor on Bane NOR signalling prime contracts, the warning resolves cleanly to "verified specialist working through subcontracting." This is the single highest-value next tap. |

Anything that's `—` in the matrix (financial_filings, insider_trading, employee_signal, media) is a future tap; the matrix will gain a new column when that tap is implemented with zero changes to anything else.

## v8 changes (external collision — PDF narrative vs Doffin public record)

## v8 changes (external collision — PDF narrative vs Doffin public record)

| Area | v7 | Now (v8) |
| --- | --- | --- |
| Number of detectors | 4 (lag_causality, narrative_dissonance, selective_disclosure, stress_test_prompts) | **5.** Added `src/detectors/external_collision.py` — first subtype `norwegian_subsidiary_organic_vs_awarded`. Pure function over `(company_id, current_text, external_sources_config)`; cache + fetch injected so unit tests pass without network. |
| External data sources | None (every claim was checked against the PDF and prior-period PDFs only) | **Doffin** (Norwegian public procurement registry) via `https://api.doffin.no/webclient/api/v2/` — the SPA endpoint that does not require a subscription key. Reverse-engineered the request shape (POST search with `searchString` + `sortBy=PUBLICATION_DATE_DESC`, GET notice detail by `id`) so a fresh laptop with no Doffin account can reproduce. |
| Provenance discipline | Per-PDF: every snippet has page+bbox+sha256 of the source file. | **Same standard for every API call.** `src/ingest/external/doffin_client.py` writes the raw JSON to `data/external/doffin/<company_id>/<supplier-slug>_<date>.json` with sha256, `fetched_at_utc`, `query_url`, and the exact request body. The finding then carries `external_evidence[].cache_path / cache_sha256 / fetched_at_utc / query_url` so the reviewer can verify the score without re-hitting Doffin. |
| Companies.json schema | `manual_metrics`, `manual_metrics_provenance` per report | Added `external_sources.doffin.supplier_subsidiaries[]` block on `qben_infra_2024`: each entry has `name`, `aliases`, `acquired_year`, `claimed_role` (extracted verbatim from the FY2024 report), and `narrative_anchor` (sentence anchor for snippet generation). |
| Scoring | Severity × evidence-magnitude per rule | New `_score_external_collision`: severity × (1 + gap_boost), where `gap_boost = 4.0` when a claimed *specialist / established player / key player / leader* has **zero** confirmed Doffin awards. The killer finding (SLAM Jernbaneteknikk) lands at priority **15.0**, ranking #2 critical in the Middelborg leaderboard. |
| Dashboard rendering | Three drawer sections: narrative snippet / per-number provenance / source provenance | **Four** sections. New `renderExternalCollision()` (static) and `_render_external_collision()` (Streamlit) card: header with source label + supplier + variance count, table with one row per confirmed award (date, buyer, heading-as-Doffin-notice-link, awarded suppliers), empty-state warning when zero awards confirm, and a tiny provenance footer showing the cached JSON path + sha256. |
| Streamlit interactivity | "Show in PDF below" buttons on narrative snippet + each per-number card | Same click-to-PDF for the narrative claim (the sentence about the subsidiary still scrolls the embedded viewer); the External collision section uses Streamlit's native `LinkColumn` so every Doffin notice heading is a one-click `open` link to the original `https://www.doffin.no/notices/<id>` page in a new tab. |

### v8 worked example — Qben Infra AB's Norwegian growth story vs the public record

Qben Infra AB is Swedish; its FY2024 report frames its organic growth via four named **Norwegian** subsidiary acquisitions. Doffin records all public procurement awards in Norway, so each subsidiary's claim has a falsifiable shadow trace.

| Subsidiary | Qben's claim (FY2024 report verbatim) | Doffin confirmed awards | Verdict | Priority |
| --- | --- | --- | --- | --- |
| **SLAM Jernbaneteknikk AS** | "specialist in rail signalling and electrification in Norway" | **0 confirmed, 0 search hits** | `no_public_record` (critical) | **15.0** |
| **Team Bygg AS** | "comprehensive resources for executing real estate projects in the Norwegian housing market" | 0 confirmed (5 search hits, all false positives on the generic name) | `no_public_record` (warning) | 4.5 |
| **Nordnes Narvik AS** | "key player in railway construction in North Norway" | 1 confirmed — but issued 2021-09-28 to Spordrift AS, **pre-acquisition** (Qben acquired Jan 2025) | `mostly_pre_acquisition` (warning) | 3.0 |
| **Banefjell AS** | "established player in track maintenance, concrete renovation and signal" | 4 confirmed — 2023, 2024, two 2026 awards, all from Bane NOR SF (Norwegian rail operator) | `consistent_with_public_record` (info) | 0.5 |

The killer line for an interview: *"Qben tells the market its 2024 Norwegian expansion gives it a specialist in rail signalling. Doffin — the official Norwegian public procurement registry — has zero confirmed award records for that specialist. The only one of the four subsidiaries with real award activity is Banefjell, which is doing track maintenance, not signalling."* Backed by `data/external/doffin/qben_infra_2024/*.json` with sha256 + timestamps the reviewer can re-verify in 30 seconds.

## v7 changes (English-only + click-anything-to-PDF)

## v7 changes (English-only + click-anything-to-PDF)

| Area | v6 | Now (v7) |
| --- | --- | --- |
| Language module | Every detector returned `{en, zh}` for headlines and follow-up prompts. Static + Streamlit dashboards exposed a language toggle. | **Removed.** `priority_scorer.make_headline()` returns a plain `str`. `stress_test_prompts._TEMPLATES` collapsed from `{axis, en, zh}` to `{axis, text}`. Dashboard language toggles deleted (HTML, JS, Streamlit radio). The orchestrator print loop has a back-compat `_hl()` for legacy payloads but new runs are English-only. |
| Static dashboard click-through | Three row-level jump buttons (issuer PDF / GitHub permalink / local file) at the right of the row. Snippet PNGs were display-only. | **Every snippet image is now an `<a target="_blank">`** linking to its own page-anchored URL. Narrative snippet links to `issuer_url#page=N&search=phrase`; each per-metric snippet links to `issuer_url#page=N&search=matched_str` so the browser's built-in PDF viewer lands directly on that row. Hover outline + ⇗ caption arrow signal clickability. URL precedence: issuer → GitHub raw → GitHub blob → local file (so the link always resolves to something). |
| Streamlit drill-down | Two cards stacked: snippet PNG on top, full PDF in a collapsed expander at the bottom. No way to jump from a metric card to its row inside the embedded PDF. | **Side-by-side layout.** Evidence cards on the left (narrative + each per-number card), embedded `streamlit-pdf-viewer` on the right (always visible, no expander). Every card has a `⇩ Show in PDF below (jump to page N)` button that updates `st.session_state['pdf_scroll_target_idx']`, triggers `st.rerun()`, and the PDF viewer re-mounts with `scroll_to_annotation=<idx>` — the embedded PDF scrolls right to the clicked bbox. Switching to a new finding resets the target to 1. |
| Annotation palette | Single yellow bbox per finding. | Unified annotations list per finding: narrative bbox in **amber** (`rgba(255,179,0,0.95)`), per-metric bboxes in **blue** (`rgba(0,119,204,0.95)`). All visible at once on the embedded PDF so the analyst can see numerator + denominator highlighted on the same page. |
| Files touched | n/a | `src/detectors/priority_scorer.py`, `src/detectors/stress_test_prompts.py`, `validation/run_real_report.py`, `dashboard/index.html`, `dashboard/dashboard.js`, `dashboard/dashboard.css`, `app/streamlit_app.py`. Pipeline re-ran clean; zero `"zh"` strings in `outputs/`. |

Why this matters in the interview: the reviewer can now click **any** datum — the dissonance sentence, a `-6.7%`, a `2 028` SEK figure — and the embedded PDF jumps to the literal row, no copy-pasting page numbers and no re-opening the PDF in a separate tab.

## v6 changes (per-number provenance + label clean-up)

| Area | v5 | Now (v6) |
| --- | --- | --- |
| Headlines reference numeric metrics (e.g. `(revenue_yoy_pct=-6.7, organic_growth_pct=-6.7)`) but nothing traced where those came from. | Sentence-level snippet only. | Every metric in `metric_alignment` now carries a `metric_evidence` record: `{metric, value, source, locator{page,bbox,matched_str,confidence,context_term}, snippet{path,...}}`. Each value's PDF row is pre-cropped to its own PNG under `outputs/evidence/<company>/metric_<key>.png`. |

## v6 changes (per-number provenance + label clean-up)

| Area | v5 | Now (v6) |
| --- | --- | --- |
| Headlines reference numeric metrics (e.g. `(revenue_yoy_pct=-6.7, organic_growth_pct=-6.7)`) but nothing traced where those came from. | Sentence-level snippet only. | Every metric in `metric_alignment` now carries a `metric_evidence` record: `{metric, value, source, locator{page,bbox,matched_str,confidence,context_term}, snippet{path,...}}`. Each value's PDF row is pre-cropped to its own PNG under `outputs/evidence/<company>/metric_<key>.png`. |
| Metric provenance schema | n/a | Four explicit source tiers: `auto_regex` (extractor matched a known pattern), `manual_curation` (curated `manual_metrics_provenance` block in `validation/companies.json`), `manual_unverified` (best-effort PyMuPDF value-search with same-row context preference), `unverified` (no PDF row located). Each tier renders with a distinct colour badge so the analyst sees confidence at a glance. |
| Extractor refactor | `extract_headline_metrics(text) -> {metric: value}` | `extract_headline_metrics_with_provenance(text) -> ({metric: value}, {metric: {regex_id, raw_match, char_span, snippet_anchor, source}})`. Backwards-compatible shim preserved. Captures *which* regex fired and the full matched substring so PyMuPDF can re-find the row. |
| Number locator | n/a | New `src/ingest/metric_locator.py`: `locate_by_anchor(pdf, anchor)` for auto-extracted matches, `locate_by_value(pdf, value, context_keywords)` for manual overrides. The value-search generates 10+ candidate renderings of the number (incl. Nordic comma decimals, parens-for-negatives, narrative roundings like "decrease of 7%"), iterates from most-specific to least-specific, and scores hits by Manhattan distance to the nearest context keyword on the same page (with a same-row bonus). |
| Headline label | `drama_score` (informal slang) | **`priority_score`** everywhere. File renamed: `src/detectors/drama_scorer.py` → `src/detectors/priority_scorer.py`. Field/function/dashboard-column/footer copy all migrated. Same numeric scale, same per-rule formula — only the label changed. |
| Manual override schema | `manual_metrics: {key: value}` only | `manual_metrics` + optional `manual_metrics_provenance: {key: {anchor, note}}`. Curating an `anchor` for a manual value lets the orchestrator promote it from `manual_unverified` to `manual_curation` (highest-confidence colour). Techstep FY2025 and Qben FY2024 ship with curated anchors as worked examples. |
| Dashboard rendering | "Show evidence" drawer with one snippet | Drawer now also shows a **Per-number provenance** block: one row per metric, with badge, page+confidence meta, and the cropped PNG. Streamlit drill-down adds a matching section using bordered cards + `st.image`. |

Stats from the latest run (Middelborg + benchmarks):

- 25 findings → **25 sentence snippets** + **14 metric snippets** auto-generated.
- The Techstep growth-narrative critical (priority 27.0) now ships with PNGs proving `revenue_yoy_pct = -6.7` and `organic_growth_pct = -6.7` both trace to page 16, the sentence *"Techstep had total revenue of NOK 1 001 million in 2025, a decrease of 7%"*. The mismatch between rounded narrative (-7%) and curated precise value (-6.7%) is preserved in the snippet caption (`note`).
- Total evidence library: ~450 KB / 39 PNGs (avg ~12 KB each). Lazy-loaded in the static dashboard.

Why this matters in the interview: the reviewer can now point at any `-6.7` in any headline and answer "where did this come from?" with a one-click PDF row crop -- not a paragraph, not a page, the actual table row or sentence.

## v5 changes (evidence-snippet grounding)

| Area | v4 | Now (v5) |
| --- | --- | --- |
| Evidence delivery | Inline `<mark>` of the extracted sentence text + jump link | Original-typography PNG snippet cropped from the source PDF using `PyMuPDF`. Static dashboard renders `<img>` (lazy-loaded, ~5–25 KB each); Streamlit drill-down opens with `st.image(...)` as the first thing the analyst sees. |
| Crop module | n/a | [`src/ingest/evidence_snippet.py`](src/ingest/evidence_snippet.py) — `crop_evidence_snippet(pdf_path, page, bbox)` returns PNG bytes; `write_evidence_snippet(...)` persists to disk. Horizontally expands the bbox to the full text column so the cropped row reads naturally. Deterministic 12-char SHA256 keys keep filenames stable across runs. |
| Pre-generation | n/a | The orchestrator writes one PNG per finding to `outputs/evidence/<company>/<key>.png` and records `provenance.current.evidence_snippet = {path, width, height, size_bytes, page, bbox, key}`. Snippet metadata propagates into every leaderboard row, so neither dashboard re-reads the source PDFs at render time. |
| Narrative findings evidence | "growth mentioned 57 times" with no anchor | `narrative_dissonance` now records the **first sentence** in which any family keyword appears (`claim_excerpt` + `claim_excerpt_matched_term`), and the pipeline crops that sentence as a snippet. So every dissonance row in the dashboard can show "the actual sentence I'm complaining about". |
| Static dashboard drawer | Sentence-context `<mark>` only | `<figure class="evidence-snippet"><img ../><figcaption>…</figcaption></figure>` rendered above the sentence-context block. Button label flips from "Read in context" to "Show evidence" when a snippet is available. |
| Streamlit panel | `streamlit-pdf-viewer` only | `st.image(snippet)` as first-screen evidence + a collapsed expander still hosting the full PDF + bbox highlight for deep dives. The leaderboard now has a "Crop" column flagging which rows ship a pre-cropped snippet. |

Stats from the latest run (Middelborg + benchmarks):

- 25 total findings → **11 evidence snippets** auto-generated (every PDF-sourced finding with an excerpt got one).
- Total evidence library: **~220 KB across 11 PNGs** (avg ~20 KB each).
- The remaining 14 findings either come from TXT-only sources (Otovo / Norwegian Air / Norse Atlantic / Mowi) or are reclassification-only `selective_disclosure` rows that don't have a single anchor sentence.

Why this matters in the interview: instead of "the system thinks Qben's tone doesn't match the numbers", you can point at a 1095×59 PNG that is *literally the row from page 4 of the year-end report* — same typography, same justification, same SEK figures. The priority score remains a ranking heuristic, but the evidence is now indisputably the publisher's own bytes.

## v4 changes (sentence-level locator + highlight)

| Area | v3 | Now (v4) |
| --- | --- | --- |
| PDF locator granularity | page-only (`pdf_page_hit`) | sentence-level: [`src/ingest/excerpt_locator.py`](src/ingest/excerpt_locator.py) uses `pdfplumber` word-level extraction to find the exact bbox of the matched excerpt and the sentence-context window (previous + matched + next sentence). |
| Issuer click-through | `<url>#page=N` | `<url>#page=N&search=<normalised phrase>` so Chrome / Firefox built-in PDF viewers jump to the page *and* highlight the phrase. Exposed as `issuer_url_at_phrase`, `github_blob_url_at_phrase`, `github_raw_url_at_phrase` in every finding's `provenance.current`. |
| Static dashboard | "Show excerpt" toggle | "Read in context" drawer shows `prev sentence · MATCHED · next sentence` with `<mark>` highlighting; jump button label becomes "Issuer PDF (jump+highlight)" when a search phrase is available. |
| Streamlit dashboard | page-pinned PDF embed | `streamlit-pdf-viewer` now receives `annotations=[{page, x, y, width, height, color}]` derived from the pdfplumber bbox and `scroll_to_annotation=1`, so the matched sentence is highlighted in a yellow box inside the inline PDF panel. Drill-down card also renders the sentence-context window with `<mark>` highlighting. |
| Provenance schema | `pdf_page_hit` | new `provenance.current.excerpt_locator = {page, bbox{x0,top,x1,bottom,width,height}, sentence_context{prev,match,next}, normalised_search}`; coarse `pdf_page_hit` kept for backwards compatibility. |

Smoke test (Qben FY 2024, `lag_causality` finding):

```json
"excerpt_locator": {
  "page": 4,
  "bbox": {"x0": 42.6, "top": 323.65, "x1": 372.0, "bottom": 332.65, ...},
  "sentence_context": {
    "prev": "Fourth quarter of 2024 ... Net sales for the fourth quarter totalled SEK 292 million (235), up 24 per cent.",
    "match": "The increase is attributable primarily to the consolidation of ININ from late November as well as completed acquisitions.",
    "next": "EBITA for the fourth quarter was SEK -114 million ..."
  },
  "normalised_search": "increase is attributable primarily to the consolidation of"
}
```

The issuer URL emitted for that finding is:

```
https://www.qben.se/.../Qben-Infra-Year-End-report-2024.pdf#page=4&search=increase%20is%20attributable%20primarily%20to%20the%20consolidation%20of
```

Open it in Chrome and the PDF viewer jumps to page 4 with the phrase highlighted in yellow.

## v3 changes (git-anchored provenance + click-through dashboard)

| Area | Before | Now |
| --- | --- | --- |
| Provenance | Curated `data/sources.json` with URLs only | Every entry now records `sha256` of the local PDF/TXT, the latest `git_sha` that touched the file, and pre-built `github_blob_url` / `github_raw_url` / `github_head_url`. Regeneration script: [`scripts/refresh_provenance.py`](scripts/refresh_provenance.py). |
| Per-finding traceability | `source_used` was a single string | Each finding (and each dashboard / leaderboard row) carries a full `provenance` block: `issuer_url`, `github_blob_url`, `github_head_url`, `local_path`, `sha256`, `git_sha`, and best-effort `pdf_page_hit`. For findings that quote a sentence (`lag_causality`), the orchestrator re-extracts the PDF page-by-page and stamps the page where the excerpt was located, so the issuer URL becomes `<url>#page=N`. |
| Visualization | None (raw JSON only) | Static SPA in [`dashboard/`](dashboard/) (vanilla JS, no build step) and a richer Streamlit app in [`app/streamlit_app.py`](app/streamlit_app.py). Both read the same `outputs/*leaderboard.json` payloads, so they always agree. |
| Click-through | n/a | Every leaderboard row exposes three buttons: open issuer PDF at the matched page, jump to the immutable GitHub commit permalink, or open the local PDF via `file://`. EN/中文 headline toggle and per-row excerpt drawer included. |
| Repo provenance | Untracked working directory | New `scripts/init_repo.sh` bootstraps `git init` + `git-lfs` for `*.pdf`, optional `gh repo create` for publishing as `<owner>/investment-redflag-scanner`. |
| Publication | n/a | New `.github/workflows/pages.yml` publishes the dashboard + outputs + sources.json to GitHub Pages on every push to `main`. |

## v3 quick start

```bash
cd qben_redflag_scanner

# one-time bootstrap (sets the github_owner in config/provenance.json first)
bash scripts/init_repo.sh                # local-only
bash scripts/init_repo.sh --remote       # also pushes to github

# regenerate provenance + run all detectors
.venv/bin/python scripts/refresh_provenance.py
.venv/bin/python validation/run_real_report.py

# local visualization (open one of the two)
.venv/bin/python -m http.server 8765     # then open http://localhost:8765/dashboard/
.venv/bin/streamlit run app/streamlit_app.py

# publish: push to main and GitHub Pages auto-deploys
git add -A && git commit -m "refresh" && git push
# public URL: https://<owner>.github.io/investment-redflag-scanner/
```

> The static dashboard uses `fetch()` to load JSON, which browsers block on the `file://` protocol. Always serve `dashboard/` via a tiny http server (or GitHub Pages).

## v2 changes (kept for context)

| Area | Before | Now |
| --- | --- | --- |
| Leverage / coverage extraction | Manual only | Regex extractor in `src/ingest/metric_extractor.py` auto-extracts `net_debt`, `interest_expense`, `EBITDA`, derives `interest_coverage_ratio` / `net_debt_to_ebitda`; only promoted into the metric set when the derived value falls inside a sanity band, otherwise the curated `manual_metrics` are kept. Every report payload now carries a `metrics_audit` block (auto vs manual side-by-side). |
| Prior-period coverage | Qben only (Q2 2025 vs Q4 2024) | All five Middelborg-linked targets now have a prior reference; `selective_disclosure` fires on Qben, ININ, Techstep, with K33 also wired in (FY2024 vs Arcario FY2023) and North Energy (FY2025 vs FY2024). |
| Source provenance | TXT-only, no URLs | Every report now has a `source_url` + local PDF and is logged in `data/sources.json`. PDFs are downloaded into `data/raw/<co>/<period>/*.pdf` and parsed by `pypdf`; the orchestrator prefers the PDF when its extracted text exceeds 5k chars and otherwise falls back to the curated TXT. |
| `lag_causality` false positives | Mechanical IFRS consolidation looked like a "0-quarter synergy" warning | New guardrail in `src/detectors/lag_causality.py` recognises "fully consolidated as of", "consolidated into / from", "purchase price allocation", "IFRS 3", "business combination", "first-time consolidation" etc., flips the verdict to `mechanical_consolidation_effect`, downgrades severity to `info`, and rewrites the headline so a reviewer immediately sees it is an accounting effect, not a synergy promise. |
| Priority score positioning | Implicit "score" with no caveat | Explicit "ranking heuristic, not a p-value" disclaimer below, and the report payload carries the inputs so anyone can re-derive it. |

## Middelborg-only eye-catching snapshot

Run (full set, refreshes both global + Middelborg artefacts):
```bash
pip install -r requirements.txt
python3 validation/run_real_report.py
```

Middelborg-only artifacts:

- `outputs/middelborg_validation_summary.json`
- `outputs/middelborg_dashboard_payload.json`
- `outputs/middelborg_leaderboard.json`

Non-Middelborg companies are isolated in `NON_MIDDELBORG_RESULTS.md` and the parallel non-Middelborg sections of `leaderboard.json`.

Current Middelborg scope in pipeline:

- `qben_infra`
- `qben_infra_2024`
- `inin_group`
- `techstep`
- `k33`
- `north_energy`

Top 8 attention-grabbing findings (Middelborg-only, generated 2026-05-24):

| Rank | Company | Severity | Priority | Headline |
| ---: | --- | --- | ---: | --- |
| 1 | Techstep ASA (FY 2025) | critical | 27.0 | "growth" narrative cited 57 times, but supporting metrics move opposite (`revenue_yoy_pct=-6.7`, `organic_growth_pct=-6.7`). |
| 2 | Qben Infra AB (FY 2024) | critical | 19.8 | "growth" narrative cited 34 times, but supporting metrics move opposite (`revenue_yoy_pct=-2.0`). |
| 3 | Qben Infra AB (Q2 2025 vs Q4 2024 YE) | critical | 10.5 | New `continuing_operations` framing introduced in Q2 2025, absent in Q4 2024 YE. |
| 4 | ININ Group AS (FY 2025 vs Q3 2024 Interim) | critical | 10.5 | New `continuing_operations` framing introduced in FY 2025, absent in Q3 2024 Interim. |
| 5 | ININ Group AS | critical | 10.5 | New `held_for_sale` framing introduced in FY 2025. |
| 6 | ININ Group AS | critical | 10.5 | New `discontinued_operations` framing introduced in FY 2025. |
| 7 | Techstep ASA | critical | 10.5 | New `held_for_sale` framing introduced in FY 2025. |
| 8 | Techstep ASA | critical | 10.5 | New `discontinued_operations` framing introduced in FY 2025. |
| 9 | Techstep ASA | critical |  9.0 | "resilience" narrative conflicts with leverage/coverage (`interest_coverage_ratio=1.1`, `net_debt_to_ebitda=3.9`). |
| 10 | Qben Infra AB (FY 2024) | info |  1.0 | "acquisition_completed" impact attributed to current period is **mechanical IFRS consolidation**, not synergy. *Downgraded from warning by v2 guardrail.* |

Interpretation note: rows 3 to 8 are the same family of attack — every Middelborg-linked Nordic issuer has just introduced a fresh "continuing operations / held for sale / discontinued operations" frame in their newest report. That is *exactly* the selective-disclosure attack surface the pipeline was designed to detect, and it now lights up across the cluster, not just on Qben.

## What was built

Four deterministic detectors (no LLM), pluggable on any Nordic public filer:

| Detector | Source | What it does |
| --- | --- | --- |
| `lag_causality` | `src/detectors/lag_causality.py` | Flags management claims where the asserted action-to-impact lag is shorter than the realistic business transmission window. v2 adds an IFRS-3 / consolidation guardrail so mechanical accounting inclusion is *not* counted as a synergy promise. |
| `narrative_dissonance` | `src/detectors/narrative_dissonance.py` | Counts narrative keyword families (growth, efficiency, resilience, discipline, demand) and compares against direction-correct supporting metrics (`expected_direction` + `neutral_band` for high-bad metrics like leverage). |
| `selective_disclosure` | `src/detectors/selective_disclosure.py` | Compares two periods of the same issuer. Flags KPIs disclosed last period and missing this period, plus newly introduced reclassification frames (`continuing_operations`, `held_for_sale`, `discontinued_operations`). |
| `stress_test_prompts` | `src/detectors/stress_test_prompts.py` | Attaches bilingual EN+ZH follow-up questions to every red flag across five axes: cause, cost trade-off, peer benchmarking, cash flow cross-check, disclosure completeness. |

Pipeline + outputs:

- Real public filings ingested into `data/raw/<company>/<year>/`, PDF preferred when parseable, curated TXT as fallback (`src/ingest/pdf_to_text.py`).
- One JSON per company: `outputs/companies/<company>/report.json` + `follow_ups.json` — now also includes `source_used`, `auto_extracted_metrics`, `manual_metrics`, and `metrics_audit`.
- Aggregated `outputs/validation_summary.json` and `outputs/dashboard_payload.json` for visualisation, plus Middelborg-only mirrors.

## Priority score — methodology and disclosure

> Priority score is a **deterministic ranking heuristic, not a p-value or any statistical significance test.** Its only job is to sort findings so the most consequential ones land at the top of the review queue. The inputs that produced each score are saved alongside the finding so the score can be re-derived, debated, or replaced. The label was renamed from the informal `drama_score` in v6.

Per-rule formula (see `src/detectors/priority_scorer.py`):

| Rule | Formula |
| --- | --- |
| `narrative_dissonance` | `severity_weight * (1 + min(mention_count, 50) / 10) * (0.5 + abs(alignment_score))` |
| `selective_disclosure` (reclassification) | `severity_weight * 3.5` |
| `selective_disclosure` (KPI drop) | `severity_weight * (1 + previous_emphasis_score) * (0.5 + kpi_weight)` |
| `lag_causality` | `severity_weight * (1 + max(0, min_lag_required - observed_lag))` |
| Severity weights | `critical=3.0, warning=1.5, info=0.5` |

What to say in an interview: "Priority score is a deterministic ranking key. It sorts findings; it does not prove them. Each finding stores the underlying counts, alignment scores, and severity inputs so the order can be re-computed or replaced with a different weighting scheme."

## Source registry (every PDF / URL we actually used)

Machine-readable: `data/sources.json`.

| Company | Period | Local PDF | Source URL |
| --- | --- | --- | --- |
| Qben Infra AB | Q4 / Year-end report 2024 (EN) | [`data/raw/qben_infra/2024/qben_infra_year_end_2024.pdf`](data/raw/qben_infra/2024/qben_infra_year_end_2024.pdf) | https://www.qben.se/en/wp-content/uploads/sites/2/2025/02/Qben-Infra-Year-End-report-2024.pdf |
| Qben Infra AB | Full Annual Report 2024 (SV) | [`data/raw/qben_infra/2024_annual/qben_infra_annual_2024_se.pdf`](data/raw/qben_infra/2024_annual/qben_infra_annual_2024_se.pdf) | https://storage.mfn.se/4bd04418-dc79-464a-9ff0-bfde37b7fb50/qben-infra-arsredovisning-2024.pdf |
| Qben Infra AB | Q2 2025 interim | [`data/raw/qben_infra/2025/qben_infra_q2_2025.pdf`](data/raw/qben_infra/2025/qben_infra_q2_2025.pdf) | https://storage.mfn.se/a331fe1a-d7b7-4ca9-9fa9-c3e59f4a7812/qben-infras-q2-report-2025.pdf |
| Techstep ASA | Annual Report 2025 | [`data/raw/techstep/2025/techstep_annual_2025.pdf`](data/raw/techstep/2025/techstep_annual_2025.pdf) | https://storage.mfn.se/c/aHR0cHM6Ly9hcGkzLm9zbG8ub3Nsb2JvcnMubm8vdjEvbmV3c3JlYWRlci9hdHRhY2htZW50P21lc3NhZ2VJZD02NzE5MzAmYXR0YWNobWVudElkPTMyNDU1Mw/techstep_asa_annual_report_2025.pdf |
| Techstep ASA | Annual Report 2024 | [`data/raw/techstep/2024/techstep_annual_2024.pdf`](data/raw/techstep/2024/techstep_annual_2024.pdf) | https://mb.cision.com/Main/16587/4143193/3420605.pdf |
| ININ Group AS | Annual Report 2025 | [`data/raw/inin_group/2025/inin_group_annual_2025.pdf`](data/raw/inin_group/2025/inin_group_annual_2025.pdf) | https://storage.mfn.se/c/aHR0cHM6Ly9hcGkzLm9zbG8ub3Nsb2JvcnMubm8vdjEvbmV3c3JlYWRlci9hdHRhY2htZW50P21lc3NhZ2VJZD02NzI5MjImYXR0YWNobWVudElkPTMyNTQ1NQ/inin_2025-arsrapport_inkl.pdf |
| ININ Group AS | Q3 2024 Interim Financial Report | [`data/raw/inin_group/2024/inin_q3_2024_report.pdf`](data/raw/inin_group/2024/inin_q3_2024_report.pdf) | https://inin.no/wp-content/uploads/2024/10/Inin-Group-Q3.2024-Interim-Financial-Report_27.10.24_1-signert.pdf |
| K33 AB (publ) | Annual Report 2024 (English) | [`data/raw/k33/2024/k33_annual_2024_en.pdf`](data/raw/k33/2024/k33_annual_2024_en.pdf) | https://cdn.prod.website-files.com/645396e8d92c58b99155faf3/685eb3bd8b923b5906bc7701_ENG%20Annual%20Report%20K33%20AB%20(publ).pdf |
| K33 AB (publ) | Annual Report 2023 (Arcario) | [`data/raw/k33/2023/k33_arcario_annual_2023.pdf`](data/raw/k33/2023/k33_arcario_annual_2023.pdf) | https://storage.mfn.se/ae46f711-8d09-48d2-8452-2337ee50ff40/arcario-ab-arsredovisning-2023-12-31.pdf |
| North Energy ASA | Annual Report 2025 | [`data/raw/north_energy/2025/north_energy_annual_2025.pdf`](data/raw/north_energy/2025/north_energy_annual_2025.pdf) | https://live.euronext.com/sites/default/files/company_press_releases/attachments_oslo/2026/03/23/669041_North%20Energy%20annual%20report%202025.pdf |
| North Energy ASA | Annual Report 2024 | [`data/raw/north_energy/2024/north_energy_annual_2024.pdf`](data/raw/north_energy/2024/north_energy_annual_2024.pdf) | https://live.euronext.com/sites/default/files/company_press_releases/attachments_oslo/2025/03/19/641596_North%20Energy%20annual%20report%202024.pdf |

Non-Middelborg sources (Otovo, Norwegian Air, Norse Atlantic, Mowi) are listed in `NON_MIDDELBORG_RESULTS.md` and `data/sources.json`.

## Known limitations and how each was addressed

| Limitation | Status | Resolution |
| --- | --- | --- |
| `manual_metrics` (esp. K33 / North Energy leverage and coverage) were hand-typed | partially addressed | `extract_leverage_metrics` now derives them from raw text; the orchestrator records a `metrics_audit` block (auto vs manual) and only auto-promotes values that fall inside a sanity band. For a few issuers the regex picks up balance-sheet rows instead of P&L rows, so the curated value is still preferred — exposed transparently. |
| Priority score is a heuristic, not a p-value | fully addressed | Explicit disclosure block above; formula documented per detector; inputs preserved in the per-finding JSON. (Renamed from `drama_score` in v6 for analyst-neutral terminology.) |
| Prior-period comparisons existed only for Qben | fully addressed | Techstep FY2024, ININ Q3 2024 Interim, K33/Arcario FY2023, and North Energy FY2024 are now wired in. `selective_disclosure` now fires across all five Middelborg-linked targets. |
| `lag_causality` flagged IFRS consolidation as "synergy too fast" | fully addressed | New guardrail in `src/detectors/lag_causality.py`; matching sentences are tagged `consolidation_caveat=true`, verdict becomes `mechanical_consolidation_effect`, severity drops to `info`, headline rewritten so the reader sees "accounting effect, not synergy". |
| TXT-only ingestion felt unauditable | fully addressed | All Middelborg-linked targets are mirrored as PDFs under `data/raw/<co>/<period>/*.pdf`; `pypdf` extracts text in-pipeline; `data/sources.json` documents every URL. |

## Why this still works as the application angle

- Targets the employer's actual core holding (Middelborg Invest AS acquired Qben Infra stake on 22 October 2025), not a generic toy dataset.
- Same v1 architecture, but the audit story is now *intellectually honest* — flagged findings come with their source PDFs, their methodology disclosure, and an explicit guardrail for the most obvious false-positive class (IFRS consolidation).
- Architected to scale: add a new Norwegian transparent issuer by dropping a PDF into `data/raw/<company>/<year>/`, registering a stub in `validation/companies.json` (with `primary_pdf` + `source_url`), and re-running the orchestrator.

## Suggested next iterations

1. Wire `stress_test_prompts` into Anthropic Claude API for adversarial re-examination of each red flag (semantic attack pass on top of the deterministic pass).
2. Replace the leverage regex with a small calibration / table-extraction pass (e.g. Camelot on the financial highlights page) so K33 / North Energy auto-extract gets to manual-quality.
3. Add a related-party network detector (entity extraction across the Middelborg co-investor cluster: Songa Investments, Tigerstaden Marine, Gimle Invest).
4. Add segment-level KPI alignment (e.g. Qben Rail vs Qben Power vs Qben Construction vs Qben TIC).
5. Add cash-conversion bridging detector (NI vs OCF gap decomposition into A/R, inventory, accruals).
6. Build a Streamlit / Plotly dashboard reading `outputs/dashboard_payload.json`.
