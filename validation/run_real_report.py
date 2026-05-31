"""End-to-end orchestrator: load real reports, run 4 detectors, write outputs.

Usage:
  python3 validation/run_real_report.py                  # run all companies
  python3 validation/run_real_report.py --company qben_infra

v3: every finding now carries a `provenance` block linking back to the issuer
URL, the immutable GitHub commit permalink, the local file, and (best-effort)
the PDF page where the excerpt was found.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT_ROOT)

from src.ingest.pdf_to_text import load_text_or_pdf
from src.ingest.metric_extractor import extract_headline_metrics_with_provenance
from src.ingest.excerpt_locator import locate_excerpt
from src.ingest.evidence_snippet import (
    crop_evidence_snippet,
    evidence_key,
    png_dimensions,
)
from src.ingest.metric_locator import (
    locate_by_anchor,
    locate_by_value,
    metric_to_context_keywords,
)
from src.detectors.lag_causality import detect_lag_breaks
from src.detectors.narrative_dissonance import detect_narrative_dissonance
from src.detectors.selective_disclosure import detect_disclosure_drops
from src.detectors.stress_test_prompts import attach_follow_ups_to_all
from src.detectors.priority_scorer import enrich_with_priority, top_headlines
from src.detectors.external_collision import detect as detect_external_collision

# v9 triangulation: hypotheses (claims) are first-class; each external
# data source is a tap that emits standardised EvidenceEntry rows.
from src.triangulation.ledger import LedgerStore
from src.triangulation.engine import derive_all
from src.triangulation.runner import (
    run_taps_for_hypotheses,
    state_to_finding,
    build_triangulation_matrix,
    build_audit_roadmap,
)
from src.ingest.external.doffin_tap import DoffinTap
from src.ingest.external.brreg_tap import BrregTap
from src.ingest.external.ted_tap import TedTap
from src.ingest.external.derived_taps import (
    RevenueSupportCalculatorTap,
    ExplanatorySlippageScannerTap,
)
# v10 visualization
from src.visualization.argument_tree import build_argument_tree
from src.visualization.narrative_writer import write_paragraph
from src.visualization.sankey_builder import build_sankey
from validation.report_card import write_company_report


COMPANIES_PATH = os.path.join(HERE, "companies.json")
HYPOTHESES_PATH = os.path.join(HERE, "hypotheses.json")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "outputs")
SOURCES_PATH = os.path.join(PROJECT_ROOT, "data", "sources.json")
PROVENANCE_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "provenance.json")
LEDGER_DIR = os.path.join(PROJECT_ROOT, "data", "ledger")
EXTERNAL_CACHE_ROOT = os.path.join(PROJECT_ROOT, "data", "external")

_MIN_PDF_CHARS_TO_PREFER = 5000


def _abs(path):
    return os.path.join(PROJECT_ROOT, path)


def _load_companies():
    with open(COMPANIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_json_safe(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _index_sources(registry):
    """Map every local file path (relative to PROJECT_ROOT) to its source entry."""
    if not registry:
        return {}
    by_path = {}
    for entry in registry.get("sources", []):
        for key in ("local_pdf", "local_txt"):
            p = entry.get(key)
            if p:
                by_path[os.path.normpath(p)] = entry
    return by_path


def _provenance_for_file(rel_path, sources_index, sources_registry, prov_cfg):
    """Assemble a provenance block for a single local file path."""
    if not rel_path:
        return None
    norm = os.path.normpath(rel_path)
    entry = sources_index.get(norm) or {}
    file_kind = "local_pdf" if norm.lower().endswith(".pdf") else "local_txt"
    file_prov = (entry.get("provenance") or {}).get(file_kind, {})
    head_sha = None
    if sources_registry and isinstance(sources_registry.get("provenance_config"), dict):
        head_sha = sources_registry["provenance_config"].get("head_sha")
    block = {
        "local_path": norm,
        "kind": file_kind,
        "issuer_url": entry.get("url"),
        "issuer_page": entry.get("issuer_page"),
        "language": entry.get("language"),
        "label": entry.get("label"),
        "period": entry.get("period"),
        "sha256": file_prov.get("sha256"),
        "git_sha": file_prov.get("git_sha"),
        "github_blob_url": file_prov.get("github_blob_url"),
        "github_raw_url": file_prov.get("github_raw_url"),
        "github_head_url": file_prov.get("github_head_url"),
        "repo_head_sha": head_sha,
    }
    if prov_cfg:
        block["owner"] = prov_cfg.get("github_owner")
        block["repo"] = prov_cfg.get("github_repo")
    return block


_PAGE_MARKER = re.compile(r"<<<PAGE (\d+)>>>")


def _split_pages(text):
    """Return list of (page_no, page_text). Assumes pdf_to_text page markers.
    Returns [] for plain-text inputs that lack markers."""
    if "<<<PAGE" not in text:
        return []
    pages = []
    parts = _PAGE_MARKER.split(text)
    # parts: ['', '1', '...page1...', '2', '...page2...', ...]
    for i in range(1, len(parts), 2):
        try:
            page_no = int(parts[i])
        except ValueError:
            continue
        page_text = parts[i + 1] if i + 1 < len(parts) else ""
        pages.append((page_no, page_text))
    return pages


def _locate_excerpt_page(pages, excerpt):
    """Return the first page number whose text contains a normalised fragment of
    `excerpt`. None when not found or when `pages` is empty."""
    if not pages or not excerpt:
        return None
    fragment = " ".join(excerpt.split())[:80].lower()
    if len(fragment) < 12:
        return None
    for page_no, page_text in pages:
        if fragment in " ".join(page_text.split()).lower():
            return page_no
    return None


def _build_metric_evidence(
    finding,
    company_id,
    current_pdf_path,
    auto_metrics_provenance,
    manual_metrics,
    manual_metrics_provenance,
):
    """For each metric referenced by `finding`, attach per-number provenance.

    Resolution order for each metric:
      1. User-supplied manual_metrics_provenance[metric] (page + anchor)
         -> locate_by_anchor, mark source=manual_curation
      2. Auto-extracted regex provenance with snippet_anchor
         -> locate_by_anchor, mark source=auto_regex
      3. Manual override value (no provenance metadata) + PDF
         -> locate_by_value with relax_context fallback, mark source=manual_unverified
      4. Otherwise -> source=unverified (no snippet)

    Each resolved entry gets a pre-cropped PNG written under
    outputs/evidence/<company_id>/metric_<key>.png.
    """
    if not finding.get("metric_alignment"):
        return []
    out = []
    for spec in finding["metric_alignment"]:
        mk = spec.get("metric")
        value = spec.get("value")
        if value is None:
            continue
        entry = {
            "metric": mk,
            "value": value,
            "expected_direction": spec.get("expected_direction"),
            "score": spec.get("score"),
            "source": "unverified",
            "locator": None,
            "snippet": None,
            "note": None,
        }

        man_prov = manual_metrics_provenance.get(mk) if manual_metrics_provenance else None
        auto_prov = auto_metrics_provenance.get(mk) if auto_metrics_provenance else None
        is_manual = mk in (manual_metrics or {}) and manual_metrics[mk] is not None

        locator = None
        if man_prov and current_pdf_path and current_pdf_path.lower().endswith(".pdf"):
            anchor = man_prov.get("anchor")
            if anchor:
                locator = locate_by_anchor(current_pdf_path, anchor)
                if locator:
                    entry["source"] = "manual_curation"
                    entry["note"] = man_prov.get("note")
        if locator is None and auto_prov and current_pdf_path and current_pdf_path.lower().endswith(".pdf"):
            anchor = auto_prov.get("snippet_anchor")
            if anchor:
                locator = locate_by_anchor(current_pdf_path, anchor)
                if locator:
                    entry["source"] = "auto_regex"
                    entry["regex_id"] = auto_prov.get("regex_id")
                    entry["note"] = auto_prov.get("raw_match")
        if locator is None and is_manual and current_pdf_path and current_pdf_path.lower().endswith(".pdf"):
            keywords = metric_to_context_keywords(mk)
            locator = locate_by_value(current_pdf_path, value, keywords, require_context=False)
            if locator:
                entry["source"] = "manual_unverified"
                entry["note"] = (
                    "Manual override; auto-locator best-effort match. "
                    "Verify against the cropped snippet."
                )

        entry["locator"] = locator
        if locator and locator.get("bbox") and locator.get("page"):
            key = evidence_key(
                company_id,
                f"{finding.get('rule_id', 'rule')}__metric__{mk}",
                locator["page"],
                locator["bbox"],
                str(value),
            )
            rel_path = os.path.join("outputs", "evidence", company_id, f"metric_{key}.png")
            abs_path = os.path.join(PROJECT_ROOT, rel_path)
            png_bytes = crop_evidence_snippet(current_pdf_path, locator["page"], locator["bbox"])
            if png_bytes:
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "wb") as f:
                    f.write(png_bytes)
                dims = png_dimensions(png_bytes)
                entry["snippet"] = {
                    "path": rel_path,
                    "width": dims[0] if dims else None,
                    "height": dims[1] if dims else None,
                    "size_bytes": len(png_bytes),
                    "page": locator["page"],
                    "bbox": locator["bbox"],
                    "matched_str": locator.get("matched_str"),
                    "confidence": locator.get("confidence"),
                    "context_term": locator.get("context_term"),
                }
        out.append(entry)
    return out


def _decorate_finding_with_provenance(
    finding,
    current_prov,
    prior_prov,
    current_pages,
    current_pdf_path,
    company_id,
    auto_metrics_provenance=None,
    manual_metrics=None,
    manual_metrics_provenance=None,
):
    """Attach a provenance block to a single finding.

    Picks the right source (current vs prior), locates the PDF page that hosts
    the excerpt via two passes:
      1) coarse page-only hit using the <<<PAGE N>>> markers from pdf_to_text
      2) precise word-level bbox + sentence_context via pdfplumber
         (src.ingest.excerpt_locator)
    Falls back to page-only when the PDF is image-only.

    When a bbox is available, also pre-generates an evidence snippet PNG via
    PyMuPDF and writes its relative path into `evidence_snippet.path` so the
    dashboards can `<img>` / `st.image` the original-typography proof.
    """
    excerpt = (
        finding.get("claim_excerpt")
        or finding.get("evidence_excerpt")
        or finding.get("excerpt")
    )
    primary = dict(current_prov) if current_prov else {}
    if excerpt:
        coarse_page = _locate_excerpt_page(current_pages, excerpt)
        precise = None
        if current_pdf_path and current_pdf_path.lower().endswith(".pdf"):
            try:
                precise = locate_excerpt(current_pdf_path, excerpt)
            except Exception:
                precise = None
        page = (precise or {}).get("page") or coarse_page
        if precise:
            primary["excerpt_locator"] = precise
        if page:
            primary["pdf_page_hit"] = page
            search_q = (precise or {}).get("normalised_search")
            for url_key in ("issuer_url", "github_blob_url", "github_raw_url"):
                url = primary.get(url_key)
                if not url:
                    continue
                primary[url_key + "_at_page"] = f"{url}#page={page}"
                if search_q:
                    encoded = quote(search_q)
                    primary[url_key + "_at_phrase"] = (
                        f"{url}#page={page}&search={encoded}"
                    )

        # Evidence snippet (pre-generated PNG) -- only when we have an actual
        # bbox + PDF source. Skip silently for image-only PDFs / TXT inputs.
        bbox = (precise or {}).get("bbox")
        if bbox and page and current_pdf_path and current_pdf_path.lower().endswith(".pdf"):
            key = evidence_key(company_id, finding.get("rule_id", "rule"), page, bbox, excerpt)
            rel_path = os.path.join("outputs", "evidence", company_id, f"{key}.png")
            abs_path = os.path.join(PROJECT_ROOT, rel_path)
            png_bytes = crop_evidence_snippet(current_pdf_path, page, bbox)
            if png_bytes:
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "wb") as f:
                    f.write(png_bytes)
                dims = png_dimensions(png_bytes)
                primary["evidence_snippet"] = {
                    "path": rel_path,
                    "width": dims[0] if dims else None,
                    "height": dims[1] if dims else None,
                    "size_bytes": len(png_bytes),
                    "page": page,
                    "bbox": bbox,
                    "key": key,
                }
    prov_block = {"current": primary}
    if prior_prov:
        prov_block["prior"] = prior_prov
    finding["provenance"] = prov_block

    # Per-number provenance for every metric this finding cites.
    metric_evidence = _build_metric_evidence(
        finding,
        company_id,
        current_pdf_path,
        auto_metrics_provenance or {},
        manual_metrics or {},
        manual_metrics_provenance or {},
    )
    if metric_evidence:
        finding["metric_evidence"] = metric_evidence
    return finding


def _resolve_source(company_cfg, report_block):
    """Return the actual file path used.

    Prefer the local PDF when it has enough extracted text; otherwise fall back to
    the curated TXT (some Q-report PDFs are image-only and pypdf returns only
    boilerplate, in which case the human-cleaned TXT is more reliable).
    """
    pdf_path = report_block.get("primary_pdf")
    txt_path = report_block.get("primary_file")
    if pdf_path and os.path.isfile(_abs(pdf_path)):
        try:
            text = load_text_or_pdf(_abs(pdf_path))
            if len(text) >= _MIN_PDF_CHARS_TO_PREFER:
                return _abs(pdf_path), "pdf"
        except Exception:
            pass
    if txt_path and os.path.isfile(_abs(txt_path)):
        return _abs(txt_path), "txt"
    if pdf_path:
        return _abs(pdf_path), "pdf"
    raise FileNotFoundError(
        f"No usable source for {company_cfg['id']} - tried PDF and TXT"
    )


def _metrics_audit(auto, manual):
    """Side-by-side audit of auto vs manual metrics. Diff and source flags."""
    keys = set(auto.keys()) | set(manual.keys())
    audit = {}
    for k in sorted(keys):
        a = auto.get(k)
        m = manual.get(k)
        if a is None and m is None:
            continue
        if k.endswith("_source") or k.endswith("_raw"):
            continue
        chosen = m if m is not None else a
        agree = None
        if a is not None and m is not None:
            try:
                agree = abs(float(a) - float(m)) / max(abs(float(m)), 1e-6) < 0.10
            except (TypeError, ValueError):
                agree = None
        audit[k] = {
            "auto": a,
            "manual": m,
            "chosen": chosen,
            "source": "manual" if m is not None else "auto",
            "agrees_within_10pct": agree
        }
    return audit


def _run_single(company_cfg, sources_index, sources_registry, prov_cfg, *, triangulated_findings=None):
    current = company_cfg["current_report"]
    prior = company_cfg.get("prior_report")

    current_path, current_kind = _resolve_source(company_cfg, current)
    current_text = load_text_or_pdf(current_path)
    current_pages = _split_pages(current_text)
    auto_metrics, auto_metrics_provenance = extract_headline_metrics_with_provenance(current_text)
    manual_metrics = current.get("manual_metrics") or {}
    manual_metrics_provenance = current.get("manual_metrics_provenance") or {}
    merged_metrics = {**auto_metrics, **{k: v for k, v in manual_metrics.items() if v is not None}}
    metrics_audit = _metrics_audit(auto_metrics, manual_metrics)

    current_rel = os.path.relpath(current_path, PROJECT_ROOT)
    current_prov = _provenance_for_file(
        current_rel, sources_index, sources_registry, prov_cfg
    )

    findings = []
    findings.extend(detect_lag_breaks(current_text, current["label"]))
    findings.extend(detect_narrative_dissonance(current_text, merged_metrics))
    # v9: triangulated_findings come pre-built from main() via the
    # hypothesis-driven loop. We just append them here so they flow through
    # the same provenance + priority + dashboard pipeline as everything else.
    # The legacy v8 detect_external_collision is still importable for ad-hoc
    # CLI use but no longer fires in the standard pipeline.
    if triangulated_findings:
        findings.extend(triangulated_findings)

    prior_payload = None
    prior_prov = None
    if prior:
        prior_path, prior_kind = _resolve_source(company_cfg, prior)
        prior_text = load_text_or_pdf(prior_path)
        findings.extend(detect_disclosure_drops(
            prior_text, prior["label"],
            current_text, current["label"]
        ))
        prior_rel = os.path.relpath(prior_path, PROJECT_ROOT)
        prior_payload = {
            "label": prior["label"],
            "file": prior_rel,
            "kind": prior_kind
        }
        prior_prov = _provenance_for_file(
            prior_rel, sources_index, sources_registry, prov_cfg
        )

    findings = attach_follow_ups_to_all(findings)
    findings = enrich_with_priority(findings, company_cfg["name"])
    current_pdf_for_locator = current_path if current_kind == "pdf" else None
    for f in findings:
        _decorate_finding_with_provenance(
            f,
            current_prov,
            prior_prov,
            current_pages,
            current_pdf_for_locator,
            company_cfg["id"],
            auto_metrics_provenance=auto_metrics_provenance,
            manual_metrics=manual_metrics,
            manual_metrics_provenance=manual_metrics_provenance,
        )

    payload = {
        "company": company_cfg["id"],
        "name": company_cfg["name"],
        "ticker": company_cfg.get("ticker"),
        "exchange": company_cfg.get("exchange"),
        "middelborg_link": company_cfg.get("middelborg_link"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "current_period": current["label"],
        "prior_period": prior["label"] if prior else None,
        "source_used": {
            "current_file": current_rel,
            "current_kind": current_kind,
            "prior": prior_payload
        },
        "provenance": {
            "current": current_prov,
            "prior": prior_prov
        },
        "auto_extracted_metrics": auto_metrics,
        "auto_metrics_provenance": auto_metrics_provenance,
        "manual_metrics": manual_metrics,
        "manual_metrics_provenance": manual_metrics_provenance,
        "merged_metrics_used": merged_metrics,
        "metrics_audit": metrics_audit,
        "findings": findings
    }
    return payload


def _run_triangulation(targets, *, skip_taps=False):
    """v9: load hypotheses, run all applicable taps, derive states.

    Returns:
      triangulated_by_company: company_id -> [finding dicts]
      states: hypothesis_id -> TriangulationState dict
      hypotheses: list[hypothesis dict]
      matrix: dashboard-renderable matrix dict
      roadmap: ranked next-tap recommendations list
    """
    hyp_doc = _load_json_safe(HYPOTHESES_PATH) or {}
    all_hypotheses = hyp_doc.get("hypotheses") or []
    all_by_id = {h["id"]: h for h in all_hypotheses}
    target_ids = {c["id"] for c in targets}
    # Hypotheses directly attributable to the run scope.
    primary_hyps = [h for h in all_hypotheses if h.get("source_company") in target_ids]
    # v10: pull in peer-control hypotheses too, even if their source_company
    # is outside scope -- the derived_revenue_support tap reads their ledger.
    in_scope_ids = {h["id"] for h in primary_hyps}
    for h in list(primary_hyps):
        for pid in h.get("peer_controls") or []:
            if pid in all_by_id and pid not in in_scope_ids:
                primary_hyps.append(all_by_id[pid])
                in_scope_ids.add(pid)
    hypotheses = primary_hyps
    name_by_id = {c["id"]: c["name"] for c in targets}
    for h in hypotheses:
        h["source_company_name"] = name_by_id.get(
            h["source_company"], h["source_company"]
        )
    if not hypotheses:
        return {}, {}, [], {"tap_kinds": [], "rows": []}, []

    ledger_store = LedgerStore(LEDGER_DIR)
    # v10: two-pass tap orchestration.
    # Pass 1 (primary): API-hitting taps populate the ledger.
    # Pass 2 (derived): synthesizers read pass-1 ledger entries (incl.
    # peer subsidiaries' procurement evidence) to emit cross-tap analyses.
    primary_taps = [DoffinTap(), BrregTap(), TedTap()]
    hyp_by_id = {h["id"]: h for h in hypotheses}
    derived_taps = [
        RevenueSupportCalculatorTap(ledger_store, hyp_by_id),
        ExplanatorySlippageScannerTap(),
    ]
    if not skip_taps:
        log_primary = run_taps_for_hypotheses(
            hypotheses,
            primary_taps,
            ledger_store,
            external_cache_root=Path(EXTERNAL_CACHE_ROOT),
            skip_if_ledger_fresh=False,
        )
        for hid, tap_log in log_primary.items():
            if tap_log:
                print(f"  [v9 primary] {hid}: {', '.join(tap_log)}")
        log_derived = run_taps_for_hypotheses(
            hypotheses,
            derived_taps,
            ledger_store,
            external_cache_root=Path(EXTERNAL_CACHE_ROOT),
            skip_if_ledger_fresh=False,
        )
        for hid, tap_log in log_derived.items():
            if tap_log:
                print(f"  [v10 derived] {hid}: {', '.join(tap_log)}")

    states = derive_all(hypotheses, ledger_store)
    matrix = build_triangulation_matrix(hypotheses, states)
    roadmap = build_audit_roadmap(states, hypotheses)

    triangulated_by_company: dict = {}
    for hyp in hypotheses:
        state = states.get(hyp["id"])
        if not state:
            continue
        finding = state_to_finding(hyp, state)
        triangulated_by_company.setdefault(hyp["source_company"], []).append(finding)
    return triangulated_by_company, states, hypotheses, matrix, roadmap


def build_report_library(companies_cfg, headlines_pool):
    """Build the company -> reports tree consumed by the dashboard
    sidebar. Companies and prior-report controls are pulled from
    companies.json so reports with zero findings still show up. Each
    report node gets a severity breakdown derived from the headlines
    pool (which is what feeds the leaderboard)."""
    # Index findings by (company_id, period). The id + period pair is
    # the only stable join key here: prov.label is normalised by the
    # report loader and routinely differs from the label in
    # companies.json (e.g. "Annual Report 2025" vs "FY 2025"), but
    # prov.period is identical to current_report.period because both
    # are pulled from the same companies.json entry at run start.
    from collections import defaultdict

    findings_by_key = defaultdict(list)
    for f in headlines_pool:
        prov = f.get("provenance") or {}
        company_id = f.get("company") or "?"
        period = prov.get("period") or "?"
        findings_by_key[(company_id, period)].append(f)

    def _sev_buckets(fs):
        out = {"critical": 0, "warning": 0, "info": 0}
        for f in fs:
            sev = f.get("severity")
            if sev in out:
                out[sev] += 1
        return out

    def _match_findings(cid, configured_period):
        """Exact match first, then year prefix fallback because the
        runner sometimes upgrades a bare '2025' from companies.json
        into '2025-FY' / '2025-Q4' / etc. via the report loader.
        Returns (findings, matched_period_strings) - the strings are
        the concrete prov.period values that joined, which JS uses to
        filter the leaderboard."""
        exact = findings_by_key.get((cid, configured_period))
        if exact:
            return exact, [configured_period]
        if not configured_period:
            return [], []
        prefix = str(configured_period) + "-"
        fuzzy = []
        matched_periods = []
        for (fid, fp), fs in findings_by_key.items():
            if fid == cid and fp and fp.startswith(prefix):
                fuzzy.extend(fs)
                matched_periods.append(fp)
        return fuzzy, matched_periods

    companies_out = []
    for cfg in companies_cfg:
        display_name = cfg.get("name") or cfg.get("id")
        company_id = cfg.get("id")
        reports = []
        for role, key in (("current", "current_report"), ("peer_control", "prior_report")):
            entry = cfg.get(key)
            if not entry:
                continue
            period_id = entry.get("period")
            period_label = entry.get("label") or period_id or "?"
            matched, matched_periods = _match_findings(company_id, period_id)
            sev = _sev_buckets(matched)
            reports.append({
                "period": period_label,
                "period_id": period_id,
                # Concrete prov.period strings that joined here. JS
                # filters by `prov.period in matched_periods` because
                # the human label from companies.json (e.g. "Q4 2024")
                # never equals the loader-upgraded period on findings
                # (e.g. "2024-Q4").
                "matched_periods": matched_periods,
                "role": role,
                "finding_count": len(matched),
                "critical": sev["critical"],
                "warning": sev["warning"],
                "info": sev["info"],
                "primary_pdf": entry.get("primary_pdf"),
                "primary_file": entry.get("primary_file"),
                "source_url": entry.get("source_url"),
                "has_findings": len(matched) > 0,
            })
        companies_out.append({
            "id": cfg.get("id"),
            "name": display_name,
            "ticker": cfg.get("ticker"),
            "exchange": cfg.get("exchange"),
            "middelborg_link": bool(cfg.get("middelborg_link")),
            "reports": reports,
            "report_count": len(reports),
            "total_findings": sum(r["finding_count"] for r in reports),
            "total_critical": sum(r["critical"] for r in reports),
        })

    # Note: any orphan finding whose (company_id, period) doesn't
    # appear in companies.json is dropped from the library. The
    # leaderboard still surfaces it. In practice this only happens
    # mid migration; companies.json is the source of truth.

    companies_out.sort(key=lambda c: (
        -c.get("total_critical", 0),
        -c.get("total_findings", 0),
        c.get("name") or "",
    ))

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_version": 1,
        "companies": companies_out,
        "company_count": len(companies_out),
        "report_count": sum(c["report_count"] for c in companies_out),
        "finding_count": sum(c["total_findings"] for c in companies_out),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", help="Run only this company id", default=None)
    parser.add_argument(
        "--skip-external-taps",
        action="store_true",
        help="v9: skip live API calls to Doffin/BRREG and reuse whatever is "
        "already in data/ledger/. Useful for fast iteration on dashboards.",
    )
    args = parser.parse_args()

    cfg = _load_companies()
    targets = cfg["companies"]
    if args.company:
        targets = [c for c in targets if c["id"] == args.company]
        if not targets:
            print(f"Unknown company: {args.company}")
            sys.exit(2)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    sources_registry = _load_json_safe(SOURCES_PATH)
    sources_index = _index_sources(sources_registry)
    prov_cfg = _load_json_safe(PROVENANCE_CONFIG_PATH) or {}

    # v9: run hypothesis loop once across all targets before per-company
    # detector loops.
    triangulated_by_company, states, hypotheses, matrix, roadmap = _run_triangulation(
        targets, skip_taps=args.skip_external_taps
    )
    pages_url = None
    if sources_registry and isinstance(sources_registry.get("provenance_config"), dict):
        pages_url = sources_registry["provenance_config"].get("pages_url")
    aggregate = {"runs": [], "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    dashboard = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages_url": pages_url,
        "categories": {
            "narrative_alignment": [],
            "disclosure_drop": [],
            "reclassification_introduced": [],
            "causality_break": []
        }
    }

    category_route = {
        "narrative_dissonance": "narrative_alignment",
        "selective_disclosure": "disclosure_drop",
        "lag_causality": "causality_break",
        # v9: triangulated_hypothesis findings are bucketed into a new
        # external_collision_v9 category that the static dashboard renders
        # alongside the others.
        "triangulated_hypothesis": "external_collision_v9",
    }
    dashboard["categories"]["external_collision_v9"] = []

    headlines_pool = []

    for company_cfg in targets:
        payload = _run_single(
            company_cfg,
            sources_index,
            sources_registry,
            prov_cfg,
            triangulated_findings=triangulated_by_company.get(company_cfg["id"], []),
        )
        report_path, fu_path = write_company_report(OUTPUT_DIR, company_cfg["id"], payload)
        aggregate["runs"].append({
            "company": company_cfg["id"],
            "findings_count": payload["findings"].__len__(),
            "severity_tally": payload.get("severity_tally") or {},
            "report_path": os.path.relpath(report_path, PROJECT_ROOT),
            "follow_ups_path": os.path.relpath(fu_path, PROJECT_ROOT)
        })

        for f in payload["findings"]:
            if f.get("verdict") == "reclassification_introduced":
                bucket = "reclassification_introduced"
            else:
                bucket = category_route.get(f["rule_id"], "narrative_alignment")
            prov = (f.get("provenance") or {}).get("current") or {}
            row = {
                "company": company_cfg["id"],
                "company_name": company_cfg["name"],
                "severity": f.get("severity"),
                "label": f.get("family") or f.get("kpi_id") or f.get("claim_type") or f.get("entity"),
                "composite_key": (
                    f"{company_cfg['id']}|{f.get('rule_id','?')}|"
                    f"{f.get('hypothesis_id') or (f.get('headline') or '?')[:80]}"
                ),
                "verdict": f.get("verdict"),
                "priority_score": f.get("priority_score"),
                "headline": f.get("headline"),
                "metric_alignment": f.get("metric_alignment"),
                "metric_evidence": f.get("metric_evidence"),
                "lag_quarters": f.get("lag_quarters"),
                "consolidation_caveat": f.get("consolidation_caveat"),
                "claim_excerpt": f.get("claim_excerpt"),
                "provenance": prov,
                "evidence_snippet": prov.get("evidence_snippet"),
                # v9: include the full triangulation block when present so
                # the dashboard "external_collision_v9" bucket can render the
                # per-card matrix + roadmap snippets.
                "triangulation": f.get("triangulation"),
                "hypothesis_id": f.get("hypothesis_id"),
                "entity": f.get("entity"),
                "claim": f.get("claim"),
            }
            dashboard["categories"][bucket].append(row)
            composite_key_for_row = (
                f"{company_cfg['id']}|{f.get('rule_id','?')}|"
                f"{f.get('hypothesis_id') or (f.get('headline') or '?')[:80]}"
            )
            headlines_pool.append({
                "company": company_cfg["id"],
                "company_name": company_cfg["name"],
                "rule_id": f.get("rule_id"),
                "composite_key": composite_key_for_row,
                # v8: subtype distinguishes external_collision flavours
                # (norwegian_subsidiary_organic_vs_awarded today) and
                # narrative_dissonance vs selective_disclosure variants.
                "subtype": f.get("subtype"),
                "severity": f.get("severity"),
                "priority_score": f.get("priority_score"),
                "headline": f.get("headline"),
                "consolidation_caveat": f.get("consolidation_caveat"),
                "claim_excerpt": f.get("claim_excerpt"),
                "metric_evidence": f.get("metric_evidence"),
                # v8 external_collision payload: a list of one record per
                # external source (Doffin today, BRREG/Newsweb later).
                # Dashboards render this into the new "External collision"
                # section. Always include the key so the JS rendering side
                # can branch on its presence rather than guessing.
                "external_evidence": f.get("external_evidence"),
                "subsidiary": f.get("subsidiary"),
                "subsidiary_claimed_role": f.get("subsidiary_claimed_role"),
                "subsidiary_acquired_year": f.get("subsidiary_acquired_year"),
                "verdict": f.get("verdict"),
                # v9: full triangulation state for the dashboards' matrix &
                # roadmap renderings. Carries the latest_per_tap dict, the
                # blockers_for_critical list, the next_recommended_taps list,
                # and the peer_control_status block.
                "triangulation": f.get("triangulation"),
                "hypothesis_id": f.get("hypothesis_id"),
                "entity": f.get("entity"),
                "claim": f.get("claim"),
                "provenance": prov,
                "evidence_snippet": prov.get("evidence_snippet"),
            })

        print(f"[{company_cfg['id']}] {len(payload['findings'])} findings -> {report_path}")

    agg_path = os.path.join(OUTPUT_DIR, "validation_summary.json")
    with open(agg_path, "w", encoding="utf-8") as f:
        json.dump(aggregate, f, ensure_ascii=False, indent=2)
    dash_path = os.path.join(OUTPUT_DIR, "dashboard_payload.json")
    with open(dash_path, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)

    top = top_headlines(headlines_pool, k=15)
    leaderboard_path = os.path.join(OUTPUT_DIR, "leaderboard.json")
    with open(leaderboard_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pages_url": pages_url,
            "top_findings": top
        }, f, ensure_ascii=False, indent=2)

    middelborg_ids = {
        c["id"] for c in targets
        if c.get("middelborg_link")
        and not c["middelborg_link"].lower().startswith("not a middelborg")
    }
    middelborg_headlines = [h for h in headlines_pool if h["company"] in middelborg_ids]
    middelborg_top = top_headlines(middelborg_headlines, k=15)
    middelborg_leaderboard_path = os.path.join(OUTPUT_DIR, "middelborg_leaderboard.json")
    with open(middelborg_leaderboard_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scope": "middelborg_only",
            "pages_url": pages_url,
            "company_ids": sorted(middelborg_ids),
            "top_findings": middelborg_top
        }, f, ensure_ascii=False, indent=2)

    middelborg_summary_path = os.path.join(OUTPUT_DIR, "middelborg_validation_summary.json")
    with open(middelborg_summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scope": "middelborg_only",
            "runs": [r for r in aggregate["runs"] if r["company"] in middelborg_ids]
        }, f, ensure_ascii=False, indent=2)

    middelborg_dashboard = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "middelborg_only",
        "pages_url": pages_url,
        "categories": {
            bucket: [c for c in entries if c["company"] in middelborg_ids]
            for bucket, entries in dashboard["categories"].items()
        }
    }
    middelborg_dashboard_path = os.path.join(OUTPUT_DIR, "middelborg_dashboard_payload.json")
    with open(middelborg_dashboard_path, "w", encoding="utf-8") as f:
        json.dump(middelborg_dashboard, f, ensure_ascii=False, indent=2)

    # v9 outputs: triangulation matrix and audit roadmap for the new
    # dashboard panels. Even when the run is scoped to one company we
    # still emit these so the dashboard can render the full system view.
    matrix_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_version": 1,
        "tap_kinds": matrix["tap_kinds"],
        "rows": matrix["rows"],
    }
    roadmap_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_version": 1,
        "recommended_taps": roadmap,
        "hypothesis_count": len(hypotheses),
    }
    matrix_path = os.path.join(OUTPUT_DIR, "triangulation_matrix.json")
    roadmap_path = os.path.join(OUTPUT_DIR, "audit_roadmap.json")
    with open(matrix_path, "w", encoding="utf-8") as f:
        json.dump(matrix_payload, f, ensure_ascii=False, indent=2)
    with open(roadmap_path, "w", encoding="utf-8") as f:
        json.dump(roadmap_payload, f, ensure_ascii=False, indent=2)
    print(f"Triangulation matrix: {matrix_path}")
    print(f"Audit roadmap: {roadmap_path}")

    # v10 Phase B: build per-finding argument trees + narrative paragraphs
    # keyed by a stable composite id. Static dashboard + Streamlit both load
    # these files.
    arg_trees: dict = {}
    paragraphs: dict = {}
    for item in headlines_pool:
        tree = build_argument_tree(item)
        composite_key = item.get("composite_key") or (
            f"{item.get('company','?')}|{item.get('rule_id','?')}|"
            f"{item.get('hypothesis_id') or item.get('headline','?')[:80]}"
        )
        if tree:
            arg_trees[composite_key] = tree
        paragraphs[composite_key] = write_paragraph(item)

    arg_trees_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_version": 1,
        "trees": arg_trees,
    }
    arg_trees_path = os.path.join(OUTPUT_DIR, "argument_trees.json")
    with open(arg_trees_path, "w", encoding="utf-8") as f:
        json.dump(arg_trees_payload, f, ensure_ascii=False, indent=2)
    print(f"Argument trees: {arg_trees_path} ({len(arg_trees)} trees)")

    paragraphs_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_version": 1,
        "paragraphs": paragraphs,
    }
    paragraphs_path = os.path.join(OUTPUT_DIR, "narrative_paragraphs.json")
    with open(paragraphs_path, "w", encoding="utf-8") as f:
        json.dump(paragraphs_payload, f, ensure_ascii=False, indent=2)
    print(f"Narrative paragraphs: {paragraphs_path} ({len(paragraphs)} paragraphs)")

    # v10 Phase B: top-level Sankey of the global reasoning landscape.
    sankey_data = build_sankey(headlines_pool)
    sankey_payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schema_version": 1,
        "finding_count": len(headlines_pool),
        **sankey_data,
    }
    sankey_path = os.path.join(OUTPUT_DIR, "sankey_data.json")
    with open(sankey_path, "w", encoding="utf-8") as f:
        json.dump(sankey_payload, f, ensure_ascii=False, indent=2)
    print(
        f"Sankey: {sankey_path} ({len(sankey_data['nodes'])} nodes, "
        f"{len(sankey_data['links'])} links)"
    )

    # Report library: a company -> reports tree consumed by the
    # dashboard sidebar. Built from companies.json (so peer / control
    # reports show up even when they have zero findings of their own)
    # plus the headlines pool (so each report gets a severity breakdown
    # and a count of triggered findings). The whole library is a single
    # static JSON; refresh whenever a new report is added to companies.
    library_payload = build_report_library(targets, headlines_pool)
    library_path = os.path.join(OUTPUT_DIR, "report_library.json")
    with open(library_path, "w", encoding="utf-8") as f:
        json.dump(library_payload, f, ensure_ascii=False, indent=2)
    print(
        f"Report library: {library_path} ("
        f"{len(library_payload['companies'])} companies, "
        f"{sum(len(c['reports']) for c in library_payload['companies'])} reports)"
    )

    print(f"Summary: {agg_path}")
    print(f"Dashboard: {dash_path}")
    print(f"Leaderboard: {leaderboard_path}")
    print(f"Middelborg leaderboard: {middelborg_leaderboard_path}")
    print(f"Middelborg summary: {middelborg_summary_path}")
    print(f"Middelborg dashboard: {middelborg_dashboard_path}")
    def _hl(item):
        h = item.get("headline")
        return h if isinstance(h, str) else (h or {}).get("en", "")

    print("\n=== TOP 5 PRIORITY RED FLAGS (all) ===")
    for idx, item in enumerate(top[:5], start=1):
        print(f"{idx}. [{item['severity']}] priority={item['priority_score']:>5}  {_hl(item)}")
    print("\n=== TOP 5 MIDDELBORG-ONLY RED FLAGS ===")
    for idx, item in enumerate(middelborg_top[:5], start=1):
        print(f"{idx}. [{item['severity']}] priority={item['priority_score']:>5}  {_hl(item)}")


if __name__ == "__main__":
    main()
