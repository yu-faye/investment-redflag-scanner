"""Streamlit interactive dashboard for the Investment Red-Flag Scanner.

Run:
  .venv/bin/streamlit run app/streamlit_app.py

Reads the same leaderboard payloads the static HTML dashboard uses, so both
views are guaranteed to render the same set of findings.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUTS_DIR = PROJECT_ROOT / "outputs"
LEADERBOARDS = {
    "Middelborg only": OUTPUTS_DIR / "middelborg_leaderboard.json",
    "All companies": OUTPUTS_DIR / "leaderboard.json",
}
# v9 system artefacts. Optional -- if the run is on a pre-v9 codebase the
# panels are simply hidden.
V9_MATRIX_PATH = OUTPUTS_DIR / "triangulation_matrix.json"
V9_ROADMAP_PATH = OUTPUTS_DIR / "audit_roadmap.json"
# v10 visualization artefacts. Same tolerant-load pattern.
V10_SANKEY_PATH = OUTPUTS_DIR / "sankey_data.json"
V10_TREES_PATH = OUTPUTS_DIR / "argument_trees.json"
V10_PARAGRAPHS_PATH = OUTPUTS_DIR / "narrative_paragraphs.json"

# v9: standard verdict vocab + display glyphs. Kept in sync with
# dashboard.js VERDICT_GLYPH.
_VERDICT_DISPLAY = {
    "confirms":  "\u2713 confirms",
    "partial":   "\u25D0 partial",
    "refutes":   "\u2717 refutes",
    "not_found": "\u2298 not_found",
    "neutral":   "\u25E6 neutral",
    "error":     "! error",
}


@st.cache_data(show_spinner=False)
def _load(path: Path) -> dict:
    if not path.is_file():
        return {"top_findings": [], "generated_at_utc": None, "pages_url": None}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data(show_spinner=False)
def _load_optional_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _flatten_finding(f: dict) -> dict:
    prov = f.get("provenance") or {}
    raw_headline = f.get("headline") or ""
    headline = raw_headline if isinstance(raw_headline, str) else raw_headline.get("en", "")
    issuer = (
        prov.get("issuer_url_at_phrase")
        or prov.get("issuer_url_at_page")
        or prov.get("issuer_url")
        or prov.get("issuer_page")
    )
    github_url = prov.get("github_blob_url") or prov.get("github_head_url")
    local_path = prov.get("local_path")
    local_url = None
    if local_path:
        abs_local = (PROJECT_ROOT / local_path).resolve()
        local_url = abs_local.as_uri()
        if prov.get("pdf_page_hit"):
            local_url = f"{local_url}#page={prov['pdf_page_hit']}"
    snippet = f.get("evidence_snippet") or prov.get("evidence_snippet")
    metric_evidence = f.get("metric_evidence") or []
    # v8: external_collision payload (Doffin + future BRREG/Newsweb).
    external_evidence = f.get("external_evidence") or []
    return {
        "Company": f.get("company_name") or f.get("company"),
        "Rule": f.get("rule_id"),
        "Severity": f.get("severity"),
        "Priority": f.get("priority_score"),
        "Headline": headline,
        "Snippet": "[crop]" if snippet else "",
        "External": "[doffin]" if external_evidence else "",
        "IFRS": bool(f.get("consolidation_caveat")),
        "Page hit": prov.get("pdf_page_hit"),
        "Issuer PDF": issuer,
        "GitHub permalink": github_url,
        "Local file": local_url,
        "_raw": f,
        "_provenance": prov,
        "_snippet": snippet,
        "_metric_evidence": metric_evidence,
        "_external_evidence": external_evidence,
    }


_METRIC_SOURCE_LABELS = {
    "auto_regex": ("auto-extracted (regex hit)", "blue"),
    "manual_curation": ("manually curated (anchor)", "violet"),
    "manual_unverified": ("best-effort PDF match", "orange"),
    "unverified": ("not located in PDF", "gray"),
}


# Session-state key used to coordinate the "Show in PDF below" buttons with
# the embedded streamlit-pdf-viewer instance. The button updates this key,
# Streamlit reruns, and the PDF viewer re-mounts with the new annotation
# index so it scrolls to the target bbox.
_PDF_TARGET_KEY = "pdf_scroll_target_idx"
# Browser-scroll trigger: when a button is clicked we also want to slide the
# outer page down to the PDF viewer so the analyst doesn't have to manually
# scroll. Set to True by the button, consumed (and reset) by the PDF panel.
_PDF_AUTOSCROLL_KEY = "pdf_autoscroll_pending"
# Monotonic click counter. streamlit-pdf-viewer's React frontend only reads
# scroll_to_annotation on mount -- subsequent prop changes are ignored. To
# support repeated clicks we bump this counter on every button press and
# include it in the PDF viewer's `key`, which forces Streamlit to dispose
# the old iframe and mount a fresh one (which then honours the new
# scroll_to_annotation). The outer-page autoscroll script below waits long
# enough for the fresh iframe to render before scrolling.
_PDF_CLICK_COUNTER_KEY = "pdf_click_counter"


def _collect_evidence_items(row: dict) -> list:
    """Flatten narrative snippet + each metric snippet into a single ordered
    list. Order = the order in which the buttons appear in the UI; the
    streamlit-pdf-viewer uses `scroll_to_annotation` (1-indexed) to scroll
    to the matching bbox.

    Each item: {label, source, page, bbox, snippet_path, value, note, kind}.
    """
    items = []
    snippet = row.get("_snippet")
    if snippet and snippet.get("path") and snippet.get("bbox") and snippet.get("page"):
        items.append({
            "kind": "narrative",
            "label": "Narrative sentence",
            "source": "narrative",
            "page": int(snippet["page"]),
            "bbox": snippet["bbox"],
            "snippet_path": snippet["path"],
            "width": snippet.get("width"),
            "height": snippet.get("height"),
            "size_bytes": snippet.get("size_bytes"),
        })
    for me in row.get("_metric_evidence") or []:
        snip = me.get("snippet")
        item = {
            "kind": "metric",
            "label": f"{me.get('metric')} = {me.get('value')}",
            "metric": me.get("metric"),
            "value": me.get("value"),
            "source": me.get("source", "unverified"),
            "note": me.get("note"),
            "page": int(snip["page"]) if snip and snip.get("page") else None,
            "bbox": snip.get("bbox") if snip else None,
            "snippet_path": snip.get("path") if snip else None,
            "width": snip.get("width") if snip else None,
            "height": snip.get("height") if snip else None,
            "confidence": snip.get("confidence") if snip else None,
            "context_term": snip.get("context_term") if snip else None,
            "matched_str": snip.get("matched_str") if snip else None,
        }
        items.append(item)
    return items


def _build_annotations_from_items(items: list) -> list:
    """Convert the flat evidence list into streamlit-pdf-viewer annotations.

    Items with no bbox are skipped here but kept in `items` so the UI can
    still show them as cards (just without a "Show in PDF below" button)."""
    annotations = []
    for it in items:
        bbox = it.get("bbox")
        page = it.get("page")
        if not bbox or not page:
            continue
        pad = 3
        annotations.append({
            "page": int(page),
            "x": max(0.0, float(bbox.get("x0", 0)) - pad),
            "y": max(0.0, float(bbox.get("top", 0)) - pad),
            "width": float(bbox.get("width", 0)) + pad * 2,
            "height": float(bbox.get("height", 0)) + pad * 2,
            "color": "rgba(255, 179, 0, 0.95)" if it["kind"] == "narrative" else "rgba(0, 119, 204, 0.95)",
            "interior_color": "rgba(255, 179, 0, 0.22)" if it["kind"] == "narrative" else "rgba(0, 119, 204, 0.18)",
            "border": "solid",
        })
    return annotations


def _scroll_button(key_suffix: str, anno_index: int, anno_total: int, page: int) -> None:
    """Render the per-snippet 'Show in PDF below' button. Clicking it stores
    the target annotation index in session_state and triggers a rerun, which
    re-mounts the PDF viewer scrolled to that bbox."""
    disabled = anno_index is None
    label = (
        f"\u21e9 Show in PDF below (jump to page {page})"
        if not disabled
        else "(no PDF bbox available)"
    )
    if st.button(label, key=f"scroll_{key_suffix}", disabled=disabled, use_container_width=True):
        st.session_state[_PDF_TARGET_KEY] = anno_index + 1  # 1-indexed
        st.session_state[_PDF_AUTOSCROLL_KEY] = True
        st.session_state[_PDF_CLICK_COUNTER_KEY] = (
            st.session_state.get(_PDF_CLICK_COUNTER_KEY, 0) + 1
        )
        st.rerun()


def _render_evidence_snippet(row: dict, items: list) -> None:
    """Render the narrative-sentence snippet card with a 'jump to PDF' button.

    Rendered as a parallel top-level section under st.subheader, so it sits
    visually equal to "Per-number provenance" and "Provenance & sources".
    """
    narrative = next((i for i in items if i["kind"] == "narrative"), None)
    st.subheader("Narrative evidence")
    if not narrative:
        st.caption(
            "No narrative sentence snippet was generated for this finding "
            "(typically because the source is TXT-only or the rule fired on "
            "an aggregate without a single anchor sentence)."
        )
        return
    abs_path = PROJECT_ROOT / narrative["snippet_path"]
    if not abs_path.is_file():
        st.info(f"Snippet not on disk: {narrative['snippet_path']}")
        return
    with st.container(border=True):
        st.caption("Cropped from the source PDF page.")
        width = narrative.get("width") or "?"
        height = narrative.get("height") or "?"
        size_kb = (narrative.get("size_bytes") or 0) / 1024
        st.image(
            str(abs_path),
            caption=f"page {narrative['page']} \u00b7 {width}x{height} px \u00b7 {size_kb:.1f} KB",
            use_container_width=True,
        )
        anno_index = items.index(narrative)
        _scroll_button("narrative", anno_index, len(items), narrative["page"])


def _render_metric_evidence(row: dict, items: list) -> None:
    """For each metric value cited by the finding, show a small evidence card
    with its provenance badge + snippet PNG + 'jump to PDF' button.

    Rendered as a parallel top-level section under st.subheader.
    """
    metric_items = [i for i in items if i["kind"] == "metric"]
    st.subheader("Per-number provenance")
    if not metric_items:
        st.caption(
            "This finding does not cite any individual metric values "
            "(common for selective_disclosure rows that compare reporting "
            "behaviour rather than numbers)."
        )
        return
    st.caption(
        "Each metric value in the headline is traced back to its source row "
        "in the PDF. Click 'Show in PDF below' on any card to (re)scroll the "
        "embedded viewer to that bbox -- you can jump back and forth between "
        "cards as many times as you want."
    )
    for it in metric_items:
        src = it.get("source", "unverified")
        label, color = _METRIC_SOURCE_LABELS.get(src, (src, "gray"))
        with st.container(border=True):
            head_cols = st.columns([3, 2])
            head_cols[0].markdown(f"`{it.get('metric')}` = **{it.get('value')}**")
            head_cols[1].markdown(f":{color}[{label}]")
            if it.get("snippet_path"):
                abs_path = PROJECT_ROOT / it["snippet_path"]
                if abs_path.is_file():
                    bits = []
                    if it.get("page"):
                        bits.append(f"page {it['page']}")
                    if it.get("confidence"):
                        bits.append(f"conf {it['confidence']}")
                    if it.get("context_term"):
                        bits.append(f"near \"{it['context_term']}\"")
                    if it.get("matched_str"):
                        bits.append(f"matched {it['matched_str']!r}")
                    st.image(
                        str(abs_path),
                        caption=" \u00b7 ".join(bits),
                        use_container_width=True,
                    )
                else:
                    st.caption(f"snippet missing on disk: {it['snippet_path']}")
            else:
                st.caption("No PDF row was located for this value.")
            if it.get("note"):
                st.caption(it["note"])
            anno_index = items.index(it) if it.get("bbox") and it.get("page") else None
            _scroll_button(
                f"metric_{it.get('metric')}",
                anno_index,
                len(items),
                it.get("page") or 0,
            )


_EXTERNAL_SEVERITY_COLORS = {
    "critical": "red",
    "warning": "orange",
    "info": "blue",
}


def _render_external_collision(row: dict) -> None:
    """v8: fourth top-level section. Renders one card per external source
    in the finding's external_evidence list. The PDF claim side stays in
    the 'Narrative evidence' section above (and its 'Show in PDF below'
    button still works); this section focuses on the public-registry side
    and each award row is a one-click link to the original Doffin notice.
    """
    external_evidence = row.get("_external_evidence") or []
    st.subheader("External collision")
    raw = row.get("_raw") or {}
    if not external_evidence:
        st.caption(
            "This finding has no external public-registry cross-check. "
            "Currently only the external_collision detector populates this "
            "section (v8 covers Doffin / Norwegian public procurement)."
        )
        return
    severity = (raw.get("severity") or "").lower()
    color = _EXTERNAL_SEVERITY_COLORS.get(severity, "gray")
    subsidiary = raw.get("subsidiary") or "?"
    claimed_role = raw.get("subsidiary_claimed_role") or ""
    acquired = raw.get("subsidiary_acquired_year")
    verdict = raw.get("verdict") or ""
    summary_bits = [f":{color}[{severity}]"]
    if subsidiary:
        summary_bits.append(f"`{subsidiary}`")
    if claimed_role:
        summary_bits.append(f"claimed as: *{claimed_role}*")
    if acquired:
        summary_bits.append(f"acquired ~{acquired}")
    if verdict:
        summary_bits.append(f"verdict: `{verdict}`")
    st.markdown(" \u00b7 ".join(summary_bits))

    for ee in external_evidence:
        supplier = ee.get("supplier_name", "?")
        src_label = ee.get("source_label") or ee.get("source", "external")
        confirmed = ee.get("confirmed_awards") or []
        hits = ee.get("search_hits_total", "?")
        n_conf = ee.get("confirmed_award_count") if ee.get("confirmed_award_count") is not None else len(confirmed)
        with st.container(border=True):
            head_cols = st.columns([3, 2])
            head_cols[0].markdown(f"**{src_label}** \u00b7 supplier `{supplier}`")
            head_cols[1].markdown(
                f"**{n_conf}** confirmed award{'s' if n_conf != 1 else ''} / **{hits}** search hit{'s' if hits != 1 else ''}"
            )
            if confirmed:
                table_rows = []
                for a in confirmed:
                    table_rows.append({
                        "Published": a.get("publication_date") or a.get("issue_date") or "?",
                        "Buyer": ", ".join(a.get("buyer_names") or []) or "?",
                        "Heading": (a.get("heading") or "")[:90],
                        "All awarded": ", ".join(a.get("awarded_names") or []),
                        "Notice": a.get("public_url") or "",
                    })
                df = pd.DataFrame(table_rows)
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Notice": st.column_config.LinkColumn(
                            "Notice",
                            display_text="open",
                            help="Original Doffin notice page (opens in new tab).",
                        ),
                        "Heading": st.column_config.TextColumn("Heading", width="large"),
                    },
                )
            else:
                st.warning(
                    f"No confirmed award records returned for **{supplier}**. "
                    f"Doffin text search returned {hits} hit{'s' if hits != 1 else ''} "
                    f"but none had **{supplier}** in the official `awardedNames` field. "
                    "This is the v8 killer collision: the public procurement registry "
                    "has zero record of this 'specialist' winning work."
                )
            st.caption(
                f"query: `{ee.get('query_url','n/a')}`  \u00b7  "
                f"cached: `{ee.get('cache_path','n/a')}`  \u00b7  "
                f"sha256: `{(ee.get('cache_sha256') or '')[:16]}\u2026`  \u00b7  "
                f"fetched: `{ee.get('fetched_at_utc','n/a')}`"
            )


def _render_provenance_card(row: dict) -> None:
    """Third top-level section: click-through URLs, sentence context, hashes.

    Sits visually parallel to 'Narrative evidence' and 'Per-number
    provenance' so the analyst sees three equal-weight blocks rather than a
    nested hierarchy.
    """
    st.subheader("Provenance & sources")
    prov = row.get("_provenance") or {}
    raw = row.get("_raw") or {}
    cols = st.columns(2)
    with cols[0]:
        st.markdown("**Click-through**")
        if row.get("Issuer PDF"):
            st.markdown(f"- [Open issuer PDF]({row['Issuer PDF']})")
        if row.get("GitHub permalink"):
            st.markdown(f"- [GitHub commit permalink]({row['GitHub permalink']})")
        if row.get("Local file"):
            st.markdown(f"- [Open local file]({row['Local file']})")
        if not any([row.get("Issuer PDF"), row.get("GitHub permalink"), row.get("Local file")]):
            st.caption("No source URLs available for this finding.")

        st.markdown("**Provenance hashes**")
        st.code(
            f"sha256 = {prov.get('sha256') or 'n/a'}\n"
            f"git_sha = {prov.get('git_sha') or 'n/a'}\n"
            f"repo_head = {prov.get('repo_head_sha') or 'n/a'}\n"
            f"page hit = {prov.get('pdf_page_hit') or 'n/a'}",
            language="text",
        )

    with cols[1]:
        locator = prov.get("excerpt_locator") or {}
        ctx = locator.get("sentence_context") or {}
        excerpt = raw.get("claim_excerpt") or "(no excerpt captured)"
        if ctx:
            st.markdown("**Sentence context** (matched sentence highlighted)")
            prev = (ctx.get("prev") or "").strip()
            match = (ctx.get("match") or excerpt).strip()
            nxt = (ctx.get("next") or "").strip()
            st.markdown(
                f"<div style='line-height:1.6'>"
                f"<span style='color:#8a93a7'>{prev}</span> "
                f"<mark style='background:#fff3a3;color:#000;padding:1px 3px;border-radius:3px'>"
                f"{match}</mark> "
                f"<span style='color:#8a93a7'>{nxt}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown("**Excerpt**")
            st.write(excerpt)
        if raw.get("metric_alignment"):
            st.markdown("**Metric alignment**")
            st.json(raw["metric_alignment"], expanded=False)
        if raw.get("follow_up_questions"):
            st.markdown("**Stress-test follow-up prompts**")
            st.json(raw["follow_up_questions"], expanded=False)


def _render_pdf_panel(row: dict, items: list) -> None:
    """Always-visible embedded PDF panel. Reads `_PDF_TARGET_KEY` from
    session_state to decide which annotation to scroll to."""
    prov = row.get("_provenance") or {}
    local_path = prov.get("local_path")
    if not local_path or not local_path.lower().endswith(".pdf"):
        st.caption("Inline PDF preview is only available for PDF sources.")
        return
    abs_local = PROJECT_ROOT / local_path
    if not abs_local.is_file():
        st.caption(f"Local file missing: {local_path}")
        return
    annotations = _build_annotations_from_items(items)
    default_page = prov.get("pdf_page_hit") or 1
    target_idx = st.session_state.get(_PDF_TARGET_KEY)
    # Validate the target against the current finding's annotation list. When
    # the analyst drills into a new finding, the previous target index may
    # exceed the new list length -- reset it in that case.
    if target_idx is None or target_idx < 1 or (annotations and target_idx > len(annotations)):
        target_idx = 1 if annotations else None
    try:
        from streamlit_pdf_viewer import pdf_viewer
    except ImportError:
        st.info(
            "Install streamlit-pdf-viewer to enable the inline PDF panel: "
            "`.venv/bin/pip install streamlit-pdf-viewer`"
        )
        st.markdown(
            f"[Open {os.path.basename(local_path)} locally]"
            f"({abs_local.as_uri()}#page={default_page})"
        )
        return

    if annotations:
        # Build the corresponding item index for each annotation so the
        # "Currently scrolled to" caption stays in sync. We also need this
        # mapping below to find the target page even when target_idx > 1.
        anno_to_item = []
        for it in items:
            if it.get("bbox") and it.get("page"):
                anno_to_item.append(it)
        if target_idx:
            current_anno = annotations[target_idx - 1]
            current_item = anno_to_item[target_idx - 1]
            label_bits = [
                f"annotation {target_idx}/{len(annotations)}",
                f"page {current_anno['page']}",
            ]
            if current_item.get("kind") == "metric":
                label_bits.append(
                    f"metric `{current_item.get('metric')}` = "
                    f"{current_item.get('value')}"
                )
            else:
                label_bits.append("narrative sentence")
            st.caption("Currently scrolled to: " + " \u00b7 ".join(label_bits))
            target_page = int(current_anno["page"])
        else:
            st.caption("No scroll target selected yet.")
            target_page = int(default_page)
        # streamlit-pdf-viewer's React component reads scroll_to_annotation
        # ONLY on mount; later prop changes are ignored. To support repeated
        # clicks we therefore include the monotonic click counter in `key`
        # so each click yields a fresh iframe that honours the new target.
        # The outer-page autoscroll script below waits ~700ms to give that
        # fresh iframe time to render before scrolling, which fixes the
        # earlier race where the page scrolled to a not-yet-mounted PDF.
        _ = target_page  # consumed only by the caption above
        click_nonce = st.session_state.get(_PDF_CLICK_COUNTER_KEY, 0)
        pdf_viewer(
            input=str(abs_local),
            width=1200,
            height=1100,
            annotations=annotations,
            scroll_to_annotation=int(target_idx) if target_idx else 1,
            scroll_behavior="smooth",
            annotation_outline_size=2,
            render_text=True,
            key=f"pdf_main_{local_path}_{click_nonce}",
        )
    else:
        st.caption(
            f"No bbox captured for this finding; showing page {default_page} only."
        )
        pdf_viewer(
            input=str(abs_local),
            pages_to_render=[int(default_page)],
            width=1200,
            scroll_to_page=int(default_page),
            scroll_behavior="smooth",
            key=f"pdf_main_pageonly_{local_path}",
        )

    # If a "Show in PDF below" button was just clicked, inject a one-shot
    # script that slides the outer Streamlit page down to the PDF anchor
    # (planted in main() just before this panel). Streamlit's iframe runs
    # same-origin in dev, so window.parent.document.* is reachable. We delay
    # ~250ms to let streamlit-pdf-viewer mount before scrolling.
    if st.session_state.pop(_PDF_AUTOSCROLL_KEY, False):
        try:
            import streamlit.components.v1 as components

            components.html(
                """
                <script>
                  (function () {
                    function scrollNow() {
                      try {
                        const doc = window.parent && window.parent.document
                          ? window.parent.document
                          : document;
                        const target = doc.getElementById('embedded-pdf-anchor');
                        if (target && target.scrollIntoView) {
                          target.scrollIntoView({behavior: 'smooth', block: 'start'});
                        }
                      } catch (e) {
                        /* cross-origin fallback: nothing to do */
                      }
                    }
                    setTimeout(scrollNow, 700);
                  })();
                </script>
                """,
                height=0,
            )
        except Exception:
            # components.v1 unavailable in some headless test contexts; the
            # click still works, the analyst just has to scroll manually.
            pass


def _render_triangulation_matrix_section() -> None:
    """Hypothesis x tap-kind matrix + the audit roadmap. Renders inline
    (no outer expander) so it lives under its own page anchor and can be
    targeted from the sidebar table of contents."""
    matrix = _load_optional_json(V9_MATRIX_PATH)
    roadmap = _load_optional_json(V9_ROADMAP_PATH)
    if not matrix or not (matrix.get("rows") or []):
        st.info(
            "No triangulation matrix yet. Run "
            "`.venv/bin/python validation/run_real_report.py` first."
        )
        return

    st.markdown(
        "Each row is an **audit hypothesis** (a verbatim claim from a "
        "PDF). Each column is an **external data source family**. Cells "
        "show the verdict returned by the most recent tap. A hypothesis "
        "is only allowed to graduate to **critical** when (a) >= 2 taps "
        "agree, (b) every `blocking_for_critical` falsification question "
        "has been addressed, and (c) the peer-control rule passes. "
        "Single-source absence cannot drive critical."
    )

    active_kinds = [
        k for k in matrix.get("tap_kinds", [])
        if any(
            (r.get("cells_by_kind") or {}).get(k) for r in matrix["rows"]
        )
    ]
    for r in matrix["rows"]:
        for rec in r.get("next_recommended_taps") or []:
            if rec["tap_kind"] not in active_kinds:
                active_kinds.append(rec["tap_kind"])

    matrix_rows = []
    for r in matrix["rows"]:
        cells = r.get("cells_by_kind") or {}
        row = {
            "Entity": r.get("entity") or r.get("hypothesis_id"),
            "Derived severity": r.get("derived_severity") or "?",
        }
        for kind in active_kinds:
            cell = cells.get(kind)
            if cell:
                row[kind.replace("_", " ")] = (
                    f"{_VERDICT_DISPLAY.get(cell['verdict'], cell['verdict'])} "
                    f"({cell.get('confidence')})"
                )
            else:
                row[kind.replace("_", " ")] = "\u2014"
        nrt = (r.get("next_recommended_taps") or [None])[0]
        row["Next tap"] = (
            f"{nrt['tap_kind']}"
            + (" \u2605" if nrt.get("blocking_for_critical") else "")
            if nrt
            else "\u2014"
        )
        matrix_rows.append(row)
    st.dataframe(
        pd.DataFrame(matrix_rows),
        use_container_width=True,
        hide_index=True,
    )

    for r in matrix["rows"]:
        blockers = r.get("blockers_for_critical") or []
        if not blockers:
            continue
        with st.expander(
            f"Why '{r.get('entity')}' is not yet critical ({len(blockers)} blocker(s))",
            expanded=False,
        ):
            for b in blockers:
                st.write(f"\u2022 {b}")

    st.markdown("---")
    st.markdown("### Audit roadmap")
    st.markdown(
        "Which external data source would maximally raise triangulation "
        "coverage next. Implementing one of these adds a new column to "
        "the matrix above."
    )
    recs = (roadmap or {}).get("recommended_taps") or []
    if not recs:
        st.markdown(
            '<div class="roadmap-empty-note">All applicable tap kinds for '
            'every hypothesis have already been queried. No new external '
            'data source would raise coverage right now.</div>',
            unsafe_allow_html=True,
        )
        return

    # Plain-black, high-contrast cards instead of Streamlit's coloured
    # inline markup -- the user explicitly asked for readable contrast.
    for idx, rec in enumerate(recs):
        unblock = rec.get("would_unblock_critical_for") or []
        head_html = (
            f'<div class="roadmap-card-head">'
            f'<span class="roadmap-rank">#{idx + 1}</span> '
            f'<span class="roadmap-kind">{rec["tap_kind"].replace("_", " ")}</span> '
            f'<span class="roadmap-gain">+{rec["total_information_gain"]:.2f} info gain</span>'
            + (
                f' <span class="roadmap-unblock">would unblock critical '
                f'for {len(unblock)} hypothesis(es)</span>'
                if unblock else ""
            )
            + '</div>'
        )
        st.markdown(head_html, unsafe_allow_html=True)
        bullet_lines = []
        for h in rec.get("covers_hypotheses") or []:
            block_tag = (
                ' <span class="roadmap-blocking">[blocking]</span>'
                if h.get("blocking_for_critical")
                else ""
            )
            bullet_lines.append(
                f'<li><code>{h.get("entity") or h.get("hypothesis_id")}</code> '
                f'\u2014 addresses <em>{h.get("question_id")}</em>{block_tag}</li>'
            )
        if bullet_lines:
            st.markdown(
                f'<ul class="roadmap-covers">{"".join(bullet_lines)}</ul>',
                unsafe_allow_html=True,
            )


def _render_landscape_section() -> None:
    """Sankey of the global reasoning landscape, rendered inline (no
    outer expander) so it lives under its own page anchor."""
    data = _load_optional_json(V10_SANKEY_PATH)
    if not data or not (data.get("nodes") or []):
        st.info("No Sankey payload yet -- run the pipeline once.")
        return
    try:
        import plotly.graph_objects as go
    except ImportError:
        st.info(
            "Install plotly to render the Sankey: `pip install plotly`."
        )
        return

    st.caption(
        "One flow unit per finding, split across falsification questions "
        "and tap evidence. Layers: issuer \u2192 claim/rule \u2192 question "
        "\u2192 tap evidence (verdict) \u2192 derived severity. Hover for "
        "tooltip; node colours follow tap verdict "
        "(green=confirms, red=refutes, orange=not_found)."
    )
    idx = {n["id"]: i for i, n in enumerate(data["nodes"])}
    fig = go.Figure(go.Sankey(
        node=dict(
            label=[n["name"] for n in data["nodes"]],
            color=[n.get("color") for n in data["nodes"]],
            pad=14,
            thickness=14,
            line=dict(color="#444", width=0.5),
        ),
        link=dict(
            source=[idx[l["source"]] for l in data["links"]],
            target=[idx[l["target"]] for l in data["links"]],
            value=[l["value"] for l in data["links"]],
            color=[l.get("color") for l in data["links"]],
            customdata=[
                [l.get("verdict") or "-", len(l.get("finding_keys") or [])]
                for l in data["links"]
            ],
            hovertemplate=(
                "%{source.label} \u2192 %{target.label}<br>"
                "value=%{value:.2f}<br>"
                "verdict=%{customdata[0]}<br>"
                "findings=%{customdata[1]}<extra></extra>"
            ),
        ),
    ))
    fig.update_layout(
        margin=dict(l=10, r=10, t=20, b=10),
        height=max(360, min(900, 22 * len(data["nodes"]) + 80)),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_pipeline_overview() -> None:
    """5 step deterministic audit pipeline. Lives at the top of the
    page. Copy intentionally abstract: the engineering details are
    one click away (the matrix, the JSON files), the overview is for
    a non engineer reader who wants the gist in 10 seconds."""
    steps = [
        {
            "n": "1",
            "title": "Collect",
            "color": "#2980b9",
            "body": (
                "Pull published financial reports from issuers and "
                "version pin every byte."
            ),
        },
        {
            "n": "2",
            "title": "Semantic rules",
            "color": "#8e44ad",
            "body": (
                "Apply deterministic semantic rules to spot claims "
                "worth checking. "
                "<em>Coming soon: Claude API for deeper language "
                "analysis on disputed claims.</em>"
            ),
        },
        {
            "n": "3",
            "title": "Evidence gathering",
            "color": "#16a085",
            "body": (
                "Cross check each claim against independent public "
                "registries (procurement, company registry, EU tenders)."
            ),
        },
        {
            "n": "4",
            "title": "Triangulate",
            "color": "#d35400",
            "body": (
                "Combine evidence streams into one verdict per claim. "
                "No single source can drive a critical flag on its own."
            ),
        },
        {
            "n": "5",
            "title": "Trace",
            "color": "#2c3e50",
            "body": (
                "Every finding links back to the exact source page so "
                "any reader can re derive the conclusion."
            ),
        },
    ]
    cards_html = "".join(
        f'<div class="pipeline-card" style="border-top-color:{s["color"]};">'
        f'<div class="pipeline-step" style="background:{s["color"]};">Step {s["n"]}</div>'
        f'<div class="pipeline-title">{s["title"]}</div>'
        f'<div class="pipeline-body">{s["body"]}</div>'
        f'</div>'
        for s in steps
    )
    st.markdown(
        '<style>'
        '.pipeline-strip{display:grid;grid-template-columns:repeat(5,1fr);'
        'gap:10px;margin-top:6px;margin-bottom:10px;}'
        '.pipeline-card{background:#fff;border:1px solid #d8e0ea;'
        'border-top:4px solid #2c3e50;border-radius:6px;padding:10px 12px;'
        'box-shadow:0 1px 3px rgba(0,0,0,0.05);}'
        '.pipeline-step{display:inline-block;color:#fff;font-size:10.5px;'
        'font-weight:700;letter-spacing:0.5px;padding:2px 8px;border-radius:3px;'
        'text-transform:uppercase;}'
        '.pipeline-title{font-size:15px;font-weight:700;color:#1a2532;'
        'margin-top:6px;}'
        '.pipeline-body{font-size:12px;color:#2c3e50;line-height:1.45;'
        'margin-top:4px;}'
        '.pipeline-body code{background:#f1f4f9;padding:1px 4px;'
        'border-radius:2px;font-size:11px;color:#1b2533;}'
        '.roadmap-card-head{background:#fff;border:1px solid #d8e0ea;'
        'border-left:4px solid #2c3e50;padding:6px 10px;margin-top:8px;'
        'border-radius:4px;font-size:13px;color:#000;}'
        '.roadmap-rank{font-weight:700;color:#000;}'
        '.roadmap-kind{font-weight:600;color:#000;margin-left:4px;}'
        '.roadmap-gain{color:#000;margin-left:8px;font-size:12px;}'
        '.roadmap-unblock{color:#000;background:#fef9e7;padding:1px 6px;'
        'border-radius:3px;margin-left:8px;font-size:11.5px;font-weight:600;'
        'border:1px solid #f4d03f;}'
        '.roadmap-covers{margin:4px 0 4px 18px;font-size:12.5px;color:#000;}'
        '.roadmap-blocking{color:#000;background:#fadbd8;padding:1px 5px;'
        'border-radius:3px;font-size:11px;font-weight:700;}'
        '.roadmap-empty-note{background:#eafaf1;border:1px solid #a3e4c1;'
        'color:#000;padding:8px 12px;border-radius:4px;font-size:13px;}'
        '.toc-nav{font-size:13.5px;line-height:1.9;}'
        '.toc-nav a{color:#1a2532;text-decoration:none;'
        'border-left:3px solid transparent;padding-left:8px;display:block;}'
        '.toc-nav a:hover{border-left-color:#1abc9c;background:#eafaf1;}'
        '</style>'
        f'<div class="pipeline-strip">{cards_html}</div>',
        unsafe_allow_html=True,
    )


def _render_v10_finding_block(finding: dict, composite_key: str) -> None:
    """Render the v10 narrative paragraph + nested argument tree inside
    a per-finding drawer."""
    paragraphs = _load_optional_json(V10_PARAGRAPHS_PATH) or {}
    trees = _load_optional_json(V10_TREES_PATH) or {}
    para = (paragraphs.get("paragraphs") or {}).get(composite_key)
    tree = (trees.get("trees") or {}).get(composite_key)
    if not para and not tree:
        return

    if para:
        st.markdown(f"##### {para.get('headline', '')}")
        # Convert [n] markers to anchor links via markdown footnotes-ish.
        body = para.get("body", "")
        cit_lookup = para.get("citations", {})
        # Show body as-is; citations rendered as a list below.
        st.markdown(body)
        if cit_lookup:
            st.markdown("**Citations:**")
            for num, c in cit_lookup.items():
                href = c.get("href") or ""
                label = c.get("label") or "evidence"
                kind = c.get("kind") or "evidence"
                if href:
                    st.markdown(f"- `[{num}]` _{kind}_ \u00b7 [{label}]({href})")
                else:
                    st.markdown(f"- `[{num}]` _{kind}_ \u00b7 {label}")

    if tree:
        with st.expander("Argument tree (full reasoning chain)", expanded=False):
            _render_tree_node_streamlit(tree, depth=0)


def _render_tree_node_streamlit(node: dict, depth: int) -> None:
    if not node:
        return
    glyph = node.get("glyph") or ""
    label = node.get("label") or ""
    kind = node.get("kind") or "evidence"
    severity = node.get("severity")
    verdict = node.get("verdict")
    badge_bits = []
    if severity:
        badge_bits.append(f":red[severity={severity}]" if severity == "critical"
                          else f":orange[severity={severity}]" if severity == "warning"
                          else f":blue[severity={severity}]")
    if verdict:
        badge_bits.append(f"verdict={verdict}")
    badge_str = " \u00b7 ".join(badge_bits)
    title = f"{glyph} {label}" + (f" ({badge_str})" if badge_str else "")

    if node.get("children"):
        with st.expander(title, expanded=(depth < 2)):
            if node.get("detail"):
                st.caption(node["detail"])
            for ln in node.get("links") or []:
                if ln.get("href"):
                    st.markdown(f"- [{ln.get('label', 'link')}]({ln['href']})")
            for ch in node["children"]:
                _render_tree_node_streamlit(ch, depth + 1)
    else:
        st.markdown(f"- **{glyph} {label}** {badge_str}")
        if node.get("detail"):
            st.caption(node["detail"])
        for ln in node.get("links") or []:
            if ln.get("href"):
                st.markdown(f"  - [{ln.get('label', 'link')}]({ln['href']})")


_SELECTED_IDX_KEY = "_selected_drill_idx"


def _render_leaderboard_section(filtered: pd.DataFrame) -> None:
    """Findings leaderboard + filters + clickable rows that drive the
    Drill section below. Returns nothing; communicates the selected row
    via session_state[_SELECTED_IDX_KEY]."""
    if filtered.empty:
        st.info("No findings match the current filters.")
        return

    total = len(filtered)
    sev_counts = filtered["Severity"].value_counts().to_dict()
    cols = st.columns(4)
    cols[0].metric("Total findings", total)
    cols[1].metric("Critical", int(sev_counts.get("critical", 0)))
    cols[2].metric("Warning", int(sev_counts.get("warning", 0)))
    cols[3].metric("Info", int(sev_counts.get("info", 0)))

    st.caption(
        "Click any row to drill into that finding's full reasoning chain "
        "in the **Drill into a finding** section below. The arrow column "
        "links to the same anchor so you can also scroll there manually."
    )

    # Add a leading "Open" link column. The link target is the page anchor
    # for the drill section -- clicking it just scrolls; row selection is
    # what actually picks which finding the drill section displays.
    display_df = filtered.copy()
    display_df.insert(0, "Open", ["#drill-into-a-finding"] * len(display_df))

    display_cols = [
        "Open",
        "Company",
        "Rule",
        "Severity",
        "Priority",
        "Headline",
        "Snippet",
        "IFRS",
        "Page hit",
        "Issuer PDF",
        "GitHub permalink",
        "Local file",
    ]
    column_config = {
        "Open": st.column_config.LinkColumn(
            "Open",
            display_text="\u2193 drill",
            help="Jump to the Drill section below.",
        ),
        "Headline": st.column_config.TextColumn("Headline", width="large"),
        "Priority": st.column_config.NumberColumn(
            "Priority",
            format="%.1f",
            help="Deterministic ranking heuristic (severity x evidence magnitude x novelty). Sort key only -- not a statistical test.",
        ),
        "Snippet": st.column_config.TextColumn(
            "Crop",
            help="[crop] indicates this finding has a pre-generated evidence-snippet PNG.",
        ),
        "IFRS": st.column_config.CheckboxColumn(
            "IFRS",
            help="True when the lag_causality guardrail tagged this as mechanical IFRS consolidation.",
        ),
        "Page hit": st.column_config.NumberColumn("Page", format="%d"),
        "Issuer PDF": st.column_config.LinkColumn(
            "Issuer PDF",
            display_text="open",
            help="Publisher-hosted PDF, opens at matched page when available.",
        ),
        "GitHub permalink": st.column_config.LinkColumn(
            "Permalink",
            display_text="commit",
            help="Immutable GitHub blob URL at the commit we scanned.",
        ),
        "Local file": st.column_config.LinkColumn(
            "Local",
            display_text="open",
            help="file:// link to the local PDF (best-effort page anchor).",
        ),
    }

    event = st.dataframe(
        display_df[display_cols],
        use_container_width=True,
        hide_index=False,
        column_config=column_config,
        on_select="rerun",
        selection_mode="single-row",
        key="leaderboard_table",
    )
    sel_rows = []
    try:
        sel_rows = list(event.selection.rows)  # type: ignore[attr-defined]
    except Exception:
        sel_rows = []
    if sel_rows:
        st.session_state[_SELECTED_IDX_KEY] = int(sel_rows[0])


def _render_drill_section(filtered: pd.DataFrame) -> None:
    """Per-finding deep-dive. Reads the selected row index from
    session_state[_SELECTED_IDX_KEY]; falls back to 0 (top finding)."""
    if filtered.empty:
        st.info("No findings to drill into.")
        return

    max_idx = len(filtered) - 1
    default_idx = int(st.session_state.get(_SELECTED_IDX_KEY, 0))
    default_idx = max(0, min(default_idx, max_idx))

    cols = st.columns([3, 1])
    with cols[0]:
        st.caption(
            "Currently drilled into row #" + str(default_idx) +
            ". Click a different row in the leaderboard above to switch, "
            "or use the number input on the right."
        )
    with cols[1]:
        idx = st.number_input(
            "Row index",
            min_value=0,
            max_value=max_idx,
            value=default_idx,
            step=1,
            key="_drill_idx_input",
        )

    row = filtered.iloc[int(idx)].to_dict()
    raw_row = filtered.iloc[int(idx)]
    row["_raw"] = raw_row["_raw"]
    row["_provenance"] = raw_row["_provenance"]
    row["_snippet"] = raw_row.get("_snippet")
    row["_metric_evidence"] = raw_row.get("_metric_evidence")

    finding_signature = (
        row.get("Company"),
        row.get("Rule"),
        row.get("Headline"),
    )
    if st.session_state.get("_last_finding_sig") != finding_signature:
        st.session_state["_last_finding_sig"] = finding_signature
        st.session_state[_PDF_TARGET_KEY] = 1

    priority_val = row.get("Priority")
    try:
        pv = float(priority_val)
        priority_label = "priority n/a" if pv != pv else f"priority {pv:.1f}"
    except (TypeError, ValueError):
        priority_label = "priority n/a"
    st.markdown(
        f"### {row['Company']} \u2014 {row['Rule']} ({row['Severity']}, {priority_label})"
    )
    st.markdown(f"> {row['Headline']}")

    items = _collect_evidence_items(row)

    raw_finding = row.get("_raw") or {}
    _raw_headline = raw_finding.get("headline") or "?"
    if isinstance(_raw_headline, dict):
        _raw_headline = _raw_headline.get("en") or "?"
    _raw_headline = str(_raw_headline)
    composite_key = raw_finding.get("composite_key") or "|".join(
        [
            str(raw_finding.get("company") or "?"),
            str(raw_finding.get("rule_id") or "?"),
            str(raw_finding.get("hypothesis_id") or _raw_headline[:80]),
        ]
    )
    _render_v10_finding_block(raw_finding, composite_key)
    st.divider()

    _render_evidence_snippet(row, items)
    st.divider()
    _render_metric_evidence(row, items)
    st.divider()
    _render_external_collision(row)
    st.divider()
    _render_provenance_card(row)


def _render_sidebar_toc(payload: dict) -> None:
    """Left sidebar: site navigation + report library + run metadata."""
    with st.sidebar:
        st.markdown("### Contents")
        st.markdown(
            '<nav class="toc-nav">'
            '<a href="#how-it-works">How it works</a>'
            '<a href="#findings-leaderboard">Findings leaderboard</a>'
            '<a href="#logic-chain-landscape">Logic chain landscape</a>'
            '<a href="#triangulation-matrix">Triangulation matrix</a>'
            '<a href="#drill-into-a-finding">Drill into a finding</a>'
            '<a href="#embedded-source-pdf">Embedded source PDF</a>'
            '</nav>',
            unsafe_allow_html=True,
        )
        st.divider()
        st.markdown("### Report library")
        lib = _load_optional_json(PROJECT_ROOT / "outputs" / "report_library.json")
        if not lib or not lib.get("companies"):
            st.caption("No reports indexed yet.")
        else:
            for c in lib["companies"]:
                total_f = c.get("total_findings", 0)
                total_c = c.get("total_critical", 0)
                meta = f"{c.get('report_count', 0)} \u00B7 {total_f} findings"
                if total_c:
                    meta += f" \u00B7 {total_c} crit"
                with st.expander(f"{c.get('name','?')}  ({meta})", expanded=False):
                    for r in c.get("reports", []):
                        sev_dot = "\u25CF " if r["critical"] > 0 else (
                            "\u25D0 " if r["warning"] > 0 else (
                                "\u25CB " if r["finding_count"] == 0 else "\u25CB "
                            )
                        )
                        sev_color = "#ff5d63" if r["critical"] else (
                            "#f7b955" if r["warning"] else "#8390a3"
                        )
                        peer = " *(peer)*" if r["role"] == "peer_control" else ""
                        st.markdown(
                            f"<span style='color:{sev_color}'>{sev_dot}</span> "
                            f"**{r['period']}**{peer} \u2014 "
                            f"{r['finding_count']} findings "
                            f"(C{r['critical']} W{r['warning']} I{r['info']})",
                            unsafe_allow_html=True,
                        )
        st.divider()
        st.caption(f"Generated: {payload.get('generated_at_utc') or 'n/a'}")
        pages_url = payload.get("pages_url")
        if pages_url and "REPLACE_ME" not in pages_url:
            st.markdown(f"[Open public dashboard]({pages_url})")
        else:
            st.caption(
                "Public dashboard URL: set github_owner in "
                "config/provenance.json then re-run "
                "scripts/refresh_provenance.py."
            )


def main() -> None:
    st.set_page_config(
        page_title="Investment Red Flag Scanner",
        page_icon=":mag:",
        layout="wide",
    )

    # Foundry-style theme overlay. Streamlit's [theme] block in
    # .streamlit/config.toml handles the palette primitives; this
    # CSS adds the bits Streamlit can't theme: Inter + JetBrains
    # Mono fonts, uppercase tracked labels, dense data tables,
    # darker dataframe surfaces, and tighter section spacing.
    st.markdown(
        """
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
          :root {
            --fdy-bg:        #0b0e13;
            --fdy-panel:     #141a23;
            --fdy-panel-2:   #1a2230;
            --fdy-panel-3:   #212c3c;
            --fdy-border:    #2a3548;
            --fdy-text:      #eef2f8;
            --fdy-text-2:    #c2cad7;
            --fdy-muted:     #8390a3;
            --fdy-accent:    #3dd6f5;
            --fdy-critical:  #ff5d63;
            --fdy-warning:   #f7b955;
            --fdy-info:      #58c4ff;
            --fdy-ok:        #4ddc97;
            --fdy-font-ui:   "Inter", -apple-system, BlinkMacSystemFont,
                              "Segoe UI", "PingFang SC", "Microsoft YaHei",
                              Helvetica, Arial, sans-serif;
            --fdy-font-mono: "JetBrains Mono", "SF Mono", Menlo, Monaco,
                              Consolas, monospace;
          }
          html, body, [class*="css"], .stApp {
            font-family: var(--fdy-font-ui) !important;
            -webkit-font-smoothing: antialiased;
            font-feature-settings: "cv11", "ss01", "ss03";
            color: var(--fdy-text);
          }
          .stApp {
            background:
              radial-gradient(ellipse 80% 50% at 50% -10%,
                              rgba(61,214,245,0.06), transparent 60%),
              var(--fdy-bg) !important;
          }
          /* Title + headers tighter and higher-contrast */
          h1, h2, h3, h4, h5 {
            color: var(--fdy-text) !important;
            letter-spacing: -0.01em !important;
            font-weight: 700 !important;
          }
          h1 { font-size: 28px !important; }
          h2 { font-size: 20px !important; margin-top: 1.5rem !important; }
          h3 { font-size: 16px !important; }
          /* Captions / hint text legible */
          .stCaption, [data-testid="stCaptionContainer"],
          [data-testid="stMarkdownContainer"] p {
            color: var(--fdy-text-2);
          }
          /* Sidebar surface darker than main */
          [data-testid="stSidebar"] {
            background: #0f141b !important;
            border-right: 1px solid var(--fdy-border);
          }
          [data-testid="stSidebar"] * { color: var(--fdy-text); }
          [data-testid="stSidebar"] a {
            color: var(--fdy-accent) !important;
            text-decoration: none;
          }
          /* Numbers, code, metric values in mono */
          code, pre, kbd, samp,
          [data-testid="stMetricValue"],
          [data-testid="stMetricDelta"] {
            font-family: var(--fdy-font-mono) !important;
          }
          [data-testid="stMetricValue"] {
            font-size: 28px !important;
            font-weight: 700 !important;
            color: var(--fdy-text);
            font-feature-settings: "tnum";
          }
          [data-testid="stMetricLabel"] {
            text-transform: uppercase;
            letter-spacing: 0.12em;
            font-size: 10px !important;
            color: var(--fdy-muted) !important;
            font-weight: 700;
          }
          /* Buttons (Foundry-ish) */
          .stButton > button, .stDownloadButton > button {
            background: var(--fdy-panel-2);
            color: var(--fdy-text);
            border: 1px solid var(--fdy-border);
            font-weight: 600;
            letter-spacing: 0.02em;
            transition: border-color 120ms ease, background 120ms ease;
          }
          .stButton > button:hover, .stDownloadButton > button:hover {
            border-color: var(--fdy-accent);
            background: var(--fdy-panel-3);
            color: var(--fdy-accent);
          }
          /* Radio buttons (the scope selector) */
          [data-testid="stRadio"] label { color: var(--fdy-text-2); }
          /* Tabs */
          .stTabs [data-baseweb="tab"] {
            color: var(--fdy-text-2);
            font-weight: 600;
            letter-spacing: 0.02em;
          }
          .stTabs [aria-selected="true"] {
            color: var(--fdy-accent) !important;
            border-bottom-color: var(--fdy-accent) !important;
          }
          /* Dataframe styling */
          [data-testid="stDataFrame"] {
            background: var(--fdy-panel);
            border: 1px solid var(--fdy-border);
            border-radius: 6px;
            overflow: hidden;
          }
          /* Expander chrome */
          [data-testid="stExpander"] {
            background: var(--fdy-panel) !important;
            border: 1px solid var(--fdy-border) !important;
            border-radius: 6px !important;
          }
          [data-testid="stExpander"] summary {
            font-weight: 600;
            color: var(--fdy-text);
            letter-spacing: -0.005em;
          }
          /* Select / multiselect / text input */
          [data-baseweb="select"] > div,
          [data-baseweb="input"] > div,
          .stSelectbox > div > div {
            background: var(--fdy-panel) !important;
            border-color: var(--fdy-border) !important;
            color: var(--fdy-text) !important;
          }
          /* Section divider */
          hr { border-color: var(--fdy-border) !important; }
          /* Make the pipeline / roadmap cards (already styled inline in
             the page) align with the Foundry palette. Override the
             light-mode hardcoded colors that ship inside the existing
             _render_pipeline_overview() <style> block. */
          .pipeline-card {
            background: var(--fdy-panel) !important;
            border: 1px solid var(--fdy-border) !important;
            border-top: 3px solid var(--fdy-accent) !important;
            box-shadow: none !important;
          }
          .pipeline-step {
            background: transparent !important;
            color: var(--fdy-accent) !important;
            font-family: var(--fdy-font-mono) !important;
            letter-spacing: 0.14em !important;
            padding: 0 !important;
          }
          .pipeline-title {
            color: var(--fdy-text) !important;
            font-weight: 700 !important;
          }
          .pipeline-body {
            color: var(--fdy-text-2) !important;
            line-height: 1.6 !important;
          }
          .pipeline-body code {
            background: var(--fdy-panel-3) !important;
            color: var(--fdy-accent) !important;
          }
          .roadmap-card-head {
            background: var(--fdy-panel-2) !important;
            border: 1px solid var(--fdy-border) !important;
            border-left: 3px solid var(--fdy-accent) !important;
            color: var(--fdy-text) !important;
          }
          .roadmap-rank, .roadmap-kind,
          .roadmap-gain, .roadmap-covers, .roadmap-covers code,
          .roadmap-covers em, .roadmap-covers li {
            color: var(--fdy-text) !important;
          }
          .roadmap-unblock {
            background: rgba(247,185,85,0.16) !important;
            border: 1px solid rgba(247,185,85,0.4) !important;
            color: var(--fdy-warning) !important;
          }
          .roadmap-empty-note {
            background: rgba(77,220,151,0.08) !important;
            border: 1px solid rgba(77,220,151,0.35) !important;
            color: var(--fdy-text) !important;
          }
          .toc-nav a {
            color: var(--fdy-text-2) !important;
            border-left: 2px solid transparent !important;
            padding-left: 10px !important;
          }
          .toc-nav a:hover {
            border-left-color: var(--fdy-accent) !important;
            background: var(--fdy-panel-2) !important;
            color: var(--fdy-text) !important;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("Investment Red Flag Scanner")
    st.caption(
        "Deterministic offensive audit detectors with git anchored "
        "provenance. Priority score is a deterministic ranking heuristic, "
        "not a statistical test."
    )

    # === 1. Pipeline overview =============================================
    st.header("How it works", anchor="how-it-works")
    _render_pipeline_overview()

    # === Load data (single source for every section below) ================
    # Scope selector lives in-line above the leaderboard so it does not
    # crowd the sidebar table of contents.
    scope_cols = st.columns([2, 3, 2])
    with scope_cols[0]:
        scope_label = st.radio(
            "Scope",
            list(LEADERBOARDS.keys()),
            index=0,
            horizontal=True,
            key="scope_radio",
        )
    payload = _load(LEADERBOARDS[scope_label])
    findings = payload.get("top_findings") or []
    rows = [_flatten_finding(f) for f in findings]
    df = pd.DataFrame(rows)

    _render_sidebar_toc(payload)

    if df.empty:
        st.warning(
            "No findings in the selected leaderboard. Run "
            "`.venv/bin/python validation/run_real_report.py` first."
        )
        return

    # In-page filters live here -- one line, collapsible -- so they are
    # ergonomic but do not interfere with the sidebar table of contents.
    with st.expander("Filters", expanded=False):
        severities = sorted([s for s in df["Severity"].unique() if s])
        rules = sorted([r for r in df["Rule"].unique() if r])
        companies = sorted([c for c in df["Company"].unique() if c])
        fcols = st.columns(3)
        with fcols[0]:
            sel_sev = st.multiselect("Severity", severities, default=severities)
        with fcols[1]:
            sel_rule = st.multiselect("Rule", rules, default=rules)
        with fcols[2]:
            sel_co = st.multiselect("Company", companies, default=companies)

    filtered = df[
        df["Severity"].isin(sel_sev)
        & df["Rule"].isin(sel_rule)
        & df["Company"].isin(sel_co)
    ].reset_index(drop=True)

    # === 2. Findings leaderboard ==========================================
    st.header("Findings leaderboard", anchor="findings-leaderboard")
    _render_leaderboard_section(filtered)

    # === 3. Logic chain landscape (Sankey) ================================
    st.header("Logic chain landscape", anchor="logic-chain-landscape")
    _render_landscape_section()

    # === 4. Triangulation matrix + audit roadmap ==========================
    st.header("Triangulation matrix", anchor="triangulation-matrix")
    _render_triangulation_matrix_section()

    # === 5. Drill into a finding ==========================================
    st.header("Drill into a finding", anchor="drill-into-a-finding")
    _render_drill_section(filtered)

    # === 6. Embedded source PDF ==========================================
    st.header("Embedded source PDF", anchor="embedded-source-pdf")
    st.caption(
        "Single shared viewer driven by the row currently selected in the "
        "leaderboard. Click any 'Show in PDF below' button in the Drill "
        "section above to (a) re-scroll this viewer to the corresponding "
        "bbox and (b) slide the page down here. Repeated clicks are "
        "supported -- each click forces the viewer to re-mount with the "
        "new scroll target."
    )
    if not filtered.empty:
        max_idx = len(filtered) - 1
        sel_idx = int(st.session_state.get(_SELECTED_IDX_KEY, 0))
        sel_idx = max(0, min(sel_idx, max_idx))
        pdf_row = filtered.iloc[sel_idx].to_dict()
        raw_pdf_row = filtered.iloc[sel_idx]
        pdf_row["_raw"] = raw_pdf_row["_raw"]
        pdf_row["_provenance"] = raw_pdf_row["_provenance"]
        pdf_row["_snippet"] = raw_pdf_row.get("_snippet")
        pdf_row["_metric_evidence"] = raw_pdf_row.get("_metric_evidence")
        _render_pdf_panel(pdf_row, _collect_evidence_items(pdf_row))


if __name__ == "__main__":
    main()
