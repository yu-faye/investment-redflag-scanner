"""TED tap (v9, added in v10).

Wraps `ted_client.collect_awards_for_supplier` and maps the response onto
the standard EvidenceEntry vocabulary. Two important asymmetries vs the
Doffin tap drive the confidence calibration here:

1. **TED only carries above-threshold notices.** For Norwegian construction
   contracts the directive threshold is approximately NOK 45M; smaller
   awards never reach TED at all. So `not_found` on TED is *weak* evidence
   of absence (we calibrate confidence ~0.45 instead of Doffin's 0.7).

2. **TED has multi-language winner aliases.** The same legal entity may
   appear with diacritics stripped, case-normalised, or with a localised
   suffix. The client searches per alias and OR-unions the result set, so
   the tap can trust that *any* exact-match winner-name match is a true
   positive.

Verdict mapping (subsidiary_specialist + revenue_pipeline_support):
  >= 1 post-acquisition prime contract  -> confirms (TED is canonical EU-tier registry)
  >= 1 pre-acquisition only              -> partial (acquired stale capacity)
  0 post-acq + 0 pre-acq + 0 hits        -> not_found (low-medium confidence)
  >= 1 hit but no winner-name match      -> refutes (name collision space, no actual awards)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from src.ingest.external import ted_client
from src.ingest.external.base import EvidenceTap
from src.triangulation.types import new_evidence_entry, sha256_of_json


class TedTap(EvidenceTap):
    tap_id = "ted"
    tap_kind = "public_procurement"
    addressable_question_ids = [
        "fq_direct_prime_contracts",
        "fq_eu_threshold_contracts",
        "fq_revenue_backed_by_external_awards",
    ]

    def can_address(self, hypothesis: Dict[str, Any]) -> List[str]:
        return self.addressable_questions_on(hypothesis)

    def gather(
        self,
        hypothesis: Dict[str, Any],
        *,
        cache_dir: Path,
        addressed_question_ids: List[str],
    ):
        meta = hypothesis.get("claim_metadata") or {}
        entity = hypothesis.get("entity") or "?"
        aliases = hypothesis.get("entity_aliases") or []
        acquired_year = meta.get("acquired_year")
        date_from = meta.get("ted_publication_date_from")

        result = ted_client.collect_awards_for_supplier(
            entity,
            cache_dir=cache_dir,
            expected_winner_aliases=aliases,
            publication_date_from=date_from,
        )
        confirmed = result.get("confirmed_awards") or []
        hits_total = result.get("search_hits_total") or 0
        post_acq, pre_acq = self._split_by_acquisition(confirmed, acquired_year)

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
            "ted_threshold_caveat": (
                "TED carries EU-directive-threshold notices only "
                "(~NOK 45M for works, ~NOK 1.4-2M for goods/services). "
                "Absence on TED is weaker evidence than absence on Doffin."
            ),
            "confirmed_awards": [
                {
                    "notice_id": a.get("notice_id"),
                    "public_url": a.get("public_url"),
                    "publication_date": a.get("publication_date"),
                    "buyer_names": a.get("buyer_names"),
                    "winner_names": a.get("winner_names"),
                    "heading": a.get("heading"),
                    "place_of_performance": a.get("place_of_performance"),
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
        if n >= 1 and len(post_acq) >= 1:
            return (
                "confirms",
                0.85,
                f"TED returned {n} EU-tier prime-contract notice(s) where {entity} "
                f"appears as a winner ({len(post_acq)} post-acquisition, "
                f"{len(pre_acq)} pre-acquisition). TED is the canonical EU-wide "
                f"public-procurement registry; above-threshold awards here are "
                f"hard evidence of executing capability.",
            )
        if n >= 1 and len(pre_acq) > 0:
            return (
                "partial",
                0.5,
                f"TED returned {n} prime-contract notice(s) won by {entity}, but "
                f"all {len(pre_acq)} predate the acquisition. The post-acquisition "
                f"EU-tier activity that would substantiate organic growth is absent.",
            )
        if n == 0 and hits_total == 0:
            return (
                "not_found",
                0.45,
                f"TED returned 0 notices where {entity} appears as a winner. "
                f"TED carries only EU-directive-threshold awards (~NOK 45M for "
                f"works); a smaller-scale supplier may legitimately have no TED "
                f"footprint. This is weaker than Doffin absence.",
            )
        if n == 0 and hits_total > 0:
            return (
                "refutes",
                0.55,
                f"TED returned {hits_total} text-search hit(s) but none had "
                f"{entity} in the official winner-name field. Name-collision "
                f"space only; the entity itself does not appear as a winner at "
                f"EU-tier scale.",
            )
        return (
            "neutral",
            0.35,
            f"TED returned {n} confirmed award(s) and {hits_total} search hit(s); "
            f"insufficient to take a position.",
        )
