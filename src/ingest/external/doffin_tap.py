"""Doffin tap (v9 wrapper around v8 doffin_client).

This is the thin shim that lets the v9 triangulation engine drive the
existing `collect_awards_for_supplier()`. The legacy client is preserved
unchanged for any direct callers; this tap is the only entry point used
by the v9 orchestrator.

Verdict mapping (subsidiary_specialist hypothesis class):
  - >= 3 confirmed prime-contract awards, majority post-acquisition
        -> confirms (high confidence)
  - 1 or 2 confirmed, majority post-acquisition
        -> partial
  - >= 1 confirmed but all pre-acquisition (acquired stale capacity)
        -> partial (low confidence) with narrative warning
  - 0 confirmed AND 0 unconfirmed search hits
        -> not_found (weak refute; tap is canonical for this question)
  - 0 confirmed BUT >0 unconfirmed (name collisions only)
        -> refutes (we searched, name space is noisy, none was actually
           awarded contracts under the company we care about)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from src.ingest.external import doffin_client
from src.ingest.external.base import EvidenceTap
from src.triangulation.types import (
    new_evidence_entry,
    sha256_of_json,
)


class DoffinTap(EvidenceTap):
    tap_id = "doffin"
    tap_kind = "public_procurement"
    addressable_question_ids = ["fq_direct_prime_contracts"]

    def can_address(self, hypothesis: Dict[str, Any]) -> List[str]:
        return self.addressable_questions_on(hypothesis)

    def gather(
        self,
        hypothesis: Dict[str, Any],
        *,
        cache_dir: Path,
        addressed_question_ids: List[str],
    ):
        entity = hypothesis.get("entity") or "?"
        aliases = hypothesis.get("entity_aliases") or []
        acquired_year = (hypothesis.get("claim_metadata") or {}).get("acquired_year")

        result = doffin_client.collect_awards_for_supplier(
            entity,
            cache_dir=cache_dir,
            expected_winner_aliases=aliases,
        )
        confirmed = result.get("confirmed_awards") or []
        hits_total = result.get("search_hits_total") or 0
        post_acq, pre_acq = self._split_by_acquisition(confirmed, acquired_year)

        # Verdict logic per docstring.
        verdict, confidence, narrative = self._classify(
            confirmed=confirmed,
            post_acq=post_acq,
            pre_acq=pre_acq,
            hits_total=hits_total,
            entity=entity,
        )

        payload_summary = {
            "confirmed_award_count": len(confirmed),
            "post_acquisition_award_count": len(post_acq),
            "pre_acquisition_award_count": len(pre_acq),
            "search_hits_total": hits_total,
            "confirmed_awards": [
                {
                    "notice_id": a.get("notice_id"),
                    "public_url": a.get("public_url"),
                    "publication_date": a.get("publication_date"),
                    "buyer_names": a.get("buyer_names"),
                    "awarded_names": a.get("awarded_names"),
                    "heading": a.get("heading"),
                }
                for a in confirmed
            ],
        }

        return new_evidence_entry(
            hypothesis_id=hypothesis["id"],
            tap_id=self.tap_id,
            tap_kind=self.tap_kind,
            verdict=verdict,
            confidence=confidence,
            addresses_questions=addressed_question_ids,
            narrative=narrative,
            payload_sha256=result.get("sha256") or sha256_of_json(payload_summary),
            raw_payload_ref=result.get("cache_path"),
            query_url=result.get("query_url"),
            payload_summary=payload_summary,
        )

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _split_by_acquisition(awards, acquired_year):
        post, pre = [], []
        if not acquired_year:
            return list(awards), []
        for a in awards:
            pub = a.get("publication_date") or ""
            try:
                year = int(pub[:4])
            except (TypeError, ValueError):
                year = None
            if year is None:
                pre.append(a)
            elif year >= acquired_year:
                post.append(a)
            else:
                pre.append(a)
        return post, pre

    @staticmethod
    def _classify(*, confirmed, post_acq, pre_acq, hits_total, entity):
        n = len(confirmed)
        if n >= 3 and len(post_acq) >= len(pre_acq):
            return (
                "confirms",
                0.85,
                f"Doffin returned {n} confirmed prime-contract awards to {entity}, "
                f"{len(post_acq)} post-acquisition and {len(pre_acq)} pre-acquisition. "
                f"This is consistent with the claim.",
            )
        if 1 <= n <= 2 and len(post_acq) >= len(pre_acq):
            return (
                "partial",
                0.55,
                f"Doffin returned {n} confirmed prime-contract award(s) to {entity} "
                f"({len(post_acq)} post-acquisition, {len(pre_acq)} pre-acquisition). "
                f"Some activity is present but volume is below a 'specialist / "
                f"established player' threshold for the relevant window.",
            )
        if n >= 1 and len(pre_acq) > len(post_acq):
            return (
                "partial",
                0.35,
                f"Doffin returned {n} confirmed award(s) to {entity}, but "
                f"{len(pre_acq)} of them predate the acquisition. The post-acquisition "
                f"activity that would substantiate organic growth is absent.",
            )
        if n == 0 and hits_total == 0:
            return (
                "not_found",
                0.7,
                f"Doffin returned 0 search hits and 0 confirmed awards for {entity}. "
                f"For a Norwegian state-rail-adjacent specialist this is the canonical "
                f"registry; absence is a weak-to-moderate refute. Subcontractor chains, "
                f"private-sector clients, and consortium framework agreements remain as "
                f"alternative hypotheses that this tap cannot test.",
            )
        if n == 0 and hits_total > 0:
            return (
                "refutes",
                0.6,
                f"Doffin returned {hits_total} text-search hit(s) but none had {entity} "
                f"in the official awardedNames field. The search space is noisy "
                f"(name collisions) but the entity itself does not appear as a winner.",
            )
        return (
            "neutral",
            0.4,
            f"Doffin returned {n} confirmed award(s) and {hits_total} search hit(s); "
            f"insufficient to take a position.",
        )
