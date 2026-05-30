"""External collision detector (v8).

Cross-checks PDF self-reported subsidiary expansion claims against the
external Doffin public-procurement registry. First (and only, in v8)
subtype: `norwegian_subsidiary_organic_vs_awarded`.

Hypothesis under test:
    "Company X claims subsidiary S is a SPECIALIST in domain D within
     Norway. If that claim is true, S's name should appear as the winner
     of multiple Doffin award notices within the relevant procurement
     class."

Severity bands (v8 first cut):
    - 0 confirmed awards AND subsidiary marketed as
      specialist / established player / key player -> critical
    - 1-2 confirmed awards AND >=50% pre-acquisition -> warning
      (acquired stale capacity rather than building)
    - 3+ confirmed awards AND post-acquisition -> info
      (claim is consistent with public record)

Design rules shared with the existing detectors:
    - Pure function over inputs: (pdf_text, external_config, doffin_client)
    - Emits standard finding dicts with rule_id="external_collision",
      severity, claim_excerpt, claim_excerpt_matched_term,
      external_evidence[].
    - No network in unit tests: callers can inject a fake fetcher.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from src.ingest.external import doffin_client


def _find_anchor_sentence(text: str, anchor: str) -> Optional[dict]:
    """Locate the first sentence that contains `anchor` (case-insensitive).
    Returns {sentence, char_span, matched_term} so the orchestrator's
    excerpt_locator + evidence_snippet pipeline can attach a PDF bbox.

    Sentence boundary heuristic: split on '. ' / '! ' / '? ' / newlines.
    This matches the existing narrative_dissonance excerpt extractor."""
    if not anchor or not text:
        return None
    # Split into rough sentences while preserving offsets.
    # We use a simple regex; collapsing newlines first improves recall on
    # PDF text where line breaks land mid-sentence.
    flat = re.sub(r"\s+", " ", text)
    needle = re.escape(anchor)
    m = re.search(needle, flat, flags=re.IGNORECASE)
    if not m:
        return None
    # Walk backward/forward to sentence punctuation.
    start = m.start()
    end = m.end()
    while start > 0 and flat[start - 1] not in ".!?":
        start -= 1
    # Trim leading whitespace.
    while start < len(flat) and flat[start] in " \t":
        start += 1
    while end < len(flat) and flat[end] not in ".!?":
        end += 1
    if end < len(flat) and flat[end] in ".!?":
        end += 1
    return {
        "sentence": flat[start:end].strip(),
        "matched_term": flat[m.start() : m.end()],
        "char_span": [m.start(), m.end()],
    }


def _classify(subsidiary: dict, awards: list[dict]) -> tuple[str, str, str]:
    """Return (severity, verdict, summary_phrase) for one subsidiary."""
    role = (subsidiary.get("claimed_role") or "").lower()
    acquired_year = subsidiary.get("acquired_year")
    n = len(awards)

    # Awards split by pre/post-acquisition.
    post_acq = []
    pre_acq = []
    if acquired_year:
        for a in awards:
            pub = a.get("publication_date") or ""
            try:
                year = int(pub[:4])
            except (TypeError, ValueError):
                year = None
            if year is None:
                pre_acq.append(a)
            elif year >= acquired_year:
                post_acq.append(a)
            else:
                pre_acq.append(a)
    else:
        post_acq = list(awards)

    is_specialist = any(
        kw in role for kw in ["specialist", "established player", "key player", "leader"]
    )

    if n == 0 and is_specialist:
        return (
            "critical",
            "no_public_record",
            "claimed specialist with zero Doffin award records",
        )
    if n == 0:
        return (
            "warning",
            "no_public_record",
            "zero Doffin award records in window",
        )
    if n <= 2 and len(pre_acq) >= len(post_acq):
        return (
            "warning",
            "mostly_pre_acquisition",
            f"{n} award(s), of which {len(pre_acq)} pre-acquisition",
        )
    return (
        "info",
        "consistent_with_public_record",
        f"{n} award(s), {len(post_acq)} post-acquisition",
    )


def _make_headline(company_name: str, subsidiary: dict, severity: str, awards: list[dict], summary: str) -> str:
    sub_name = subsidiary.get("name", "?")
    role = subsidiary.get("claimed_role", "")
    n = len(awards)
    if severity == "critical":
        return (
            f"{company_name}: claims {sub_name} is a {role}, but Doffin shows "
            f"{n} confirmed award(s) -- {summary}."
        )
    if severity == "warning":
        return (
            f"{company_name}: {sub_name} ({role}) -- Doffin shows {n} award(s) "
            f"({summary})."
        )
    return (
        f"{company_name}: {sub_name} Doffin activity is consistent with its "
        f"claimed role ({summary})."
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------- Public API -------------------------------------


def detect(
    company_id: str,
    company_name: str,
    current_report: dict,
    current_text: str,
    *,
    cache_dir: Path,
    fetcher: Callable[..., dict] = None,
) -> list[dict]:
    """Run the external_collision detector for one company report.

    `current_report` is the same dict shape as in validation/companies.json.
    `current_text` is the raw extracted PDF text used for anchor matching.
    `cache_dir` receives the per-supplier raw Doffin JSON files.
    `fetcher` is `doffin_client.collect_awards_for_supplier` by default;
    tests inject a fake.
    """
    config = (current_report.get("external_sources") or {}).get("doffin")
    if not config or not config.get("supplier_subsidiaries"):
        return []
    if fetcher is None:
        fetcher = doffin_client.collect_awards_for_supplier

    findings: list[dict] = []
    for sub in config["supplier_subsidiaries"]:
        # 1) Fetch + cache external evidence.
        try:
            result = fetcher(
                sub["name"],
                cache_dir=cache_dir,
                expected_winner_aliases=sub.get("aliases", []),
            )
        except Exception as e:
            # Network outage / API change shouldn't kill the whole pipeline.
            findings.append(
                {
                    "rule_id": "external_collision",
                    "subtype": "norwegian_subsidiary_organic_vs_awarded",
                    "severity": "info",
                    "verdict": "fetch_failed",
                    "company": company_id,
                    "company_name": company_name,
                    "subsidiary": sub.get("name"),
                    "claim_excerpt": None,
                    "headline": (
                        f"{company_name}: Doffin fetch failed for {sub.get('name')} -- {e!s}"
                    ),
                    "external_evidence": [],
                    "external_fetch_error": str(e),
                }
            )
            continue

        confirmed = result.get("confirmed_awards") or []
        unconfirmed = result.get("unconfirmed_awards") or []
        severity, verdict, summary = _classify(sub, confirmed)

        # 2) Anchor the finding to a PDF sentence (so v5/v6 evidence
        # snippet + jump-to-PDF still work).
        anchor_hit = _find_anchor_sentence(current_text, sub.get("narrative_anchor") or sub["name"])

        finding = {
            "rule_id": "external_collision",
            "subtype": "norwegian_subsidiary_organic_vs_awarded",
            "severity": severity,
            "verdict": verdict,
            "company": company_id,
            "company_name": company_name,
            "subsidiary": sub.get("name"),
            "subsidiary_claimed_role": sub.get("claimed_role"),
            "subsidiary_acquired_year": sub.get("acquired_year"),
            "claim_excerpt": (anchor_hit or {}).get("sentence"),
            "claim_excerpt_matched_term": (anchor_hit or {}).get("matched_term"),
            "headline": _make_headline(company_name, sub, severity, confirmed, summary),
            "external_evidence": [
                {
                    "source": "doffin",
                    "source_label": "Doffin (Norwegian public procurement)",
                    "supplier_name": result["supplier_name"],
                    "aliases_used": result["aliases_used"],
                    "query_url": result["query_url"],
                    "cache_path": result["cache_path"],
                    "cache_sha256": result["sha256"],
                    "fetched_at_utc": result["fetched_at_utc"],
                    "search_hits_total": result["search_hits_total"],
                    "confirmed_award_count": len(confirmed),
                    "unconfirmed_hit_count": len(unconfirmed),
                    "confirmed_awards": [
                        {
                            "notice_id": a["notice_id"],
                            "public_url": a["public_url"],
                            "ted_id": a.get("ted_id"),
                            "publication_date": a.get("publication_date"),
                            "buyer_names": a.get("buyer_names"),
                            "awarded_names": a.get("awarded_names"),
                            "matched_alias": a.get("matched_alias"),
                            "heading": a.get("heading"),
                            "estimated_value": a.get("estimated_value"),
                            "currency": a.get("currency"),
                        }
                        for a in confirmed
                    ],
                }
            ],
        }
        findings.append(finding)
    return findings


if __name__ == "__main__":
    # Quick CLI smoke against the actual qben_infra_2024 entry.
    import json
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    companies = json.loads(Path("validation/companies.json").read_text())
    target = next(c for c in companies["companies"] if c["id"] == "qben_infra_2024")
    pdf_text_path = Path(target["current_report"]["primary_file"])
    text = pdf_text_path.read_text(encoding="utf-8", errors="ignore")

    out = detect(
        company_id=target["id"],
        company_name=target["name"],
        current_report=target["current_report"],
        current_text=text,
        cache_dir=Path("data/external/doffin/qben_infra_2024"),
    )
    for f in out:
        print(f"[{f['severity']:<8}] {f['subsidiary']:<28} {f['headline']}")
        ee = f["external_evidence"][0] if f.get("external_evidence") else {}
        for a in (ee.get("confirmed_awards") or [])[:3]:
            print(f"      * {a['notice_id']} {a['publication_date']} {a['buyer_names']}")
        print()
