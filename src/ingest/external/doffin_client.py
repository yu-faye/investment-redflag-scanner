"""Doffin (Norwegian public procurement) client.

Doffin = "Database for offentlige innkjop". The www.doffin.no SPA hits two
public endpoints (verified during v8 Day-1 spike):

  POST https://api.doffin.no/webclient/api/v2/search-api/search
       body = {searchString, facets, hitsPerPage, page, sortBy}
       -> { numHitsTotal, numHitsAccessible, hits[], facets, activeFacets }

  GET  https://api.doffin.no/webclient/api/v2/notices-api/notices/<id>
       -> full notice detail incl. awardedNames[], buyer[], publicationDate,
          core.estimatedValue (often null), eform[], tedId

The webclient/* paths require no subscription key (they're what the public
SPA itself uses). The other path family api.doffin.no/public/v2/* does
require an Azure APIM key obtained from developer.doffin.no -- avoid it.

This module returns thin Python dicts; the external_collision detector does
the matching and scoring.
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SEARCH_URL = "https://api.doffin.no/webclient/api/v2/search-api/search"
NOTICE_URL_TMPL = "https://api.doffin.no/webclient/api/v2/notices-api/notices/{notice_id}"
PUBLIC_NOTICE_URL_TMPL = "https://www.doffin.no/notices/{notice_id}"

# Pretend to be the official SPA. The webclient/* endpoints are CORS-pinned
# to www.doffin.no, but server-side reject is based on UA/Origin headers --
# so we set both.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://www.doffin.no",
    "Referer": "https://www.doffin.no/",
}

# Notice types that represent an awarded contract (vs an open tender).
AWARD_TYPES = {
    "ANNOUNCEMENT_OF_CONCLUSION_OF_CONTRACT",
    "ANNOUNCEMENT_OF_AWARD",
    "RESULT",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def _http_json(url: str, *, body: Optional[dict] = None, timeout: int = 25) -> tuple[dict, str]:
    """Issue an HTTP request and return (parsed_json, raw_body_text). Raises
    a RuntimeError with a copy-pasteable curl recipe on non-2xx so the
    failure mode is debuggable without a debugger."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_HEADERS, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw), raw
    except urllib.error.HTTPError as e:
        body_preview = e.read()[:300].decode("utf-8", "replace")
        raise RuntimeError(
            f"Doffin HTTP {e.code} for {url}\n"
            f"  body sent: {json.dumps(body)[:200] if body else 'n/a'}\n"
            f"  response: {body_preview}\n"
            f"  reproduce: curl -X {'POST' if data else 'GET'} '{url}' "
            f"-H 'Content-Type: application/json' "
            f"--data-raw '{json.dumps(body) if body else ''}'"
        ) from e


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ------------------------- Public API -------------------------------------


def search_notices(
    search_string: str,
    *,
    award_only: bool = True,
    hits_per_page: int = 50,
    max_pages: int = 3,
) -> list[dict]:
    """Free-text search against the Doffin search-api.

    Returns the raw hit summaries. Each hit has: id, buyer[], heading,
    description, locationId[], estimatedValue, type, allTypes, status,
    issueDate, deadline, sentToTed, publicationDate, placeOfPerformance.

    `award_only=True` post-filters to award notices (ANNOUNCEMENT_OF_*_OF_
    CONTRACT). The Doffin facets API technically supports server-side
    filtering on `type`, but the facet vocabulary is unstable across
    versions so post-filtering is more robust.
    """
    all_hits: list[dict] = []
    for page in range(1, max_pages + 1):
        body = {
            "searchString": search_string,
            "facets": {},
            "hitsPerPage": hits_per_page,
            "page": page,
            # Valid sortBy values (from spike): "RELEVANCE",
            # "PUBLICATION_DATE_DESC". Plain "PUBLICATION_DATE" is rejected
            # with HTTP 400.
            "sortBy": "PUBLICATION_DATE_DESC",
        }
        j, _raw = _http_json(SEARCH_URL, body=body)
        hits = j.get("hits") or []
        all_hits.extend(hits)
        # Stop early if we've collected everything.
        total = j.get("numHitsTotal") or 0
        if len(all_hits) >= total:
            break
        # Avoid hammering -- Doffin is a small public service.
        time.sleep(0.3)
    if award_only:
        all_hits = [
            h
            for h in all_hits
            if (h.get("type") in AWARD_TYPES)
            or any(t in AWARD_TYPES for t in (h.get("allTypes") or []))
        ]
    return all_hits


def fetch_notice_detail(notice_id: str) -> dict:
    """GET the full notice detail (incl. awardedNames + eForm structured data)."""
    j, _raw = _http_json(NOTICE_URL_TMPL.format(notice_id=notice_id))
    return j


# ------------------------- Caching wrapper --------------------------------


def collect_awards_for_supplier(
    supplier_name: str,
    *,
    cache_dir: Path,
    expected_winner_aliases: Optional[list[str]] = None,
    award_only: bool = True,
    enrich_with_details: bool = True,
    details_budget: int = 12,
) -> dict:
    """End-to-end: search Doffin for `supplier_name`, optionally fetch
    each award's detail to confirm `awardedNames` actually contains the
    supplier (filters out "Team Bygg" style 192-hit false positives), and
    persist the raw API responses under cache_dir for reproducibility.

    Returns:
      {
        supplier_name, aliases_used, query_url, fetched_at_utc,
        sha256, cache_path,
        search_hits_total, search_hits_post_filter,
        confirmed_awards: [
          {notice_id, public_url, ted_id, publication_date, buyer_names,
           awarded_names, matched_alias, heading, description,
           estimated_value, currency}
        ],
        unconfirmed_awards: [...same shape but matched_alias=null...],
      }
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    aliases = [supplier_name] + (expected_winner_aliases or [])
    aliases_lower = [a.lower() for a in aliases]

    fetched_at = _now_iso()
    raw_search_payload = {
        "supplier_name": supplier_name,
        "aliases_used": aliases,
        "query_url": SEARCH_URL,
        "query_body": {
            "searchString": supplier_name,
            "facets": {},
            "hitsPerPage": 50,
            "page": 1,
            "sortBy": "PUBLICATION_DATE_DESC",
        },
        "fetched_at_utc": fetched_at,
    }

    try:
        hits = search_notices(supplier_name, award_only=award_only)
        raw_search_payload["search_ok"] = True
        raw_search_payload["search_hits"] = hits
    except Exception as e:
        raw_search_payload["search_ok"] = False
        raw_search_payload["search_error"] = str(e)
        hits = []

    confirmed: list[dict] = []
    unconfirmed: list[dict] = []
    details_fetched = 0

    if enrich_with_details:
        for h in hits[:details_budget]:
            nid = h.get("id")
            if not nid:
                continue
            try:
                detail = fetch_notice_detail(nid)
                details_fetched += 1
            except Exception as e:
                detail = {"_fetch_error": str(e), "id": nid}

            awarded = detail.get("awardedNames") or []
            match = None
            for a in awarded:
                if any(alias in (a or "").lower() for alias in aliases_lower):
                    match = a
                    break

            buyer_names = [b.get("name") for b in (detail.get("buyer") or [])]
            record = {
                "notice_id": nid,
                "public_url": PUBLIC_NOTICE_URL_TMPL.format(notice_id=nid),
                "ted_id": detail.get("tedId"),
                "publication_date": detail.get("publicationDate") or h.get("publicationDate"),
                "issue_date": detail.get("issueDate") or h.get("issueDate"),
                "buyer_names": buyer_names,
                "awarded_names": awarded,
                "matched_alias": match,
                "heading": detail.get("heading") or h.get("heading"),
                "description": (detail.get("description") or h.get("description") or "")[:280],
                "estimated_value": (detail.get("core") or {}).get("estimatedValue"),
                "currency": (detail.get("core") or {}).get("currency"),
                "raw_detail": detail,
            }
            (confirmed if match else unconfirmed).append(record)
            time.sleep(0.25)

    raw_search_payload["confirmed_awards"] = confirmed
    raw_search_payload["unconfirmed_awards"] = unconfirmed
    raw_search_payload["details_fetched"] = details_fetched
    raw_search_payload["search_hits_total"] = len(hits)
    raw_search_payload["search_hits_post_filter"] = len(hits)

    # Persist deterministically -- one file per supplier per fetch day so
    # snapshots stay reproducible. The slug is conservative (alnum + dash).
    slug = "".join(c if c.isalnum() else "-" for c in supplier_name.lower()).strip("-")
    day = fetched_at[:10]
    out_path = cache_dir / f"{slug}_{day}.json"

    blob = json.dumps(raw_search_payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    out_path.write_bytes(blob)
    sha = _sha256_bytes(blob)

    return {
        "supplier_name": supplier_name,
        "aliases_used": aliases,
        "query_url": SEARCH_URL,
        "fetched_at_utc": fetched_at,
        "sha256": sha,
        "cache_path": str(out_path.relative_to(out_path.parents[3]))
        if len(out_path.parents) > 3
        else str(out_path),
        "search_hits_total": len(hits),
        "search_hits_post_filter": len(hits),
        "details_fetched": details_fetched,
        "confirmed_awards": [_strip_raw(r) for r in confirmed],
        "unconfirmed_awards": [_strip_raw(r) for r in unconfirmed],
    }


def _strip_raw(record: dict) -> dict:
    """Drop the bulky raw_detail field from the in-memory record; the full
    payload is already on disk in the cache file."""
    return {k: v for k, v in record.items() if k != "raw_detail"}


if __name__ == "__main__":
    # Quick CLI smoke for the spike: fetch the 4 Qben subsidiaries.
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("supplier", nargs="?", default="Banefjell AS")
    p.add_argument("--cache-dir", default="data/external/doffin/_cli")
    a = p.parse_args()
    out = collect_awards_for_supplier(a.supplier, cache_dir=Path(a.cache_dir))
    print(json.dumps({k: v for k, v in out.items() if k != "confirmed_awards"}, indent=2, ensure_ascii=False))
    print(f"\nconfirmed awards ({len(out['confirmed_awards'])}):")
    for r in out["confirmed_awards"][:5]:
        print(f"  - {r['notice_id']} | {r['publication_date']} | matched={r['matched_alias']!r} | buyer={r['buyer_names']}")
        print(f"    heading: {r['heading'][:80]}")
