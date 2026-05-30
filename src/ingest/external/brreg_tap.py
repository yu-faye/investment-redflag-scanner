"""BRREG (Norwegian central register) tap.

Addresses three falsification questions when present on a hypothesis:
  - fq_entity_age_and_existence   (blocking_for_critical=true)
  - fq_industry_classification_match
  - fq_entity_identity_unambiguous

Verdict logic per question is encoded below. Single tap, multiple
questions in one EvidenceEntry -- the engine handles multi-question
resolution from the addresses_questions list.

Caching: writes the raw API response (the full enheter list, not just
the top match) to:
  data/external/brreg/<company_id>/<entity-slug>_<date>.json
with sha256 + fetched_at_utc + matched_by.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from src.ingest.external import brreg_client
from src.ingest.external.base import EvidenceTap
from src.triangulation.types import new_evidence_entry, sha256_of_json


class BrregTap(EvidenceTap):
    tap_id = "brreg"
    tap_kind = "company_registry"
    addressable_question_ids = [
        "fq_entity_age_and_existence",
        "fq_industry_classification_match",
        "fq_entity_identity_unambiguous",
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
        entity = hypothesis.get("entity") or "?"
        aliases = hypothesis.get("entity_aliases") or []
        meta = hypothesis.get("claim_metadata") or {}
        naics_prefix = meta.get("expected_naering_prefix")

        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            result = brreg_client.best_match(
                entity,
                aliases=aliases,
                expected_naering_prefix=naics_prefix,
            )
        except Exception as e:
            return new_evidence_entry(
                hypothesis_id=hypothesis["id"],
                tap_id=self.tap_id,
                tap_kind=self.tap_kind,
                verdict="error",
                confidence=0.0,
                addresses_questions=addressed_question_ids,
                narrative=f"BRREG fetch failed for {entity}: {e!s}",
                error=str(e),
            )

        # Cache the raw API response (includes ALL candidates, not just
        # top match) so reviewers can rerun disambiguation by hand.
        date_part = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = self._slug(entity)
        cache_path = cache_dir / f"{slug}_{date_part}.json"
        cache_payload = {
            "supplied_name": entity,
            "aliases_used": aliases,
            "expected_naering_prefix": naics_prefix,
            "fetched_at_utc": result["fetched_at_utc"],
            "query_url": result["query_url"],
            "matched_by": result["matched_by"],
            "total_hits": result["total_hits"],
            "top_match": result["top_match"],
            "all_candidates_summary": result["all_candidates_summary"],
            "raw_response": result["raw_response"],
        }
        raw_bytes = json.dumps(cache_payload, ensure_ascii=False, sort_keys=True).encode(
            "utf-8"
        )
        cache_path.write_bytes(raw_bytes)
        sha = sha256_of_json(cache_payload)

        verdict, confidence, narrative, summary = self._classify(
            entity=entity,
            result=result,
            naics_prefix=naics_prefix,
            addressed_question_ids=addressed_question_ids,
            acquired_year=meta.get("acquired_year"),
        )

        try:
            cache_path_rel = str(cache_path.relative_to(Path.cwd()))
        except ValueError:
            cache_path_rel = str(cache_path)

        return new_evidence_entry(
            hypothesis_id=hypothesis["id"],
            tap_id=self.tap_id,
            tap_kind=self.tap_kind,
            verdict=verdict,
            confidence=confidence,
            addresses_questions=addressed_question_ids,
            narrative=narrative,
            payload_sha256=sha,
            raw_payload_ref=cache_path_rel,
            query_url=result["query_url"],
            entity_match_orgnr=(result.get("top_match") or {}).get("organisasjonsnummer"),
            payload_summary=summary,
        )

    # ---- classification --------------------------------------------------

    def _classify(
        self,
        *,
        entity,
        result,
        naics_prefix,
        addressed_question_ids,
        acquired_year,
    ):
        top = result.get("top_match")
        matched_by = result.get("matched_by")
        total_hits = result.get("total_hits")
        candidates_summary = result.get("all_candidates_summary") or []

        # No match at all -> entity does not exist in BRREG.
        if not top or matched_by == "none":
            return (
                "refutes",
                0.85,
                f"BRREG enhetsregisteret has no entity matching '{entity}'. "
                f"For a Norwegian AS this is the canonical registry; absence "
                f"means the entity does not legally exist under that name.",
                {
                    "matched_by": matched_by,
                    "total_hits": total_hits,
                    "candidates_summary": candidates_summary,
                },
            )

        orgnr = top.get("organisasjonsnummer")
        navn = top.get("navn")
        stiftet = top.get("stiftelsesdato")
        ansatte = top.get("antallAnsatte")
        naering = (top.get("naeringskode1") or {}).get("kode")
        naering_label = (top.get("naeringskode1") or {}).get("beskrivelse")
        sletted = top.get("slettedato")
        konkurs = top.get("konkurs")
        avvikling = top.get("underAvvikling")

        summary = {
            "matched_by": matched_by,
            "total_hits": total_hits,
            "top_match": {
                "orgnr": orgnr,
                "navn": navn,
                "stiftelsesdato": stiftet,
                "antallAnsatte": ansatte,
                "naeringskode1": naering,
                "naering_label": naering_label,
                "slettedato": sletted,
                "konkurs": konkurs,
                "underAvvikling": avvikling,
            },
            "candidates_summary": candidates_summary,
        }

        # Compose narrative based on which questions this tap is
        # addressing (so the same EvidenceEntry surfaces the relevant
        # facts for whatever question is being viewed).
        narrative_bits: List[str] = []
        narrative_bits.append(
            f"BRREG resolved '{entity}' to orgnr {orgnr} '{navn}' (match={matched_by}, "
            f"{len(candidates_summary)} candidates inspected from the top page; "
            f"BRREG free-text search reports {total_hits} dataset entries containing "
            f"any of the name tokens)."
        )
        if stiftet:
            try:
                age_years = (
                    datetime.now(timezone.utc).date()
                    - datetime.fromisoformat(stiftet).date()
                ).days / 365.25
                narrative_bits.append(
                    f"Founded {stiftet} (~{age_years:.1f} years old)."
                )
            except (TypeError, ValueError):
                narrative_bits.append(f"Founded {stiftet}.")
        if ansatte is not None:
            narrative_bits.append(f"Reports {ansatte} employees.")
        if naering:
            narrative_bits.append(
                f"NAICS={naering} ({naering_label})."
            )
        if sletted:
            narrative_bits.append(f"DELETED on {sletted}.")
        if konkurs:
            narrative_bits.append("Marked BANKRUPT.")
        if avvikling:
            narrative_bits.append("Marked UNDER LIQUIDATION.")

        # ---- Decide verdict + confidence -------------------------------
        # Identity-resolution problem -> overrules everything else.
        # We use the inspected-candidates count (top page of name search)
        # for the noise threshold; total_hits from BRREG is a token-OR
        # count and not useful for this purpose.
        candidate_pool = len(candidates_summary)
        if matched_by == "substring" and candidate_pool >= 8:
            narrative_bits.append(
                f"WARNING: identity ambiguous -- top page returned {candidate_pool} "
                f"name candidates and the engine selected by substring only. "
                f"Conclusions about this entity are unreliable until disambiguated "
                f"by orgnr."
            )
            return (
                "partial",
                0.3,
                " ".join(narrative_bits),
                summary,
            )

        # NAICS mismatch -> strong refute of the specialist claim.
        naics_match = (
            naics_prefix is None
            or (naering and naering.startswith(naics_prefix))
        )
        if naics_prefix and not naics_match:
            narrative_bits.append(
                f"NAICS prefix MISMATCH: expected {naics_prefix}*, got {naering}. "
                f"The entity exists but operates in a different industry than "
                f"claimed."
            )
            return (
                "refutes",
                0.8,
                " ".join(narrative_bits),
                summary,
            )

        # Striken / bankrupt / under liquidation -> strong refute.
        if sletted or konkurs or avvikling:
            return (
                "refutes",
                0.85,
                " ".join(narrative_bits),
                summary,
            )

        # Young company (founded within ~2 years of acquisition) -> partial.
        if stiftet and acquired_year:
            try:
                year_founded = int(stiftet[:4])
                if year_founded >= acquired_year - 1:
                    narrative_bits.append(
                        f"Note: founded {year_founded} vs acquired {acquired_year} -- "
                        f"the 'specialist with operating history' framing is "
                        f"weak (entity is essentially the same age as the acquisition)."
                    )
                    return (
                        "partial",
                        0.5,
                        " ".join(narrative_bits),
                        summary,
                    )
            except ValueError:
                pass

        # Healthy NAICS match, real employees, multi-year history -> confirms.
        return (
            "confirms",
            0.85,
            " ".join(narrative_bits),
            summary,
        )


if __name__ == "__main__":
    import json as J
    import sys
    from pathlib import Path as P

    sys.path.insert(0, str(P(__file__).resolve().parents[3]))
    from src.triangulation.types import VERDICTS  # noqa

    hyp = {
        "id": "hyp_qben_slam_specialist",
        "entity": "SLAM Jernbaneteknikk AS",
        "entity_aliases": ["SLAM Jernbaneteknikk"],
        "claim_metadata": {
            "acquired_year": 2024,
            "expected_naering_prefix": "42.12",
        },
        "falsification_questions": [
            {"id": "fq_entity_age_and_existence", "relevant_tap_kinds": ["company_registry"]},
            {"id": "fq_industry_classification_match", "relevant_tap_kinds": ["company_registry"]},
        ],
    }
    tap = BrregTap()
    addressed = tap.can_address(hyp)
    entry = tap.gather(
        hyp, cache_dir=P("data/external/brreg/_cli"), addressed_question_ids=addressed
    )
    print(J.dumps(entry, indent=2, ensure_ascii=False))
