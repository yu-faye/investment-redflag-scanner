/* Investment Red Flag Scanner dashboard
 *
 * Loads two leaderboard JSONs (middelborg + all), wires the filters
 * and the report-library sidebar, renders the table and exposes a
 * per-row drill panel + an embedded PDF viewer with the matched
 * evidence highlighted.
 */

const SCOPES = {
  middelborg: { file: "../outputs/middelborg_leaderboard.json", data: null },
  all: { file: "../outputs/leaderboard.json", data: null },
};

// Auxiliary payloads used inside the per-finding drill panel only.
// Loaded once at startup; loading failures simply hide the relevant
// sub-blocks in the drill.
const AUX_FILES = {
  trees: "../outputs/argument_trees.json",
  paragraphs: "../outputs/narrative_paragraphs.json",
};
let v10Trees = null;
let v10Paragraphs = null;

// Report library: company -> reports tree shown in the sidebar so the
// analyst can see every report that has been ingested (not just the
// ones that produced findings). Optional; load failure just hides
// the section.
const REPORT_LIBRARY_FILE = "../outputs/report_library.json";
let reportLibrary = null;

const state = {
  scope: "middelborg",
  severity: "",
  rule: "",
  company: "",
  // When a report library node is clicked, leaderboard rows are
  // filtered to that company + the concrete prov.period strings that
  // joined to that report in run_real_report.build_report_library
  // (e.g. company="Techstep ASA", libraryFilterPeriods=["2025-FY"]).
  libraryFilterCompany: "",
  libraryFilterPeriods: null,
  // Human label shown in the active-filter banner above the table.
  libraryFilterLabel: "",
  // The currently drilled finding (composite_key). Drives the
  // "Why we flagged it" + "Source PDF" sections at the bottom of
  // the page.
  selectedKey: null,
};

document.addEventListener("DOMContentLoaded", () => {
  bindControls();
  bindTocScrollSpy();
  Promise.all(
    Object.entries(SCOPES).map(([key, conf]) =>
      fetchJson(conf.file).then((data) => {
        SCOPES[key].data = data;
      })
    )
  )
    .then(() => {
      populateCompanyFilter();
      populateMeta();
      // Default-select the top finding so the Drill + PDF sections are
      // non-empty on first render.
      const data = SCOPES[state.scope].data || { top_findings: [] };
      if (data.top_findings && data.top_findings[0]) {
        state.selectedKey = compositeKeyOf(data.top_findings[0]);
      }
      render();
    })
    .catch((err) => {
      document.getElementById("leaderboard-body").innerHTML =
        `<tr><td colspan="8" style="color:var(--critical)">Failed to load JSON payloads: ${escapeHtml(
          String(err)
        )}</td></tr>`;
    });

  // Drill-panel aux payloads: tolerant load. Missing files just hide
  // the corresponding sub-blocks inside the drill panel.
  Promise.all([
    fetchJson(AUX_FILES.trees).catch(() => null),
    fetchJson(AUX_FILES.paragraphs).catch(() => null),
  ]).then(([t, p]) => {
    v10Trees = t;
    v10Paragraphs = p;
    // Re-render rows once narrative paragraphs / argument trees have
    // arrived so the drill panel picks them up.
    if (SCOPES[state.scope].data) render();
  });

  // Report library sidebar: build from the dedicated payload first,
  // fall back to deriving from the leaderboard if the payload isn't
  // there yet (older runs).
  fetchJson(REPORT_LIBRARY_FILE)
    .then((d) => { reportLibrary = d; renderReportLibrary(); })
    .catch(() => {
      // Fallback: derive a minimal library from the leaderboard data
      // once it's loaded.
      const tryDerive = () => {
        const data = SCOPES.all.data || SCOPES.middelborg.data;
        if (!data) { setTimeout(tryDerive, 400); return; }
        reportLibrary = deriveLibraryFromLeaderboard(data);
        renderReportLibrary();
      };
      tryDerive();
    });
});

function fetchJson(path) {
  return fetch(path, { cache: "no-store" }).then((r) => {
    if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}`);
    return r.json();
  });
}

function bindControls() {
  document.querySelectorAll(".scope-tabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document
        .querySelectorAll(".scope-tabs .tab")
        .forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.scope = btn.dataset.scope;
      populateCompanyFilter();
      render();
    });
  });
  ["filter-severity", "filter-rule", "filter-company"].forEach((id) => {
    document.getElementById(id).addEventListener("change", (e) => {
      const key = id.replace("filter-", "");
      state[key] = e.target.value;
      render();
    });
  });
}

function populateCompanyFilter() {
  const data = SCOPES[state.scope].data || { top_findings: [] };
  const sel = document.getElementById("filter-company");
  const current = state.company;
  const companies = uniq(
    data.top_findings.map((f) => f.company_name || f.company)
  ).sort();
  sel.innerHTML =
    '<option value="">all</option>' +
    companies
      .map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`)
      .join("");
  if (companies.includes(current)) {
    sel.value = current;
  } else {
    state.company = "";
  }
}

function populateMeta() {
  const data = SCOPES.middelborg.data || SCOPES.all.data || {};
  document.getElementById("meta-generated").textContent =
    data.generated_at_utc || "n/a";
  const sample = (data.top_findings || [])[0] || {};
  const prov = (sample.provenance || {});
  const owner = prov.owner;
  const repo = prov.repo;
  const repoEl = document.getElementById("meta-repo");
  if (owner && repo && owner !== "REPLACE_ME") {
    repoEl.innerHTML = `<a href="https://github.com/${escapeHtml(
      owner
    )}/${escapeHtml(repo)}" target="_blank" rel="noopener">${escapeHtml(
      owner + "/" + repo
    )}</a>`;
  } else if (repo) {
    repoEl.textContent = `${owner || "<owner>"}/${repo}`;
  } else {
    repoEl.textContent = "n/a";
  }
  const pagesEl = document.getElementById("meta-pages");
  if (data.pages_url && !data.pages_url.includes("REPLACE_ME")) {
    pagesEl.innerHTML = `<a href="${escapeHtml(
      data.pages_url
    )}" target="_blank" rel="noopener">${escapeHtml(data.pages_url)}</a>`;
  } else {
    pagesEl.textContent = data.pages_url || "n/a";
  }
}

function render() {
  const data = SCOPES[state.scope].data || { top_findings: [] };
  const filtered = data.top_findings.filter(matchesFilters);
  renderKpis(filtered);
  renderRows(filtered);
  // v11 layout: drill + embedded PDF sections at the bottom of the page
  // re-render whenever the selection or the filtered set changes.
  renderDrillSection(data.top_findings);
  renderPdfSection(data.top_findings);
}

/* The Drill + PDF sections read from the *unfiltered* findings list so
 * that a selected finding remains visible even if the analyst narrows
 * the leaderboard filter. */
function findFindingByKey(all, key) {
  if (!all || !key) return null;
  for (const f of all) {
    if (compositeKeyOf(f) === key) return f;
  }
  return null;
}

function matchesFilters(f) {
  if (state.severity && f.severity !== state.severity) return false;
  if (state.rule && f.rule_id !== state.rule) return false;
  if (state.company) {
    const label = f.company_name || f.company;
    if (label !== state.company) return false;
  }
  if (state.libraryFilterCompany) {
    const label = f.company_name || f.company || "";
    if (label !== state.libraryFilterCompany) return false;
  }
  if (state.libraryFilterPeriods && state.libraryFilterPeriods.length) {
    // Match against the concrete prov.period strings the library
    // payload told us joined to this report (set up server-side in
    // build_report_library). prov.label is unreliable for joining
    // because the loader rewrites it.
    const prov = f.provenance || {};
    const period = prov.period || "";
    if (!state.libraryFilterPeriods.includes(period)) return false;
  }
  return true;
}

function renderKpis(rows) {
  const counts = { critical: 0, warning: 0, info: 0 };
  rows.forEach((r) => {
    if (counts[r.severity] !== undefined) counts[r.severity] += 1;
  });
  document.getElementById("kpi-total").textContent = rows.length;
  document.getElementById("kpi-critical").textContent = counts.critical;
  document.getElementById("kpi-warning").textContent = counts.warning;
  document.getElementById("kpi-info").textContent = counts.info;
}

function renderRows(rows) {
  const tbody = document.getElementById("leaderboard-body");
  const empty = document.getElementById("empty-note");
  if (!rows.length) {
    tbody.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  tbody.innerHTML = rows.map((row, idx) => renderRow(row, idx + 1)).join("");
  tbody.querySelectorAll(".toggle-excerpt").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const cell = e.currentTarget
        .closest("tr")
        .querySelector(".col-headline");
      cell.classList.toggle("show-excerpt");
      const open = cell.classList.contains("show-excerpt");
      const hadSnippet = cell.querySelector(".evidence-snippet");
      const closedLabel = hadSnippet ? "Show evidence" : "Read in context";
      e.currentTarget.textContent = open ? "Hide evidence" : closedLabel;
    });
  });
  // v11 layout: per-row drill trigger. Sets state.selectedKey, re-renders
  // the Drill + PDF sections below, and smooth-scrolls the page to the
  // Drill section anchor.
  tbody.querySelectorAll(".drill-trigger").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const key = a.dataset.key;
      if (key) {
        state.selectedKey = key;
        render();
        const drill = document.getElementById("drill-section");
        if (drill) drill.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });
}

function renderRow(f, rank) {
  const prov = f.provenance || {};
  const locator = prov.excerpt_locator || {};
  const snippet = f.evidence_snippet || prov.evidence_snippet;
  const metricEvidence = f.metric_evidence || [];
  const externalEvidence = f.external_evidence || [];
  // Prefer phrase-anchored URL (Chrome built-in PDF viewer parses ?search=)
  const issuer =
    prov.issuer_url_at_phrase ||
    prov.issuer_url_at_page ||
    prov.issuer_url ||
    prov.issuer_page;
  const githubUrl =
    prov.github_blob_url_at_phrase ||
    prov.github_blob_url_at_page ||
    prov.github_blob_url ||
    prov.github_head_url;
  let localPath = null;
  if (prov.local_path) {
    let fragment = "";
    if (prov.pdf_page_hit) {
      fragment = `#page=${prov.pdf_page_hit}`;
      if (locator.normalised_search) {
        fragment += `&search=${encodeURIComponent(locator.normalised_search)}`;
      }
    }
    localPath = "../" + prov.local_path + fragment;
  }
  const headline = typeof f.headline === "string"
    ? f.headline
    : (f.headline && (f.headline.en || "")) || "";
  const excerpt = f.claim_excerpt || "";
  const consolFlag = f.consolidation_caveat
    ? '<span class="consol-flag" title="IFRS consolidation guardrail">IFRS</span>'
    : "";
  const pageHint = prov.pdf_page_hit
    ? `p.${prov.pdf_page_hit}${
        locator.bbox
          ? ` · bbox y${Math.round(locator.bbox.top)}`
          : ""
      }`
    : "";

  const key = compositeKeyOf(f);
  const selected = state.selectedKey === key ? " selected" : "";
  return `
    <tr>
      <td class="col-drill">
        <a class="drill-trigger${selected}" data-key="${escapeHtml(key)}" href="#drill-section" title="Drill into this finding below">\u2193</a>
      </td>
      <td class="col-rank">${rank}</td>
      <td class="col-company">
        <span class="company-name">${escapeHtml(
          f.company_name || f.company
        )}</span>
        <span class="company-tags">${escapeHtml(
          (prov.period ? prov.period + " · " : "") + (prov.label || "")
        )}</span>
      </td>
      <td class="col-rule">${escapeHtml(f.rule_id || "")}</td>
      <td class="col-sev"><span class="sev-badge sev-${escapeHtml(
        f.severity || ""
      )}">${escapeHtml(f.severity || "")}</span></td>
      <td class="col-priority">${formatPriority(f.priority_score)}</td>
      <td class="col-headline">
        <div>${escapeHtml(headline)}${consolFlag}</div>
        ${renderContextDrawer(locator.sentence_context, excerpt, snippet, metricEvidence, prov, locator, externalEvidence, f)}
      </td>
      <td class="col-jump">
        <div class="jump-buttons">
          ${jumpLink(
            locator.normalised_search ? "Issuer PDF (jump+highlight)" : "Open issuer PDF",
            issuer,
            locator.normalised_search
              ? "Opens issuer PDF at the page and asks the browser PDF viewer to search the phrase"
              : "Issuer-hosted PDF; opens at the matched page when available"
          )}
          ${jumpLink(
            "GitHub permalink",
            githubUrl,
            "Immutable commit-pinned URL of the exact PDF we scanned"
          )}
          ${jumpLink(
            "Open local file",
            localPath,
            "file:// link; useful for offline review"
          )}
        </div>
        ${pageHint ? `<span class="jump-page-hint">${escapeHtml(pageHint)}</span>` : ""}
        ${
          snippet || locator.sentence_context || excerpt || v10HasContent(f)
            ? `<button class="toggle-excerpt">${
                snippet ? "Show evidence" : "Read reasoning"
              }</button>`
            : ""
        }
      </td>
    </tr>
  `;
}

/* Renders the sentence-context drawer.
 * Every snippet image (narrative + per-metric) is wrapped in <a> so clicking
 * it opens the source PDF at the exact page (and -- when the browser PDF
 * viewer supports the syntax -- with `search=` to land on the matched phrase).
 *
 * Per-snippet jump target priority:
 *   1. issuer URL + #page=N&search=phrase   (best: works in Chrome/Firefox
 *      built-in PDF viewer; analyst lands on the publisher's hosted PDF)
 *   2. GitHub blob raw URL + #page=N        (immutable commit pin)
 *   3. local file:// URL  + #page=N         (offline fallback)
 */
function renderContextDrawer(ctx, excerpt, snippet, metricEvidence, prov, locator, externalEvidence, finding) {
  externalEvidence = externalEvidence || [];
  // v10: never early-return when narrative paragraphs / argument trees
  // are available -- those carry the audit story even for triangulated
  // findings with no PDF claim_excerpt.
  const fHasV10 = finding && v10HasContent(finding);
  if (
    !ctx &&
    !excerpt &&
    !snippet &&
    !(metricEvidence && metricEvidence.length) &&
    !(externalEvidence && externalEvidence.length) &&
    !fHasV10
  )
    return '<div class="excerpt"></div>';

  const narrativeJump = buildSnippetJump(prov, snippet && snippet.page, (locator || {}).normalised_search);

  const snippetHtml = snippet && snippet.path
    ? wrapSnippetLink(
        narrativeJump,
        `<figure class="evidence-snippet clickable" title="Open source PDF at page ${escapeHtml(snippet.page || "?")} ${narrativeJump ? '(opens in new tab)' : '(no source URL available)'}">
           <img src="../${escapeHtml(snippet.path)}" alt="Evidence snippet from page ${escapeHtml(snippet.page || "?")}" loading="lazy" />
           <figcaption>${narrativeJump ? "&#x21D7; " : ""}Narrative snippet \u00b7 page ${escapeHtml(snippet.page || "?")} \u00b7 ${escapeHtml(snippet.width || "?")}\u00d7${escapeHtml(snippet.height || "?")} px</figcaption>
         </figure>`
      )
    : "";

  let bodyHtml = "";
  if (ctx) {
    const prev = ctx.prev || "";
    const match = ctx.match || excerpt || "";
    const next = ctx.next || "";
    bodyHtml = `
      <div class="ctx-text">
        ${prev ? `<span class="ctx-prev">${escapeHtml(prev)}</span> ` : ""}
        <mark class="ctx-match">${escapeHtml(match)}</mark>
        ${next ? ` <span class="ctx-next">${escapeHtml(next)}</span>` : ""}
      </div>
    `;
  } else if (excerpt) {
    bodyHtml = `<div class="ctx-text"><mark>${escapeHtml(excerpt)}</mark></div>`;
  }

  const metricsHtml = renderMetricEvidence(metricEvidence, prov);
  const externalHtml = renderExternalCollision(externalEvidence, finding || {});
  // v10: narrative paragraph + argument tree appear *first* so the analyst
  // sees the deterministic auditor summary before any raw snippets.
  const v10Html = renderV10Block(finding || {});

  return `<div class="excerpt context">${v10Html}${snippetHtml}${bodyHtml}${metricsHtml}${externalHtml}</div>`;
}

/* v8: External collision block. PDF claim left (already shown above in the
 * narrative snippet + sentence context) so this block focuses on the
 * external side: which public registry, what records came back, what
 * variance the detector computed. Every confirmed-award row is a clickable
 * link to the original Doffin notice page (opens in new tab).
 */
function renderExternalCollision(externalEvidence, finding) {
  if (!externalEvidence || !externalEvidence.length) return "";
  const blocks = externalEvidence.map((ee) => {
    const src = ee.source || "external";
    const srcLabel = ee.source_label || src;
    const supplier = ee.supplier_name || finding.subsidiary || "";
    const confirmed = ee.confirmed_award_count ?? (ee.confirmed_awards || []).length;
    const hits = ee.search_hits_total ?? "?";
    const variance = `${confirmed} confirmed award${confirmed === 1 ? "" : "s"} / ${hits} search hit${hits === 1 ? "" : "s"}`;
    const fetched = ee.fetched_at_utc || "?";
    const sha = (ee.cache_sha256 || "").slice(0, 16);

    const rows = (ee.confirmed_awards || []).map((a) => {
      const date = a.publication_date || a.issue_date || "?";
      const buyer = (a.buyer_names || []).join(", ") || "?";
      const heading = a.heading || "";
      const winners = (a.awarded_names || []).join(", ");
      const url = a.public_url || (a.notice_id ? `https://www.doffin.no/notices/${a.notice_id}` : null);
      const linkOpen = url ? `<a class="ec-notice-link" href="${escapeHtml(url)}" target="_blank" rel="noopener">` : "<span class='ec-notice-link'>";
      const linkClose = url ? "</a>" : "</span>";
      return `
        <tr>
          <td class="ec-cell-date">${escapeHtml(date)}</td>
          <td class="ec-cell-buyer">${escapeHtml(buyer)}</td>
          <td class="ec-cell-heading">${linkOpen}${escapeHtml(heading.slice(0, 90))}${heading.length > 90 ? "..." : ""}${linkClose}</td>
          <td class="ec-cell-winners">${escapeHtml(winners)}</td>
        </tr>
      `;
    }).join("");

    const emptyHint = !rows
      ? `<div class="ec-empty">No confirmed award records returned for <code>${escapeHtml(supplier)}</code>. Search returned ${escapeHtml(String(hits))} text-match hit${hits === 1 ? "" : "s"} but none had <code>${escapeHtml(supplier)}</code> in the official <code>awardedNames</code> field.</div>`
      : "";

    return `
      <div class="ec-block ec-sev-${escapeHtml(finding.severity || "")}">
        <div class="ec-header">
          <span class="ec-badge">${escapeHtml(srcLabel)}</span>
          <span class="ec-supplier"><code>${escapeHtml(supplier)}</code></span>
          <span class="ec-variance">${escapeHtml(variance)}</span>
        </div>
        ${rows ? `<table class="ec-table"><thead><tr><th>Published</th><th>Buyer</th><th>Heading (click for notice)</th><th>All awarded suppliers</th></tr></thead><tbody>${rows}</tbody></table>` : ""}
        ${emptyHint}
        <div class="ec-provenance">
          query: <a href="${escapeHtml(ee.query_url || "#")}" target="_blank" rel="noopener">${escapeHtml(ee.query_url || "n/a")}</a>
          &nbsp;\u00b7&nbsp; cached: <code>${escapeHtml(ee.cache_path || "n/a")}</code>
          &nbsp;\u00b7&nbsp; sha256: <code>${escapeHtml(sha)}\u2026</code>
          &nbsp;\u00b7&nbsp; fetched: <code>${escapeHtml(fetched)}</code>
        </div>
      </div>
    `;
  });
  return `
    <div class="external-collision-block">
      <div class="external-collision-title">External collision (click any heading to open the Doffin notice)</div>
      ${blocks.join("")}
    </div>
  `;
}

/* Builds the per-snippet jump URL using the same precedence (issuer >
 * github > local) as the row-level jump buttons. `searchPhrase` is optional;
 * when omitted, the URL has only a #page=N anchor. */
function buildSnippetJump(prov, page, searchPhrase) {
  if (!prov || !page) return null;
  const frag = "#page=" + encodeURIComponent(page) +
    (searchPhrase ? "&search=" + encodeURIComponent(searchPhrase) : "");
  if (prov.issuer_url) return prov.issuer_url + frag;
  if (prov.github_raw_url) return prov.github_raw_url + frag;
  if (prov.github_blob_url) return prov.github_blob_url + frag;
  if (prov.local_path) return "../" + prov.local_path + frag;
  return null;
}

function wrapSnippetLink(url, innerHtml) {
  if (!url) return innerHtml;
  return `<a class="snippet-link" href="${escapeHtml(url)}" target="_blank" rel="noopener">${innerHtml}</a>`;
}

/* Per-number provenance block. Each metric value cited by the finding gets a
 * row with a source badge (auto-extracted / manually curated / best-effort /
 * unverified) and -- when the locator produced a bbox -- a clickable PNG
 * snippet cropped from the source PDF.
 */
function renderMetricEvidence(metricEvidence, prov) {
  if (!metricEvidence || !metricEvidence.length) return "";
  const rows = metricEvidence.map((me) => {
    const src = me.source || "unverified";
    const badgeLabel = {
      auto_regex: "auto-extracted",
      manual_curation: "manually curated",
      manual_unverified: "best-effort match",
      unverified: "not located",
    }[src] || src;
    const snip = me.snippet;
    const jump = snip && snip.page
      ? buildSnippetJump(prov, snip.page, snip.matched_str)
      : null;
    const figure = snip && snip.path
      ? `<img class="metric-snippet-img" src="../${escapeHtml(snip.path)}" alt="${escapeHtml(me.metric)} = ${escapeHtml(me.value)} on page ${escapeHtml(snip.page || "?")}" loading="lazy" />`
      : `<span class="metric-snippet-missing">no PDF row located</span>`;
    const figureWithLink = (snip && snip.path)
      ? wrapSnippetLink(jump, `<span class="metric-snippet-anchor${jump ? ' clickable' : ''}" title="${jump ? 'Open source PDF at page ' + (snip.page || '?') + ' (opens in new tab)' : 'No source URL'}">${figure}</span>`)
      : figure;
    const meta = snip
      ? `${jump ? "&#x21D7; " : ""}page ${escapeHtml(snip.page || "?")}${
          snip.confidence ? ` \u00b7 conf ${escapeHtml(snip.confidence)}` : ""
        }${snip.context_term ? ` \u00b7 near "${escapeHtml(snip.context_term)}"` : ""}`
      : "";
    const note = me.note ? `<div class="metric-snippet-note">${escapeHtml(me.note)}</div>` : "";
    return `
      <div class="metric-snippet-row metric-src-${escapeHtml(src)}">
        <div class="metric-snippet-head">
          <span class="metric-snippet-key"><code>${escapeHtml(me.metric)}</code> = <strong>${escapeHtml(me.value)}</strong></span>
          <span class="metric-source-badge badge-${escapeHtml(src)}">${escapeHtml(badgeLabel)}</span>
          ${meta ? `<span class="metric-snippet-meta">${meta}</span>` : ""}
        </div>
        ${figureWithLink}
        ${note}
      </div>
    `;
  });
  return `
    <div class="metric-evidence-block">
      <div class="metric-evidence-title">Per-number provenance (click any snippet to open the source PDF)</div>
      ${rows.join("")}
    </div>
  `;
}

function jumpLink(label, url, title) {
  if (!url) {
    return `<a class="disabled" title="not available">${escapeHtml(
      label
    )}</a>`;
  }
  const safeUrl = escapeHtml(url);
  return `<a href="${safeUrl}" target="_blank" rel="noopener" title="${escapeHtml(
    title
  )}">${escapeHtml(label)}</a>`;
}

function formatPriority(v) {
  if (v === undefined || v === null) return "";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toFixed(1);
}

function uniq(arr) {
  return Array.from(new Set(arr.filter((x) => x !== undefined && x !== null)));
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/* ------------------------- Per-finding cross-check map ------------------ */
/* Plain-language verdict labels used inside the drill panel only. */
const VERDICT_LABEL = {
  confirms:   { glyph: "\u2713", label: "matches the report",      cls: "vd-confirms"  },
  partial:    { glyph: "\u25D0", label: "partly matches",          cls: "vd-partial"   },
  refutes:    { glyph: "\u2717", label: "contradicts the report",  cls: "vd-refutes"   },
  not_found:  { glyph: "\u2298", label: "nothing in the registry", cls: "vd-not-found" },
  neutral:    { glyph: "\u25E6", label: "no clear signal",         cls: "vd-neutral"   },
  error:      { glyph: "!",      label: "lookup failed",           cls: "vd-error"     },
};

/* Renders the per-finding "What we checked" panel for a triangulated
 * finding. Reads from finding.triangulation.verdicts (one row per
 * external source we queried) and prints a plain-language table:
 *   source name -> what came back -> what it means
 * For non-triangulated rules (narrative_dissonance, selective_disclosure,
 * lag_causality) returns "" so the drill panel just shows the narrative
 * paragraph + argument tree.
 */
function renderPerFindingCrossCheck(f) {
  const t = f && f.triangulation;
  if (!t) return "";
  const verdicts = t.verdicts || [];
  if (!verdicts.length) return "";
  const rows = verdicts.map((v) => {
    const meta = VERDICT_LABEL[v.verdict] || { glyph: "?", label: v.verdict || "?", cls: "vd-other" };
    const srcLabel = (v.tap_kind || v.tap_id || "external source").replace(/_/g, " ");
    const narrative = v.narrative || v.summary || "";
    return `
      <tr class="cc-row ${meta.cls}">
        <td class="cc-source">${escapeHtml(srcLabel)}</td>
        <td class="cc-verdict"><span class="cc-glyph">${meta.glyph}</span> ${escapeHtml(meta.label)}</td>
        <td class="cc-narrative">${escapeHtml(narrative)}</td>
      </tr>`;
  }).join("");
  const sev = t.derived_severity || f.severity || "info";
  return `
    <div class="cross-check-block">
      <div class="cross-check-head">
        <strong>What we checked, in plain English</strong>
        <span class="cc-sev cc-sev-${escapeHtml(sev)}">Final read: ${escapeHtml(sev)}</span>
      </div>
      <table class="cross-check-table">
        <thead><tr><th>Where we looked</th><th>What it said</th><th>What we found</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

/* ------------------------- Drill panel helpers -------------------------- */

function compositeKeyOf(f) {
  if (f.composite_key) return f.composite_key;
  // headline may be a string or a legacy {en: "..."} bilingual object.
  let h = f.headline;
  if (h && typeof h === "object") h = h.en || "";
  h = String(h || "?");
  return `${f.company || "?"}|${f.rule_id || "?"}|${
    f.hypothesis_id || h.slice(0, 80)
  }`;
}

function v10HasContent(f) {
  const key = compositeKeyOf(f);
  const hasPara = !!(v10Paragraphs && v10Paragraphs.paragraphs && v10Paragraphs.paragraphs[key]);
  const hasTree = !!(v10Trees && v10Trees.trees && v10Trees.trees[key]);
  return hasPara || hasTree;
}

function renderV10Block(f) {
  const key = compositeKeyOf(f);
  const para = v10Paragraphs && v10Paragraphs.paragraphs && v10Paragraphs.paragraphs[key];
  const tree = v10Trees && v10Trees.trees && v10Trees.trees[key];
  if (!para && !tree) return "";
  const paraHtml = para ? renderNarrativeParagraph(para) : "";
  const treeHtml = tree ? renderArgumentTreeOpen(tree) : "";
  return `<div class="v10-block">${paraHtml}${treeHtml}</div>`;
}

function renderNarrativeParagraph(para) {
  if (!para) return "";
  // Replace [N] markers in body with clickable <a> anchors to the
  // citation list below.
  const bodyHtml = escapeHtml(para.body || "").replace(/\[(\d+)\]/g, (_, n) =>
    `<a class="v10-cite" href="#cite-${n}" data-cite="${n}">[${n}]</a>`
  );
  const citationsList = Object.entries(para.citations || {})
    .map(([num, c]) => {
      const href = c.href || "";
      const safeHref = href ? escapeHtml(href) : "";
      const safeLabel = escapeHtml(c.label || "evidence");
      const inner = safeHref
        ? `<a href="${safeHref}" target="_blank" rel="noopener">${safeLabel}</a>`
        : `<span class="v10-cite-nolink">${safeLabel}</span>`;
      return `<li id="cite-${escapeHtml(num)}" class="v10-cite-${escapeHtml(c.kind || "evidence")}"><span class="v10-cite-num">[${escapeHtml(num)}]</span> ${inner}</li>`;
    })
    .join("");
  return `
    <div class="v10-narrative">
      <div class="v10-narrative-head">${escapeHtml(para.headline || "")}</div>
      <p class="v10-narrative-body">${bodyHtml}</p>
      ${citationsList ? `<ol class="v10-citations">${citationsList}</ol>` : ""}
    </div>
  `;
}

function renderArgumentTreeOpen(tree) {
  if (!tree) return "";
  // Collapsed by default. The drill panel has plenty of content
  // without it; the analyst can expand when they want the
  // step-by-step breakdown.
  return `
    <details class="v10-tree-root">
      <summary><span class="v10-tree-tag">Step by step reasoning</span> click to expand</summary>
      ${renderArgumentTreeNode(tree, 0)}
    </details>
  `;
}

function renderArgumentTreeNode(node, depth) {
  if (!node) return "";
  const links = (node.links || []).filter((l) => l && l.href).map((l) =>
    `<a class="v10-tree-link" href="${escapeHtml(l.href)}" target="_blank" rel="noopener">${escapeHtml(l.label)}</a>`
  ).join(" \u00b7 ");
  const meta = node.metadata && Object.keys(node.metadata).length
    ? `<dl class="v10-tree-meta">${Object.entries(node.metadata).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) =>
        `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(typeof v === "object" ? JSON.stringify(v) : String(v))}</dd>`
      ).join("")}</dl>`
    : "";
  const detailHtml = node.detail
    ? `<div class="v10-tree-detail">${escapeHtml(node.detail)}</div>`
    : "";
  const children = (node.children || []).map((c) => renderArgumentTreeNode(c, depth + 1)).join("");
  const sevClass = node.severity ? `v10-tree-sev-${node.severity}` : "";
  const vClass = node.verdict ? `v10-tree-v-${node.verdict}` : "";
  const isOpen = depth < 2; // root and immediate children expanded; deeper collapsed.
  return `
    <details class="v10-tree-node v10-tree-${node.kind} ${sevClass} ${vClass}"${isOpen ? " open" : ""}>
      <summary><span class="v10-tree-glyph">${escapeHtml(node.glyph || "")}</span> <span class="v10-tree-label">${escapeHtml(node.label)}</span></summary>
      ${detailHtml}
      ${links ? `<div class="v10-tree-links">${links}</div>` : ""}
      ${meta}
      ${children}
    </details>
  `;
}

/* ------------------------- Drill section -------------------------------- */
/* Builds the "Why we flagged it" panel for the currently selected
 * finding. Stack from top to bottom:
 *   1. Header: company name, rule, severity, headline, jump-to-PDF
 *   2. Plain-English summary paragraph (narrative_writer output)
 *   3. "What we checked, in plain English" cross-check table -- only
 *      for triangulated_hypothesis findings; other rule families skip
 *      this since they have no external verdict table
 *   4. Step-by-step reasoning tree (collapsible, defaults closed)
 */
function renderDrillSection(allFindings) {
  const host = document.getElementById("drill-host");
  if (!host) return;
  const f = findFindingByKey(allFindings, state.selectedKey);
  if (!f) {
    host.innerHTML = `<p class="drill-empty">Click the \u2193 arrow in front of any row in the table above to read the full reasoning here.</p>`;
    return;
  }
  const headline = typeof f.headline === "string"
    ? f.headline
    : (f.headline && f.headline.en) || "";
  const sev = f.severity || "info";
  const sevBadge = `<span class="sev-badge sev-${escapeHtml(sev)}">${escapeHtml(sev)}</span>`;
  const prov = f.provenance || {};
  const page = prov.pdf_page_hit;
  const hasPdf = !!prov.local_path || !!prov.github_raw_url || !!prov.issuer_url;
  const jumpBtn = hasPdf
    ? `<button type="button" id="drill-jump-pdf" class="drill-jump-pdf" title="Scroll to the source PDF below, opened at ${page ? "page " + page : "the start"} with the evidence highlighted in yellow.">
         Show me in the PDF below${page ? " &middot; page " + escapeHtml(String(page)) : ""} &darr;
       </button>`
    : "";
  const ruleLabel = friendlyRuleName(f.rule_id);
  const reportLabel = prov.label || prov.period || "";
  const headerHtml = `
    <div class="drill-head">
      <h3>${escapeHtml(f.company_name || f.company || "")} ${sevBadge}</h3>
      <p class="drill-subline">
        <span class="drill-rule">${escapeHtml(ruleLabel)}</span>
        ${reportLabel ? `<span class="drill-report">${escapeHtml(reportLabel)}</span>` : ""}
      </p>
      <p class="drill-blockquote">${escapeHtml(headline)}</p>
      ${jumpBtn}
    </div>
  `;
  const narrativeHtml = renderV10Block(f) || `<p class="drill-empty">No plain-English summary written for this finding yet.</p>`;
  const crossCheckHtml = renderPerFindingCrossCheck(f);
  host.innerHTML = headerHtml + narrativeHtml + crossCheckHtml;
  const btn = document.getElementById("drill-jump-pdf");
  if (btn) {
    btn.addEventListener("click", () => {
      const pdfSec = document.getElementById("pdf-section");
      if (pdfSec) pdfSec.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
}

/* Friendly label for an internal rule_id. Falls back to the id itself
 * when the rule isn't in the map; that's intentional so newly added
 * detectors at least show their id rather than an empty string. */
function friendlyRuleName(ruleId) {
  const map = {
    narrative_dissonance: "Story vs numbers",
    selective_disclosure: "What the report leaves out",
    lag_causality: "Said vs delivered",
    triangulated_hypothesis: "Report vs outside sources",
  };
  return map[ruleId] || ruleId || "";
}

/* ------------------------- Embedded PDF section ------------------------- */
/* Renders an iframe pointing at our PDF.js based viewer (pdf-viewer.html)
 * with ?file=, ?page= and ?search= URL params. The viewer renders the
 * PDF to canvas + a selectable text layer and applies an <mark> overlay
 * to the search phrase. Works identically in Chrome, Firefox, Safari
 * and Edge (and on iOS Safari, where native PDF embedding fails). */
function renderPdfSection(allFindings) {
  const host = document.getElementById("pdf-host");
  if (!host) return;
  const f = findFindingByKey(allFindings, state.selectedKey);
  if (!f) {
    host.innerHTML = `<p class="drill-empty">Select a finding above to load its source PDF here.</p>`;
    return;
  }
  const prov = f.provenance || {};
  const locator = prov.excerpt_locator || {};
  const page = prov.pdf_page_hit;
  // Highlight phrase: prefer the locator's normalised search string,
  // fall back to the matched substring from the evidence snippet.
  let searchPhrase = "";
  if (locator.normalised_search) searchPhrase = locator.normalised_search;
  else if (prov.evidence_snippet && prov.evidence_snippet.matched_str) {
    searchPhrase = prov.evidence_snippet.matched_str;
  }
  // Pick a same origin PDF URL when possible. Pages serves
  // data/raw/** with application/pdf so PDF.js can fetch it via
  // XHR without a CORS preflight.
  let fileUrl = null;
  if (prov.local_path) fileUrl = "../" + prov.local_path;
  else if (prov.github_raw_url) fileUrl = prov.github_raw_url;
  else if (prov.issuer_url) fileUrl = prov.issuer_url;

  if (!fileUrl) {
    host.innerHTML = `<p class="drill-empty">No source PDF URL recorded for this finding.</p>`;
    return;
  }

  // Build the viewer URL. The viewer (dashboard/pdf-viewer.html) reads
  // these three URL params and applies highlighting on load.
  const qs = new URLSearchParams();
  qs.set("file", fileUrl);
  if (page) qs.set("page", String(page));
  if (searchPhrase) qs.set("search", searchPhrase);
  const viewerUrl = "pdf-viewer.html?" + qs.toString();

  // External jump links (separate tabs) for users who want the
  // publisher's hosted PDF or the immutable GitHub permalink.
  const externals = [];
  if (prov.local_path) externals.push({ label: "Local", url: "../" + prov.local_path + (page ? `#page=${page}` : "") });
  if (prov.issuer_url_at_phrase || prov.issuer_url) externals.push({ label: "Issuer", url: prov.issuer_url_at_phrase || prov.issuer_url_at_page || prov.issuer_url });
  if (prov.github_blob_url_at_phrase || prov.github_blob_url) externals.push({ label: "GitHub", url: prov.github_blob_url_at_phrase || prov.github_blob_url_at_page || prov.github_blob_url });
  const linksHtml = externals
    .map(
      (c) =>
        `<a href="${escapeHtml(c.url)}" target="_blank" rel="noopener">${escapeHtml(c.label)} \u2197</a>`
    )
    .join(" &middot; ");

  host.innerHTML = `
    <div class="pdf-host-head">
      <div>
        <strong>${escapeHtml(f.company_name || f.company || "")}</strong>
        &middot; ${escapeHtml(prov.label || prov.period || "")}
        ${page ? ` &middot; page ${escapeHtml(String(page))}` : ""}
        ${searchPhrase ? ` &middot; <span class="pdf-host-search">highlight: <code>${escapeHtml(searchPhrase.length > 60 ? searchPhrase.slice(0, 60) + "\u2026" : searchPhrase)}</code></span>` : ""}
      </div>
      <div class="pdf-host-links">Open externally: ${linksHtml || "<span style='opacity:0.5'>none</span>"}</div>
    </div>
    <div class="pdf-host-frame">
      <iframe src="${escapeHtml(viewerUrl)}"
              class="pdf-host-object"
              title="Source PDF viewer"
              allow="fullscreen"
              loading="lazy"></iframe>
    </div>
  `;
}

/* ------------------------- ToC scroll-spy ------------------------------- */
/* Highlights the active sidebar link as the analyst scrolls through the
 * page. Uses IntersectionObserver so it stays cheap. */
function bindTocScrollSpy() {
  const links = Array.from(document.querySelectorAll(".toc-link"));
  if (!links.length || !("IntersectionObserver" in window)) return;
  const linkByTarget = new Map();
  links.forEach((a) => linkByTarget.set(a.dataset.target, a));
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((e) => {
        if (!e.isIntersecting) return;
        const link = linkByTarget.get(e.target.id);
        if (!link) return;
        links.forEach((l) => l.classList.remove("active"));
        link.classList.add("active");
      });
    },
    { rootMargin: "-30% 0px -55% 0px", threshold: 0 }
  );
  linkByTarget.forEach((_link, id) => {
    const el = document.getElementById(id);
    if (el) observer.observe(el);
  });
}

/* ======================================================================
 * Report library (sidebar tree)
 * ----------------------------------------------------------------------
 * Renders companies -> reports tree in the left sidebar. Clicking a
 * report node filters the leaderboard + drill + PDF sections to just
 * that company + period pair. Clicking the company name (or "All
 * reports") clears the per report filter but keeps the company filter.
 * ====================================================================== */

/* Sidebar fallback when outputs/report_library.json is missing (older
 * runs). Derives a minimal company -> reports map directly from the
 * leaderboard payload. The derived periods come from prov.period so
 * the JS filter will hit them the same way the real library does. */
function deriveLibraryFromLeaderboard(data) {
  const findings = (data && data.top_findings) || [];
  const map = new Map();
  for (const f of findings) {
    const prov = f.provenance || {};
    const company = f.company_name || f.company || "?";
    const periodKey = prov.period || "(unknown period)";
    const periodLabel = prov.label || periodKey;
    if (!map.has(company)) map.set(company, { name: company, reports: new Map() });
    const c = map.get(company);
    if (!c.reports.has(periodKey)) {
      c.reports.set(periodKey, {
        period: periodLabel,
        period_id: periodKey,
        matched_periods: [periodKey],
        finding_count: 0,
        critical: 0,
        warning: 0,
        info: 0,
        has_findings: true,
      });
    }
    const r = c.reports.get(periodKey);
    r.finding_count += 1;
    if (f.severity && r[f.severity] !== undefined) r[f.severity] += 1;
  }
  const companies = Array.from(map.values()).map((c) => ({
    name: c.name,
    reports: Array.from(c.reports.values()),
  }));
  companies.sort((a, b) => a.name.localeCompare(b.name));
  return { schema_version: 0, source: "derived_from_leaderboard", companies };
}

function _periodsEqual(a, b) {
  if (!a || !b) return false;
  if (a.length !== b.length) return false;
  const sa = [...a].sort();
  const sb = [...b].sort();
  return sa.every((v, i) => v === sb[i]);
}

function renderReportLibrary() {
  const host = document.getElementById("toc-library");
  if (!host) return;
  const lib = reportLibrary || {};
  const companies = lib.companies || [];
  if (!companies.length) {
    host.innerHTML = `<p class="toc-library-empty">No reports indexed yet.</p>`;
    return;
  }
  const parts = companies.map((c) => {
    const reports = c.reports || [];
    const reportsHtml = reports.map((r) => {
      // Filter key is the matched_periods array generated by
      // build_report_library() server-side; falls back to the human
      // period label so the sidebar still works for older payloads
      // that pre-date the matched_periods field.
      const periodsKey = (r.matched_periods && r.matched_periods.length)
        ? r.matched_periods
        : (r.period_id ? [r.period_id] : [r.period]);
      const sevDot = r.critical > 0
        ? `<span class="lib-sev lib-sev-critical" title="this report has critical flags">\u25CF</span>`
        : r.warning > 0
        ? `<span class="lib-sev lib-sev-warning" title="this report has warnings">\u25CF</span>`
        : r.info > 0
        ? `<span class="lib-sev lib-sev-info" title="this report has info flags">\u25CF</span>`
        : `<span class="lib-sev lib-sev-none" title="no flags on this report">\u25CB</span>`;
      const peerTag = r.role === "peer_control"
        ? `<span class="lib-tag-peer" title="Used as a year-over-year baseline">prior</span>`
        : "";
      const isActive =
        state.libraryFilterCompany === c.name &&
        _periodsEqual(state.libraryFilterPeriods, periodsKey)
          ? " is-active"
          : "";
      return `
        <a href="#"
           class="lib-report${isActive}"
           data-company="${escapeHtml(c.name)}"
           data-periods="${escapeHtml(JSON.stringify(periodsKey))}"
           data-label="${escapeHtml(r.period)}">
          ${sevDot}
          <span class="lib-report-period">${escapeHtml(r.period)}</span>
          ${peerTag}
        </a>`;
    }).join("");
    const isCompanyActive =
      state.libraryFilterCompany === c.name &&
      (!state.libraryFilterPeriods || !state.libraryFilterPeriods.length)
        ? " is-active"
        : "";
    return `
      <details class="lib-company" ${isCompanyActive ? "open" : ""}>
        <summary class="lib-company-summary${isCompanyActive ? " is-active" : ""}">
          <span class="lib-company-name">${escapeHtml(c.name)}</span>
        </summary>
        ${reportsHtml}
      </details>`;
  });
  const hasFilter = state.libraryFilterCompany ||
    (state.libraryFilterPeriods && state.libraryFilterPeriods.length);
  const clearHtml = hasFilter
    ? `<a href="#" id="lib-clear" class="lib-clear">Clear filter \u00D7</a>`
    : "";
  host.innerHTML = `${clearHtml}<div class="lib-tree">${parts.join("")}</div>`;

  host.querySelectorAll(".lib-report").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      let periods = [];
      try { periods = JSON.parse(a.dataset.periods || "[]"); } catch (_) { periods = []; }
      state.libraryFilterCompany = a.dataset.company || "";
      state.libraryFilterPeriods = periods;
      state.libraryFilterLabel = a.dataset.label || "";
      // Pick the first matching row as the new drill target so the
      // "Why we flagged it" panel below also updates in place. If no
      // row matches we leave the previous selection alone.
      const data = SCOPES[state.scope].data || { top_findings: [] };
      const matching = data.top_findings.find(matchesFilters);
      if (matching) state.selectedKey = compositeKeyOf(matching);
      renderReportLibrary();
      render();
      updateLibraryBanner();
      // No scrollIntoView: the user wants the leaderboard to update
      // silently without the page jumping anywhere.
    });
  });
  host.querySelectorAll(".lib-company-summary").forEach((s) => {
    s.addEventListener("click", (e) => {
      // Double-click on a company name = filter to that company only,
      // clear the per-report filter. Single click keeps the native
      // <details> toggle behaviour.
      if (e.detail !== 2) return;
      const company = s.parentElement.querySelector(".lib-company-name")?.textContent;
      if (!company) return;
      e.preventDefault();
      state.libraryFilterCompany = company;
      state.libraryFilterPeriods = null;
      state.libraryFilterLabel = "";
      const data = SCOPES[state.scope].data || { top_findings: [] };
      const matching = data.top_findings.find(matchesFilters);
      if (matching) state.selectedKey = compositeKeyOf(matching);
      renderReportLibrary();
      render();
      updateLibraryBanner();
    });
  });
  const clear = document.getElementById("lib-clear");
  if (clear) {
    clear.addEventListener("click", (e) => {
      e.preventDefault();
      state.libraryFilterCompany = "";
      state.libraryFilterPeriods = null;
      state.libraryFilterLabel = "";
      renderReportLibrary();
      render();
      updateLibraryBanner();
    });
  }
}

function updateLibraryBanner() {
  const banner = document.getElementById("library-active");
  if (!banner) return;
  const hasFilter = state.libraryFilterCompany ||
    (state.libraryFilterPeriods && state.libraryFilterPeriods.length);
  if (!hasFilter) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }
  const label = state.libraryFilterLabel
    ? `${state.libraryFilterCompany} &middot; ${state.libraryFilterLabel}`
    : state.libraryFilterCompany;
  banner.hidden = false;
  banner.innerHTML = `Showing flags for: <strong>${escapeHtml(label)}</strong> <a href="#" id="banner-clear">Clear \u00D7</a>`;
  const c = document.getElementById("banner-clear");
  if (c) c.addEventListener("click", (e) => {
    e.preventDefault();
    state.libraryFilterCompany = "";
    state.libraryFilterPeriods = null;
    state.libraryFilterLabel = "";
    renderReportLibrary();
    render();
    updateLibraryBanner();
  });
}
