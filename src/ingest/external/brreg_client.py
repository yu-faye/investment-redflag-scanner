"""Brønnøysundregistrene (BRREG) enhetsregisteret client.

BRREG is the Norwegian central register of companies. The
enhetsregisteret API is fully public, no key needed:

  GET https://data.brreg.no/enhetsregisteret/api/enheter
      ?navn=<name>&size=N
      -> { _embedded: { enheter: [<entity>...] }, page: {totalElements} }

Each entity carries:
  - organisasjonsnummer  (9-digit national id)
  - navn                 (legal name, uppercase)
  - stiftelsesdato       (YYYY-MM-DD registration date)
  - antallAnsatte        (employee count, when reported)
  - naeringskode1        ({ kode: "42.120", beskrivelse: "..." }) NAICS / NACE
  - forretningsadresse   (postal address dict)
  - konkurs              (bankrupt?), underAvvikling (under liquidation?)
  - slettedato           (deletion date if struck off)

This module is the thin HTTP shim; the BrregTap turns its dicts into
EvidenceEntry rows.
"""
from __future__ import annotations

import hashlib
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

API_BASE = "https://data.brreg.no/enhetsregisteret/api/enheter"

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "qben-redflag-scanner/0.9 (https://github.com/research-spike)",
}


def _ssl_ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


def _http_get(url: str, *, timeout: int = 25) -> tuple[Dict[str, Any], str]:
    req = urllib.request.Request(url, headers=_HEADERS, method="GET")
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=timeout) as resp:
        raw = resp.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"BRREG GET {url} returned non-JSON ({e}). First 200 bytes: {raw[:200]!r}"
        )
    return data, hashlib.sha256(raw).hexdigest()


def search_by_name(name: str, *, size: int = 10) -> Dict[str, Any]:
    """Free-text name search. Returns raw API response dict.

    Note: search is whitespace-tokenised and very tolerant. Generic names
    like "Team Bygg" return thousands of hits. Always pair with an
    additional disambiguation step (NAICS prefix match, address match,
    or known orgnr) before trusting the top hit.
    """
    url = f"{API_BASE}?navn={urllib.parse.quote(name)}&size={size}"
    data, _ = _http_get(url)
    data["__query_url"] = url
    return data


def fetch_by_orgnr(orgnr: str) -> Dict[str, Any]:
    """Direct lookup by 9-digit organisasjonsnummer. The most reliable
    way to anchor on a specific entity."""
    orgnr = str(orgnr).strip()
    if not (orgnr.isdigit() and len(orgnr) == 9):
        raise ValueError(f"orgnr must be 9 digits, got {orgnr!r}")
    url = f"{API_BASE}/{orgnr}"
    data, _ = _http_get(url)
    data["__query_url"] = url
    return data


def best_match(
    name: str,
    *,
    aliases: Optional[List[str]] = None,
    expected_naering_prefix: Optional[str] = None,
    size: int = 25,
) -> Dict[str, Any]:
    """Search by name, then pick the single best match.

    Match priority:
      1. Exact case-insensitive match of `navn` against (name + aliases).
      2. Case-insensitive match AND naeringskode1.kode starts with
         expected_naering_prefix.
      3. Substring match of name in entity navn, with NAICS prefix tie-break.
      4. None.

    Returns a dict with: top_match (entity or None), matched_by ("exact" /
    "exact+naics" / "substring" / "none"), all_candidates_summary (small
    list for dashboard), raw_response (full API JSON for cache), query_url.
    """
    aliases = aliases or []
    needle_set = {n.strip().upper() for n in [name, *aliases] if n}
    raw = search_by_name(name, size=size)
    candidates = (raw.get("_embedded") or {}).get("enheter") or []

    def naics_ok(ent):
        if not expected_naering_prefix:
            return True
        kode = ((ent.get("naeringskode1") or {}).get("kode")) or ""
        return kode.startswith(expected_naering_prefix)

    exact_naics_match = None
    exact_any_match = None
    substring_match = None
    for ent in candidates:
        navn = (ent.get("navn") or "").upper()
        if navn in needle_set:
            if naics_ok(ent) and exact_naics_match is None:
                exact_naics_match = ent
            if exact_any_match is None:
                exact_any_match = ent
        if substring_match is None and any(n in navn for n in needle_set):
            substring_match = ent

    top, matched_by = None, "none"
    if exact_naics_match:
        top, matched_by = exact_naics_match, "exact+naics"
    elif exact_any_match:
        top, matched_by = exact_any_match, "exact"
    elif substring_match:
        top, matched_by = substring_match, "substring"

    summary = [
        {
            "orgnr": c.get("organisasjonsnummer"),
            "navn": c.get("navn"),
            "stiftelsesdato": c.get("stiftelsesdato"),
            "antallAnsatte": c.get("antallAnsatte"),
            "naeringskode1": (c.get("naeringskode1") or {}).get("kode"),
            "naering_label": (c.get("naeringskode1") or {}).get("beskrivelse"),
            "slettedato": c.get("slettedato"),
            "underAvvikling": c.get("underAvvikling"),
            "konkurs": c.get("konkurs"),
        }
        for c in candidates[:8]
    ]
    return {
        "supplied_name": name,
        "aliases_used": aliases,
        "expected_naering_prefix": expected_naering_prefix,
        "matched_by": matched_by,
        "top_match": top,
        "total_hits": (raw.get("page") or {}).get("totalElements", len(candidates)),
        "all_candidates_summary": summary,
        "query_url": raw.get("__query_url"),
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "raw_response": raw,
    }


if __name__ == "__main__":
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else "Banefjell"
    naics = sys.argv[2] if len(sys.argv) > 2 else "42.12"
    res = best_match(name, aliases=[name + " AS"], expected_naering_prefix=naics)
    print(json.dumps({
        "matched_by": res["matched_by"],
        "total_hits": res["total_hits"],
        "top_match": (res["top_match"] or {}).get("navn"),
        "top_orgnr": (res["top_match"] or {}).get("organisasjonsnummer"),
        "top_naering": (res["top_match"] or {}).get("naeringskode1"),
        "top_stiftet": (res["top_match"] or {}).get("stiftelsesdato"),
        "top_ansatte": (res["top_match"] or {}).get("antallAnsatte"),
    }, indent=2, ensure_ascii=False))
