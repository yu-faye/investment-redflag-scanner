"""Shared vocabulary for the triangulation system.

Verdicts, tap kinds, and the EvidenceEntry shape are deliberately the
same across every data source so the engine can reason about them
uniformly. Adding a new tap family means picking the right `tap_kind`
and emitting EvidenceEntry rows -- nothing more.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TypedDict


# --- Verdict vocabulary ----------------------------------------------------
# Each tap emits exactly one verdict per EvidenceEntry. The engine treats
# them with the following polarity:
#   confirms   ->  pushes hypothesis toward "supported"
#   partial    ->  pushes hypothesis toward "supported" with reduced weight
#   refutes    ->  pushes hypothesis toward "concern"
#   not_found  ->  weakly toward "concern" if the tap is the canonical
#                  registry for this question; pure neutral otherwise
#   neutral    ->  tap ran, addresses the question, but the answer doesn't
#                  bear on the claim either way
#   error      ->  tap failed; engine should treat as "pending" not silence
VERDICTS = (
    "confirms",
    "partial",
    "refutes",
    "not_found",
    "neutral",
    "error",
)

# --- Tap kind families -----------------------------------------------------
# Hypotheses declare which tap_kinds can address each falsification
# question. New taps slot in by claiming one or more tap_kind labels.
TAP_KINDS = (
    "public_procurement",       # Doffin (NO), TED (EU)
    "company_registry",         # Brønnøysundregistrene (BRREG, NO)
    "financial_filings",        # Proff, Bisnode, Companies House
    "insider_trading",          # Newsweb, Finanstilsynet
    "subcontractor_directory",  # industry directories (not yet implemented)
    "employee_signal",          # LinkedIn, job postings
    "media",                    # press releases, news wires
    # v10: derived taps run after primary taps in a second pass and
    # synthesize new EvidenceEntry rows from other ledger entries. They
    # never hit external APIs; they reason about cross-tap consistency.
    "derived_analysis",
)

# --- Severity bands the engine may derive ---------------------------------
# A single tap CANNOT set these. Only TriangulationEngine.derive() does.
DERIVED_SEVERITIES = ("info", "warning", "critical")


# --- EvidenceEntry shape ---------------------------------------------------
class EvidenceEntry(TypedDict, total=False):
    """One append-only row in a hypothesis's ledger.

    Required: hypothesis_id, tap_id, tap_kind, gathered_at_utc, verdict,
              confidence, addresses_questions, narrative.
    Optional: payload_sha256, raw_payload_ref, query_url, error,
              entity_match_orgnr, payload_summary (small structured
              extract for dashboard rendering without re-reading raw).
    """
    hypothesis_id: str
    tap_id: str
    tap_kind: str
    gathered_at_utc: str
    verdict: str
    confidence: float
    addresses_questions: List[str]
    narrative: str
    payload_sha256: Optional[str]
    raw_payload_ref: Optional[str]
    query_url: Optional[str]
    error: Optional[str]
    entity_match_orgnr: Optional[str]
    payload_summary: Optional[Dict[str, Any]]


def new_evidence_entry(
    *,
    hypothesis_id: str,
    tap_id: str,
    tap_kind: str,
    verdict: str,
    confidence: float,
    addresses_questions: List[str],
    narrative: str,
    payload_sha256: Optional[str] = None,
    raw_payload_ref: Optional[str] = None,
    query_url: Optional[str] = None,
    error: Optional[str] = None,
    entity_match_orgnr: Optional[str] = None,
    payload_summary: Optional[Dict[str, Any]] = None,
) -> EvidenceEntry:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got {verdict!r}")
    if tap_kind not in TAP_KINDS:
        raise ValueError(f"tap_kind must be one of {TAP_KINDS}, got {tap_kind!r}")
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be in [0,1], got {confidence}")
    return {
        "hypothesis_id": hypothesis_id,
        "tap_id": tap_id,
        "tap_kind": tap_kind,
        "gathered_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": verdict,
        "confidence": round(confidence, 3),
        "addresses_questions": list(addresses_questions),
        "narrative": narrative,
        "payload_sha256": payload_sha256,
        "raw_payload_ref": raw_payload_ref,
        "query_url": query_url,
        "error": error,
        "entity_match_orgnr": entity_match_orgnr,
        "payload_summary": payload_summary,
    }


def sha256_of_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_of_json(obj: Any) -> str:
    return sha256_of_bytes(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8"))
