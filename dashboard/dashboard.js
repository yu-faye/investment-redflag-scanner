/* Investment Red Flag Scanner dashboard
 *
 * Loads two leaderboard JSONs (middelborg + all), wires filters, renders
 * the table, and exposes click through destinations per finding plus an
 * embedded PDF viewer with the matched evidence highlighted.
 */

const SCOPES = {
  middelborg: { file: "../outputs/middelborg_leaderboard.json", data: null },
  all: { file: "../outputs/leaderboard.json", data: null },
};

// v9: optional artefacts. If they 404 (older runs) the panels just stay
// hidden -- we don't want a missing v9 file to break the v7/v8 dashboard.
const V9_FILES = {
  matrix: "../outputs/triangulation_matrix.json",
  roadmap: "../outputs/audit_roadmap.json",
};
let v9Matrix = null;
let v9Roadmap = null;

// v10: argument trees, narrative paragraphs, top-level Sankey of the
// reasoning landscape. All optional; load failures hide the section.
const V10_FILES = {
  sankey: "../outputs/sankey_data.json",
  trees: "../outputs/argument_trees.json",
  paragraphs: "../outputs/narrative_paragraphs.json",
};
let v10Sankey = null;
let v10Trees = null;
let v10Paragraphs = null;

// Report library: company -> reports tree shown in the sidebar so the
// analyst can see every report that has been ingested (not just the
// findings). Optional; load failure just hides the section.
const REPORT_LIBRARY_FILE = "../outputs/report_library.json";
let reportLibrary = null;

const state = {
  scope: "middelborg",
  severity: "",
  rule: "",
  company: "",
  // When a Sankey node is clicked, leaderboard rows are filtered to
  // only those whose composite_key is in `sankeyFilterKeys`.
  sankeyFilterKeys: null,
  sankeyFilterLabel: "",
  // When a report library node is clicked, leaderboard rows are
  // filtered to that company + period pair.
  libraryFilterCompany: "",
  libraryFilterPeriod: "",
  // The currently drilled finding (composite_key). Drives the Drill
  // into a finding + Embedded source PDF sections at the bottom of
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

  // v9 panels: tolerant load. Missing files just hide the section.
  fetchJson(V9_FILES.matrix)
    .then((d) => {
      v9Matrix = d;
      return fetchJson(V9_FILES.roadmap);
    })
    .then((d) => {
      v9Roadmap = d;
      renderTriangulationMatrix();
      renderAuditRoadmap();
    })
    .catch(() => {
      const sec = document.getElementById("matrix-section");
      if (sec) sec.hidden = true;
    });

  // v10 panels: same tolerant pattern.
  Promise.all([
    fetchJson(V10_FILES.sankey).catch(() => null),
    fetchJson(V10_FILES.trees).catch(() => null),
    fetchJson(V10_FILES.paragraphs).catch(() => null),
  ]).then(([s, t, p]) => {
    v10Sankey = s;
    v10Trees = t;
    v10Paragraphs = p;
    if (v10Sankey) {
      renderSankey();
    } else {
      const sec = document.getElementById("landscape-section");
      if (sec) sec.hidden = true;
    }
    // Re-render rows so narrative paragraphs / argument trees appear once
    // the v10 payloads have arrived.
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
  if (state.sankeyFilterKeys) {
    if (!state.sankeyFilterKeys.has(compositeKeyOf(f))) return false;
  }
  if (state.libraryFilterCompany) {
    const label = f.company_name || f.company || "";
    if (label !== state.libraryFilterCompany) return false;
  }
  if (state.libraryFilterPeriod) {
    const prov = f.provenance || {};
    const period = prov.label || prov.period || "";
    if (period !== state.libraryFilterPeriod) return false;
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

/* ------------------------- v9 panels ------------------------------------ */

const VERDICT_GLYPH = {
  confirms: { glyph: "\u2713", label: "confirms", cls: "vd-confirms" },
  partial:  { glyph: "\u25D0", label: "partial",  cls: "vd-partial"  },
  refutes:  { glyph: "\u2717", label: "refutes",  cls: "vd-refutes"  },
  not_found:{ glyph: "\u2298", label: "not_found",cls: "vd-not-found"},
  neutral:  { glyph: "\u25E6", label: "neutral",  cls: "vd-neutral"  },
  error:    { glyph: "!",      label: "error",    cls: "vd-error"    },
};

function renderTriangulationMatrix() {
  if (!v9Matrix || !v9Matrix.rows || !v9Matrix.rows.length) return;
  const el = document.getElementById("triangulation-matrix");
  if (!el) return;

  // Only include tap_kinds that actually have at least one non-null cell.
  const activeTapKinds = v9Matrix.tap_kinds.filter((kind) =>
    v9Matrix.rows.some((r) => r.cells_by_kind && r.cells_by_kind[kind])
  );
  // Always include the pending tap_kinds appearing in any next_recommended_taps.
  v9Matrix.rows.forEach((r) => {
    (r.next_recommended_taps || []).forEach((rec) => {
      if (!activeTapKinds.includes(rec.tap_kind)) activeTapKinds.push(rec.tap_kind);
    });
  });

  const head = `
    <thead>
      <tr>
        <th class="m-col-entity">Hypothesis (entity)</th>
        <th class="m-col-severity">Derived</th>
        ${activeTapKinds.map((k) => `<th class="m-col-tap" title="${escapeHtml(k)}">${escapeHtml(formatTapKind(k))}</th>`).join("")}
        <th class="m-col-next">Next recommended tap</th>
      </tr>
    </thead>
  `;

  const body = v9Matrix.rows.map((r) => {
    const severity = r.derived_severity || "info";
    const sevClass = `sev-cell sev-${severity}`;
    const cells = activeTapKinds.map((kind) => {
      const cell = (r.cells_by_kind || {})[kind];
      if (!cell) {
        return `<td class="m-cell m-cell-empty" title="not yet queried">\u2014</td>`;
      }
      const v = VERDICT_GLYPH[cell.verdict] || { glyph: "?", label: cell.verdict, cls: "vd-other" };
      const tip = `${cell.tap_id} verdict=${cell.verdict} conf=${cell.confidence}\n${(cell.narrative || "").slice(0, 240)}`;
      return `<td class="m-cell ${v.cls}" title="${escapeHtml(tip)}">${v.glyph}<span class="m-cell-confidence">${escapeHtml(cell.confidence ?? "")}</span></td>`;
    }).join("");
    const next = (r.next_recommended_taps && r.next_recommended_taps[0])
      ? `<span class="m-next-pill" title="${escapeHtml(JSON.stringify(r.next_recommended_taps[0]))}">${escapeHtml(formatTapKind(r.next_recommended_taps[0].tap_kind))}${r.next_recommended_taps[0].blocking_for_critical ? " \u2605" : ""}</span>`
      : `<span class="m-next-empty">\u2014</span>`;
    const blockers = (r.blockers_for_critical && r.blockers_for_critical.length)
      ? `<details class="m-blockers"><summary>${r.blockers_for_critical.length} blocker(s)</summary><ul>${r.blockers_for_critical.map((b) => `<li>${escapeHtml(b)}</li>`).join("")}</ul></details>`
      : "";
    return `
      <tr>
        <td class="m-col-entity">
          <div class="m-entity">${escapeHtml(r.entity || r.hypothesis_id)}</div>
          <div class="m-claim">${escapeHtml(r.claim || "")}</div>
          ${blockers}
        </td>
        <td class="${sevClass}">${escapeHtml(severity)}</td>
        ${cells}
        <td class="m-col-next">${next}</td>
      </tr>
    `;
  }).join("");

  el.innerHTML = head + "<tbody>" + body + "</tbody>";
}

function renderAuditRoadmap() {
  if (!v9Roadmap || !v9Roadmap.recommended_taps) return;
  const el = document.getElementById("audit-roadmap");
  if (!el) return;
  if (!v9Roadmap.recommended_taps.length) {
    el.innerHTML = `<li class="roadmap-empty">All applicable tap_kinds for every hypothesis have already been queried. No new external data source would raise coverage right now.</li>`;
    return;
  }
  el.innerHTML = v9Roadmap.recommended_taps.map((r, idx) => {
    const unblock = r.would_unblock_critical_for || [];
    const covers = (r.covers_hypotheses || []).map((h) =>
      `<li><code>${escapeHtml(h.entity || h.hypothesis_id)}</code> &mdash; addresses <em>${escapeHtml(h.question_id)}</em>${h.blocking_for_critical ? ' <span class="roadmap-star">\u2605 blocking</span>' : ''}</li>`
    ).join("");
    return `
      <li class="roadmap-item">
        <div class="roadmap-head">
          <span class="roadmap-rank">#${idx + 1}</span>
          <span class="roadmap-kind">${escapeHtml(formatTapKind(r.tap_kind))}</span>
          <span class="roadmap-gain" title="Sum of expected_information_gain across hypotheses">+${r.total_information_gain.toFixed(2)} info gain</span>
          ${unblock.length ? `<span class="roadmap-unblock">would unblock critical for ${unblock.length} hypothesis(es)</span>` : ""}
        </div>
        <ul class="roadmap-covers">${covers}</ul>
      </li>
    `;
  }).join("");
}

function formatTapKind(k) {
  return String(k || "").replace(/_/g, " ");
}

/* ------------------------- v10 panels ----------------------------------- */

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
  return `
    <details class="v10-tree-root" open>
      <summary><span class="v10-tree-tag">Argument tree</span> click to collapse</summary>
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

function renderSankey() {
  if (!v10Sankey || !window.d3 || !window.d3.sankey) {
    const sec = document.getElementById("landscape-section");
    if (sec) sec.hidden = true;
    return;
  }
  const svg = d3.select("#sankey-svg");
  const host = document.getElementById("sankey-host");
  const width = Math.max(640, host ? host.clientWidth : 900);
  const nodeCount = v10Sankey.nodes.length;
  const height = Math.max(360, Math.min(900, 22 * nodeCount + 80));
  svg.attr("viewBox", `0 0 ${width} ${height}`).attr("width", "100%").attr("height", height);
  svg.selectAll("*").remove();

  const idx = new Map();
  v10Sankey.nodes.forEach((n, i) => idx.set(n.id, i));
  const sankeyData = {
    nodes: v10Sankey.nodes.map((n) => ({ ...n })),
    links: v10Sankey.links.map((l) => ({
      source: idx.get(l.source),
      target: idx.get(l.target),
      value: l.value,
      color: l.color,
      verdict: l.verdict,
      finding_keys: l.finding_keys || [],
    })),
  };

  const layout = d3.sankey()
    .nodeWidth(14)
    .nodePadding(10)
    .extent([[10, 10], [width - 220, height - 10]]);

  const { nodes, links } = layout(sankeyData);

  // Layer captions.
  const layerLabels = v10Sankey.layer_labels || [];
  svg.append("g").selectAll("text.v10-sk-layer")
    .data(layerLabels)
    .enter()
    .append("text")
    .attr("class", "v10-sk-layer")
    .attr("x", (_, i) => 10 + ((width - 230) / Math.max(layerLabels.length - 1, 1)) * i)
    .attr("y", 8)
    .text((d) => d);

  svg.append("g")
    .attr("fill", "none")
    .attr("stroke-opacity", 0.45)
    .selectAll("path")
    .data(links)
    .enter()
    .append("path")
    .attr("d", d3.sankeyLinkHorizontal())
    .attr("stroke", (d) => d.color || "#bdc3c7")
    .attr("stroke-width", (d) => Math.max(1, d.width))
    .append("title")
    .text((d) => `${d.source.name} -> ${d.target.name}\nvalue=${d.value.toFixed(2)}\nfindings=${d.finding_keys.length}\nverdict=${d.verdict || "-"}`);

  const nodeG = svg.append("g")
    .selectAll("g")
    .data(nodes)
    .enter()
    .append("g")
    .attr("class", "v10-sk-node")
    .style("cursor", "pointer")
    .on("click", function (event, d) {
      // Aggregate finding_keys from every link touching this node.
      const keys = new Set();
      links.forEach((l) => {
        if (l.source.id === d.id || l.target.id === d.id) {
          (l.finding_keys || []).forEach((k) => keys.add(k));
        }
      });
      if (keys.size === 0) return;
      state.sankeyFilterKeys = keys;
      state.sankeyFilterLabel = d.name;
      const label = document.getElementById("sankey-active");
      if (label) {
        label.hidden = false;
        label.innerHTML = `Leaderboard filter active: <strong>${escapeHtml(d.name)}</strong> (${keys.size} finding(s)). <a href="#" id="sankey-clear">Clear</a>`;
        const clear = document.getElementById("sankey-clear");
        if (clear) clear.addEventListener("click", (e) => {
          e.preventDefault();
          state.sankeyFilterKeys = null;
          state.sankeyFilterLabel = "";
          label.hidden = true;
          render();
        });
      }
      render();
    });
  nodeG.append("rect")
    .attr("x", (d) => d.x0)
    .attr("y", (d) => d.y0)
    .attr("height", (d) => Math.max(2, d.y1 - d.y0))
    .attr("width", (d) => d.x1 - d.x0)
    .attr("fill", (d) => d.color || "#888")
    .attr("stroke", "#444")
    .append("title")
    .text((d) => `${d.name}\nlayer=${d.layer} category=${d.category}\nvalue=${d.value ? d.value.toFixed(2) : "?"}`);
  nodeG.append("text")
    .attr("x", (d) => d.x1 + 6)
    .attr("y", (d) => (d.y0 + d.y1) / 2)
    .attr("dy", "0.32em")
    .attr("text-anchor", "start")
    .attr("class", "v10-sk-nlabel")
    .text((d) => d.name.length > 38 ? d.name.slice(0, 36) + "\u2026" : d.name);
}

/* ------------------------- Drill section -------------------------------- */
/* Renders the auditor narrative paragraph + argument tree for the
 * currently selected finding into the Drill into a finding section.
 * Also surfaces a Jump to PDF button that scrolls the page down to the
 * embedded PDF viewer with the matched evidence already highlighted.
 */
function renderDrillSection(allFindings) {
  const host = document.getElementById("drill-host");
  if (!host) return;
  const f = findFindingByKey(allFindings, state.selectedKey);
  if (!f) {
    host.innerHTML = `<p class="drill-empty">Click the \u2193 arrow in front of any company name in the Findings leaderboard to drill in here.</p>`;
    return;
  }
  const v10Html = renderV10Block(f) ||
    `<p class="drill-empty">No narrative or argument tree was generated for this finding.</p>`;
  const headline = typeof f.headline === "string"
    ? f.headline
    : (f.headline && f.headline.en) || "";
  const sevBadge = `<span class="sev-badge sev-${escapeHtml(f.severity || "")}">${escapeHtml(f.severity || "")}</span>`;
  const prov = f.provenance || {};
  const page = prov.pdf_page_hit;
  const hasPdf = !!prov.local_path || !!prov.github_raw_url || !!prov.issuer_url;
  const jumpBtn = hasPdf
    ? `<button type="button" id="drill-jump-pdf" class="drill-jump-pdf" title="Scroll to the embedded PDF below, jumped to ${page ? "page " + page : "the start"} with the evidence highlighted">
         Open in PDF below${page ? " &middot; page " + escapeHtml(String(page)) : ""} &darr;
       </button>`
    : "";
  const headerHtml = `
    <div class="drill-head">
      <h3>${escapeHtml(f.company_name || f.company || "")} &middot; ${escapeHtml(f.rule_id || "")} ${sevBadge}</h3>
      <p class="drill-blockquote">${escapeHtml(headline)}</p>
      ${jumpBtn}
    </div>
  `;
  host.innerHTML = headerHtml + v10Html;
  const btn = document.getElementById("drill-jump-pdf");
  if (btn) {
    btn.addEventListener("click", () => {
      const pdfSec = document.getElementById("pdf-section");
      if (pdfSec) pdfSec.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
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

function deriveLibraryFromLeaderboard(data) {
  const findings = (data && data.top_findings) || [];
  const map = new Map();
  for (const f of findings) {
    const prov = f.provenance || {};
    const company = f.company_name || f.company || "?";
    const period = prov.label || prov.period || "(unknown period)";
    if (!map.has(company)) map.set(company, { name: company, reports: new Map() });
    const c = map.get(company);
    if (!c.reports.has(period)) {
      c.reports.set(period, { period, finding_count: 0, critical: 0, warning: 0, info: 0, has_findings: true });
    }
    const r = c.reports.get(period);
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
    const totalFindings = reports.reduce((s, r) => s + (r.finding_count || 0), 0);
    const totalCritical = reports.reduce((s, r) => s + (r.critical || 0), 0);
    const reportsHtml = reports.map((r) => {
      const sevDot = r.critical > 0
        ? `<span class="lib-sev lib-sev-critical" title="${r.critical} critical">\u25CF</span>`
        : r.warning > 0
        ? `<span class="lib-sev lib-sev-warning" title="${r.warning} warning">\u25CF</span>`
        : r.info > 0
        ? `<span class="lib-sev lib-sev-info" title="${r.info} info">\u25CF</span>`
        : `<span class="lib-sev lib-sev-none" title="no findings">\u25CB</span>`;
      const peerTag = r.role === "peer_control"
        ? `<span class="lib-tag-peer" title="Used as peer control / YoY baseline">peer</span>`
        : "";
      const isActive =
        state.libraryFilterCompany === c.name &&
        state.libraryFilterPeriod === r.period
          ? " is-active"
          : "";
      return `
        <a href="#leaderboard-section"
           class="lib-report${isActive}"
           data-company="${escapeHtml(c.name)}"
           data-period="${escapeHtml(r.period)}">
          ${sevDot}
          <span class="lib-report-period">${escapeHtml(r.period)}</span>
          ${r.finding_count > 0 ? `<span class="lib-report-count">${r.finding_count}</span>` : ""}
          ${peerTag}
        </a>`;
    }).join("");
    const isCompanyActive =
      state.libraryFilterCompany === c.name && !state.libraryFilterPeriod
        ? " is-active"
        : "";
    return `
      <details class="lib-company" ${isCompanyActive ? "open" : ""}>
        <summary class="lib-company-summary${isCompanyActive}">
          <span class="lib-company-name">${escapeHtml(c.name)}</span>
          <span class="lib-company-meta">${reports.length}\u00B7${totalFindings}${totalCritical > 0 ? `\u00B7<span class='lib-sev-critical'>${totalCritical}</span>` : ""}</span>
        </summary>
        ${reportsHtml}
      </details>`;
  });
  const clearHtml = (state.libraryFilterCompany || state.libraryFilterPeriod)
    ? `<a href="#" id="lib-clear" class="lib-clear">Clear report filter \u00D7</a>`
    : "";
  host.innerHTML = `${clearHtml}<div class="lib-tree">${parts.join("")}</div>`;

  host.querySelectorAll(".lib-report").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      state.libraryFilterCompany = a.dataset.company || "";
      state.libraryFilterPeriod = a.dataset.period || "";
      renderReportLibrary();
      const data = SCOPES[state.scope].data || { top_findings: [] };
      const matching = data.top_findings.find(matchesFilters);
      if (matching) state.selectedKey = compositeKeyOf(matching);
      render();
      updateLibraryBanner();
      const lbsec = document.getElementById("leaderboard-section");
      if (lbsec) lbsec.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
  host.querySelectorAll(".lib-company-summary").forEach((s) => {
    s.addEventListener("click", (e) => {
      // Only intercept when clicking the company name area, not the
      // disclosure triangle. We use the data attribute to know which.
      const company = s.parentElement.querySelector(".lib-company-name")?.textContent;
      // Optional: filter by company alone when summary is clicked.
      if (e.detail === 2 && company) {
        e.preventDefault();
        state.libraryFilterCompany = company;
        state.libraryFilterPeriod = "";
        renderReportLibrary();
        render();
        updateLibraryBanner();
      }
    });
  });
  const clear = document.getElementById("lib-clear");
  if (clear) {
    clear.addEventListener("click", (e) => {
      e.preventDefault();
      state.libraryFilterCompany = "";
      state.libraryFilterPeriod = "";
      renderReportLibrary();
      render();
      updateLibraryBanner();
    });
  }
}

function updateLibraryBanner() {
  const banner = document.getElementById("library-active");
  if (!banner) return;
  if (!state.libraryFilterCompany && !state.libraryFilterPeriod) {
    banner.hidden = true;
    banner.innerHTML = "";
    return;
  }
  const label = state.libraryFilterPeriod
    ? `${state.libraryFilterCompany} &middot; ${state.libraryFilterPeriod}`
    : state.libraryFilterCompany;
  banner.hidden = false;
  banner.innerHTML = `Report filter: <strong>${escapeHtml(label)}</strong> <a href="#" id="banner-clear">Clear \u00D7</a>`;
  const c = document.getElementById("banner-clear");
  if (c) c.addEventListener("click", (e) => {
    e.preventDefault();
    state.libraryFilterCompany = "";
    state.libraryFilterPeriod = "";
    renderReportLibrary();
    render();
    updateLibraryBanner();
  });
}
