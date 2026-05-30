"""TED (EU public procurement, Tenders Electronic Daily) client.

The TED v3 public API exposes notices from the EU Official Journal and from
EEA states (NO, IS, LI) where contracts exceed the EU directive thresholds.

Endpoint: POST https://api.ted.europa.eu/v3/notices/search
  body: {"query": "<expert search expression>", "limit": N, "fields": [...]}
  -> {"notices": [...], "totalNoticeCount": N, "iterationNextToken": "...",
      "timedOut": bool}

No API key required. The expert-search field vocabulary is documented at
https://docs.ted.europa.eu/api/latest/index.html .

Why TED matters for v10
-----------------------
Doffin (NO) only mirrors a *subset* of high-value notices to TED, and the
fields differ subtly. TED is the canonical EU-wide registry. For Norwegian
EEA-tier construction contracts (typically NOK 45M+ for works), TED often
has a notice that Doffin's search API misses, so adding TED as a second
public_procurement tap lets the triangulation engine arbitrate single-
source absences (the user's "what if the database just doesn't have it?"
concern).
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SEARCH_URL = "https://api.ted.europa.eu/v3/notices/search"
PUBLIC_NOTICE_URL_TMPL = "https://ted.europa.eu/en/notice/-/detail/{notice_id}"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

DEFAULT_FIELDS = [
    "publication-number",
    "publication-date",
    "notice-title",
    "buyer-name",
    "winner-name",
    "notice-type",
    "place-of-performance",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _http_json(url: str, *, body: Optional[dict] = None, timeout: int = 25) -> tuple[dict, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers=_HEADERS, method="POST" if data else "GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw), raw
    except urllib.error.HTTPError as e:
        body_preview = e.read()[:300].decode("utf-8", "replace")
        raise RuntimeError(
            f"TED HTTP {e.code} for {url}\n"
            f"  body sent: {json.dumps(body)[:200] if body else 'n/a'}\n"
            f"  response: {body_preview}\n"
            f"  reproduce: curl -X POST '{url}' "
            f"-H 'Content-Type: application/json' "
            f"--data-raw '{json.dumps(body) if body else ''}'"
        ) from e


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ------------------------- Public API -------------------------------------


def search_by_winner(
    winner_name: str,
    *,
    limit: int = 50,
    extra_query: str = "",
) -> dict:
    """Find TED notices where `winner_name` appears in the winner-name field.

    `extra_query` lets the caller AND additional clauses, e.g.
    'AND publication-date>=20240101'. TED expert-query syntax: clauses
    joined by AND/OR; string equality with `field="value"`; date is
    `field>=YYYYMMDD`.
    """
    query = f'winner-name="{winner_name}"'
    if extra_query:
        query = f"({query}) {extra_query.strip()}"
    body = {"query": query, "limit": limit, "fields": DEFAULT_FIELDS}
    j, _raw = _http_json(SEARCH_URL, body=body)
    return j


def search_by_buyer(
    buyer_name: str,
    *,
    limit: int = 50,
    extra_query: str = "",
) -> dict:
    """Find TED notices where `buyer_name` appears in the buyer-name field."""
    query = f'buyer-name="{buyer_name}"'
    if extra_query:
        query = f"({query}) {extra_query.strip()}"
    body = {"query": query, "limit": limit, "fields": DEFAULT_FIELDS}
    j, _raw = _http_json(SEARCH_URL, body=body)
    return j


# ------------------------- Caching wrapper --------------------------------


def collect_awards_for_supplier(
    supplier_name: str,
    *,
    cache_dir: Path,
    expected_winner_aliases: Optional[list[str]] = None,
    publication_date_from: Optional[str] = None,
) -> dict:
    """End-to-end: search TED for every notice where `supplier_name` is a
    winner; persist the raw API response under cache_dir; return a thin
    summary dict the EvidenceTap consumes.

    `publication_date_from` is an ISO date string ('YYYY-MM-DD'); when
    provided, the query is narrowed via TED expert syntax.

    Returns:
      {
        supplier_name, aliases_used, query_url, fetched_at_utc,
        sha256, cache_path,
        search_hits_total,
        confirmed_awards: [
          {notice_id, public_url, publication_date, buyer_names,
           winner_names, matched_alias, heading}
        ],
      }
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    aliases = [supplier_name] + (expected_winner_aliases or [])
    aliases_lower = [a.lower() for a in aliases]

    fetched_at = _now_iso()
    extra = ""
    if publication_date_from:
        ymd = publication_date_from.replace("-", "")
        extra = f"AND publication-date>={ymd}"

    raw = {
        "supplier_name": supplier_name,
        "aliases_used": aliases,
        "query_url": SEARCH_URL,
        "query_body": {
            "query": f'winner-name="{supplier_name}"' + (f" ({extra})" if extra else ""),
            "limit": 50,
            "fields": DEFAULT_FIELDS,
        },
        "fetched_at_utc": fetched_at,
    }

    try:
        # Search per alias (TED expert search does not OR string fields well
        # across alias variants without quoting tricks; one call per alias
        # gives us deterministic provenance).
        all_notices: list[dict] = []
        seen: set[str] = set()
        for alias in aliases:
            try:
                resp = search_by_winner(alias, extra_query=extra, limit=50)
            except RuntimeError as e:
                raw.setdefault("alias_errors", []).append({"alias": alias, "error": str(e)})
                continue
            for n in resp.get("notices") or []:
                pid = n.get("publication-number")
                if pid and pid not in seen:
                    seen.add(pid)
                    all_notices.append(n)
            time.sleep(0.25)
        raw["search_ok"] = True
        raw["search_hits"] = all_notices
    except Exception as e:
        raw["search_ok"] = False
        raw["search_error"] = str(e)
        all_notices = []

    confirmed: list[dict] = []
    for n in all_notices:
        winners = _flatten_eng(n.get("winner-name"))
        match = None
        for w in winners:
            if any(alias in (w or "").lower() for alias in aliases_lower):
                match = w
                break
        if match is None:
            # The text search hit a non-winner field (description, etc.).
            # We deliberately drop these -- TED's full-text recall is wide
            # so winner-name disagreement is treated as a name collision.
            continue
        pid = n.get("publication-number") or ""
        confirmed.append(
            {
                "notice_id": pid,
                "public_url": PUBLIC_NOTICE_URL_TMPL.format(notice_id=pid),
                "publication_date": (n.get("publication-date") or "")[:10],
                "buyer_names": _flatten_eng(n.get("buyer-name")),
                "winner_names": winners,
                "matched_alias": match,
                "heading": _pick_eng_title(n.get("notice-title")),
                "place_of_performance": _flatten_eng(n.get("place-of-performance")),
            }
        )

    raw["confirmed_awards"] = confirmed
    raw["search_hits_total"] = len(all_notices)
    raw["search_hits_post_filter"] = len(confirmed)

    slug = "".join(c if c.isalnum() else "-" for c in supplier_name.lower()).strip("-")
    day = fetched_at[:10]
    out_path = cache_dir / f"{slug}_{day}.json"

    blob = json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    out_path.write_bytes(blob)
    sha = _sha256_bytes(blob)

    # Express cache_path relative to repo root if we can; falls back to abs.
    try:
        cache_path_str = str(out_path.relative_to(out_path.parents[3]))
    except (IndexError, ValueError):
        cache_path_str = str(out_path)

    return {
        "supplier_name": supplier_name,
        "aliases_used": aliases,
        "query_url": SEARCH_URL,
        "fetched_at_utc": fetched_at,
        "sha256": sha,
        "cache_path": cache_path_str,
        "search_hits_total": len(all_notices),
        "search_hits_post_filter": len(confirmed),
        "confirmed_awards": confirmed,
    }


def _flatten_eng(field):
    """TED returns localized fields as {"eng": [...], "fra": [...], ...}.
    Prefer English when present; otherwise take whatever language has
    content."""
    if field is None:
        return []
    if isinstance(field, dict):
        for key in ("eng", "ENG"):
            if key in field and field[key]:
                v = field[key]
                return v if isinstance(v, list) else [v]
        for v in field.values():
            if v:
                return v if isinstance(v, list) else [v]
        return []
    if isinstance(field, list):
        return field
    return [field]


def _pick_eng_title(title_field) -> str:
    if not title_field:
        return ""
    if isinstance(title_field, dict):
        for key in ("eng", "ENG"):
            if key in title_field and title_field[key]:
                v = title_field[key]
                return v if isinstance(v, str) else (v[0] if v else "")
        for v in title_field.values():
            if v:
                return v if isinstance(v, str) else (v[0] if v else "")
        return ""
    return str(title_field)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("supplier", nargs="?", default="Banefjell AS")
    p.add_argument("--cache-dir", default="data/external/ted/_cli")
    p.add_argument("--from", dest="date_from", default=None)
    a = p.parse_args()
    out = collect_awards_for_supplier(
        a.supplier,
        cache_dir=Path(a.cache_dir),
        publication_date_from=a.date_from,
    )
    print(
        json.dumps(
            {k: v for k, v in out.items() if k != "confirmed_awards"},
            indent=2,
            ensure_ascii=False,
        )
    )
    print(f"\nconfirmed awards ({len(out['confirmed_awards'])}):")
    for r in out["confirmed_awards"][:5]:
        print(
            f"  - {r['notice_id']} | {r['publication_date']} | "
            f"matched={r['matched_alias']!r} | buyer={r['buyer_names']}"
        )
        print(f"    heading: {(r['heading'] or '')[:90]}")
