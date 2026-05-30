"""Derived taps (v10): cross-tap synthesizers.

A derived tap does NOT hit an external API. It reads the ledger entries
already produced by primary taps (Doffin, TED, BRREG, ...) for a target
hypothesis and its peer-control hypotheses, plus the issuer's PDF, and
emits a new EvidenceEntry that reasons about cross-source consistency.

Why model them as taps at all
-----------------------------
The TriangulationEngine's mental model is: hypothesis -> ledger ->
state. If derived analyses are not in the ledger, they cannot participate
in the engine's invariants (single-source cap, peer-control rule, etc.),
and the dashboard cannot click through to them. Wrapping each derived
analyser as an EvidenceTap keeps the engine, the ledger, and the
dashboard panels uniform.

This module exposes two derived taps both addressing the
revenue_pipeline_support claim category:

1. `RevenueSupportCalculatorTap`
   Reads procurement-tap rows from each peer-control hypothesis,
   counts post-acquisition confirmed prime contracts, and computes a
   `external_support_pct` proxy. Verdict:
     >= 75% of peers have >=1 post-acq award  -> confirms
     50%-75%                                   -> partial
     < 50%                                     -> refutes (drives critical when combined)

2. `ExplanatorySlippageScannerTap`
   Compares `reported_revenue_yoy_pct` (and ebita_margin) against
   positive-framing language detected in the source PDF (CEO comment
   region). When KPIs deteriorated but the narrative is uniformly
   positive, emits `refutes` with the exact offending sentences cached
   under data/external/derived/.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.ingest.external.base import EvidenceTap
from src.triangulation.ledger import LedgerStore
from src.triangulation.types import new_evidence_entry, sha256_of_json


# --- Revenue support calculator -------------------------------------------


class RevenueSupportCalculatorTap(EvidenceTap):
    """Derived tap: aggregates procurement-tap evidence across peer
    subsidiaries to estimate whether reported revenue is grounded in
    external order flow."""

    tap_id = "derived_revenue_support"
    tap_kind = "derived_analysis"
    addressable_question_ids = ["fq_revenue_backed_by_external_awards"]

    def __init__(
        self,
        ledger_store: LedgerStore,
        all_hypotheses_by_id: Dict[str, Dict[str, Any]],
    ):
        self._ledger = ledger_store
        self._hyp_by_id = all_hypotheses_by_id

    def can_address(self, hypothesis: Dict[str, Any]) -> List[str]:
        if hypothesis.get("claim_category") != "revenue_pipeline_support":
            return []
        return self.addressable_questions_on(hypothesis)

    def gather(
        self,
        hypothesis: Dict[str, Any],
        *,
        cache_dir: Path,
        addressed_question_ids: List[str],
    ):
        meta = hypothesis.get("claim_metadata") or {}
        peer_ids: List[str] = hypothesis.get("peer_controls") or []
        threshold_pct: float = float(meta.get("external_support_threshold_pct") or 50.0)
        reported_revenue = meta.get("reported_revenue_msek")

        per_peer = []
        peers_with_post_acq = 0
        peers_with_any_confirmed = 0
        total_post_acq = 0
        total_pre_acq = 0
        confidence_weighted_signal = 0.0

        for peer_id in peer_ids:
            row = self._scan_peer(peer_id)
            per_peer.append(row)
            if row["post_acq_count"] > 0:
                peers_with_post_acq += 1
            if row["confirmed_count"] > 0:
                peers_with_any_confirmed += 1
            total_post_acq += row["post_acq_count"]
            total_pre_acq += row["pre_acq_count"]
            confidence_weighted_signal += row["weighted_signal"]

        n_peers = max(len(peer_ids), 1)
        external_support_pct = round(100.0 * peers_with_post_acq / n_peers, 1)
        any_support_pct = round(100.0 * peers_with_any_confirmed / n_peers, 1)
        weighted_pct = round(100.0 * min(1.0, confidence_weighted_signal / n_peers), 1)

        verdict, confidence, narrative = self._classify(
            external_support_pct=external_support_pct,
            any_support_pct=any_support_pct,
            threshold_pct=threshold_pct,
            n_peers=n_peers,
            total_post_acq=total_post_acq,
            reported_revenue=reported_revenue,
        )

        payload = {
            "method": "v10.revenue_support_calculator",
            "reported_revenue_msek": reported_revenue,
            "external_support_threshold_pct": threshold_pct,
            "peers_evaluated": n_peers,
            "peers_with_post_acq_confirmed_awards": peers_with_post_acq,
            "peers_with_any_confirmed_awards": peers_with_any_confirmed,
            "external_support_pct": external_support_pct,
            "any_support_pct": any_support_pct,
            "confidence_weighted_support_pct": weighted_pct,
            "total_post_acq_awards": total_post_acq,
            "total_pre_acq_awards": total_pre_acq,
            "per_peer_breakdown": per_peer,
        }

        cache_dir.mkdir(parents=True, exist_ok=True)
        out_path = cache_dir / f"{hypothesis['id']}_revenue_support.json"
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        return new_evidence_entry(
            hypothesis_id=hypothesis["id"],
            tap_id=self.tap_id,
            tap_kind=self.tap_kind,
            verdict=verdict,
            confidence=confidence,
            addresses_questions=addressed_question_ids,
            narrative=narrative,
            payload_sha256=sha256_of_json(payload),
            raw_payload_ref=str(out_path),
            payload_summary=payload,
        )

    # ---- helpers ----------------------------------------------------------

    def _scan_peer(self, peer_id: str) -> Dict[str, Any]:
        """Read all procurement-tap rows for a peer hypothesis and
        aggregate the confirmed-award counts."""
        rows = self._ledger.latest_per_tap(peer_id)
        confirmed_count = 0
        post_acq = 0
        pre_acq = 0
        weighted_signal = 0.0
        tap_breakdown = []
        for tap_id, entry in rows.items():
            if entry.get("tap_kind") != "public_procurement":
                continue
            summary = entry.get("payload_summary") or {}
            c = int(summary.get("confirmed_award_count") or 0)
            post = int(summary.get("post_acquisition_award_count") or 0)
            pre = int(summary.get("pre_acquisition_award_count") or 0)
            conf = float(entry.get("confidence") or 0.5)
            confirmed_count += c
            post_acq += post
            pre_acq += pre
            # Signal contribution: post-acq awards weighted by tap confidence,
            # capped per-tap at 1.0 so a single source cannot dominate.
            tap_signal = min(1.0, post * 0.5) * conf
            weighted_signal += tap_signal
            tap_breakdown.append(
                {
                    "tap_id": tap_id,
                    "verdict": entry.get("verdict"),
                    "confidence": conf,
                    "confirmed_award_count": c,
                    "post_acquisition_award_count": post,
                    "pre_acquisition_award_count": pre,
                    "tap_signal": round(tap_signal, 3),
                }
            )
        peer_hyp = self._hyp_by_id.get(peer_id, {})
        return {
            "peer_hypothesis_id": peer_id,
            "peer_entity": peer_hyp.get("entity"),
            "confirmed_count": confirmed_count,
            "post_acq_count": post_acq,
            "pre_acq_count": pre_acq,
            "weighted_signal": round(min(weighted_signal, 1.0), 3),
            "tap_breakdown": tap_breakdown,
        }

    @staticmethod
    def _classify(
        *,
        external_support_pct: float,
        any_support_pct: float,
        threshold_pct: float,
        n_peers: int,
        total_post_acq: int,
        reported_revenue: Optional[float],
    ):
        rev_note = (
            f" reported_revenue_msek={reported_revenue}" if reported_revenue else ""
        )
        if external_support_pct >= 75.0:
            return (
                "confirms",
                0.7,
                f"Across {n_peers} operating subsidiaries, "
                f"{external_support_pct:.0f}% have at least one post-acquisition "
                f"confirmed prime contract in Doffin/TED ({total_post_acq} awards "
                f"total).{rev_note} Order flow is consistent with the stated "
                f"revenue base.",
            )
        if external_support_pct >= threshold_pct:
            return (
                "partial",
                0.6,
                f"Across {n_peers} operating subsidiaries, "
                f"{external_support_pct:.0f}% have at least one post-acquisition "
                f"confirmed prime contract ({total_post_acq} total). This is at "
                f"or above the {threshold_pct:.0f}% threshold, but not a strong "
                f"confirm.{rev_note}",
            )
        return (
            "refutes",
            0.65,
            f"Only {external_support_pct:.0f}% of {n_peers} operating "
            f"subsidiaries show a post-acquisition prime-contract footprint in "
            f"Doffin or TED ({total_post_acq} awards). This is below the "
            f"{threshold_pct:.0f}% external-support threshold; reported revenue "
            f"of "
            f"{reported_revenue} MSEK is poorly grounded in public-registry "
            f"order flow.",
        )


# --- Explanatory slippage scanner -----------------------------------------


# Bilingual positive-framing vocabulary. Each token MUST be a stand-alone
# word; the scanner uses word boundaries.
POSITIVE_FRAMING_TOKENS = [
    # English
    "growth", "growing", "stronger", "strengthen", "strengthens", "strengthened",
    "strong", "robust", "platform", "focused", "focus", "expand", "expansion",
    "strategic", "strategy", "sustainable", "profitable", "consolidating",
    "consolidation", "leading", "leader", "momentum", "milestone", "transform",
    "transformation", "execution", "delivering", "delivered", "deliver",
    "step", "phase", "position", "positions", "positioned", "opportunity",
    "opportunities",
    # Swedish (the Q1 2026 report is Swedish)
    "tillvaxt", "tillväxt", "starkare", "stark", "stärka", "starkt", "hallbart",
    "hållbar", "hållbart", "lonsam", "lönsam", "lönsamhet", "strategiskt",
    "strategisk", "plattform", "fokuserad", "fokuserat", "renodlad",
    "slagkraftig", "ny fas", "konsolidera", "tydlig", "tydliga", "förstärkt",
    "bekräftar", "position", "positionerat", "växande", "stärka", "ledstjarna",
    "ledstjärna", "specialist",
]

# Words that explicitly acknowledge a problem; presence of these mutes the
# slippage finding because the issuer is not hiding the bad news.
ACKNOWLEDGEMENT_TOKENS = [
    # English
    "decline", "declined", "decrease", "decreased", "fell", "loss", "losses",
    "weak", "weakness", "miss", "missed", "underperform", "challenging",
    "headwind", "headwinds", "deterioration", "down ",
    # Swedish
    "minskning", "minskade", "minskat", "minskar", "nedgang", "nedgång",
    "förlust", "svaghet", "negativ", "lägre", "lagre", "utmanande", "motvind",
    "försämring", "forsamring", "sjunkit", "sjönk", "sjonk",
]


class ExplanatorySlippageScannerTap(EvidenceTap):
    """Derived tap: flags revenue/margin deterioration that is framed in
    uniformly positive CEO-comment language ('explanatory slippage')."""

    tap_id = "derived_explanatory_slippage"
    tap_kind = "derived_analysis"
    addressable_question_ids = ["fq_explanatory_narrative_consistent"]

    # CEO commentary tends to live in the first ~5k chars. We only scan
    # this window to keep the signal tight; long boilerplate accounting
    # notes near the end of the report would dilute it.
    SCAN_WINDOW_CHARS = 5000

    def can_address(self, hypothesis: Dict[str, Any]) -> List[str]:
        if hypothesis.get("claim_category") != "revenue_pipeline_support":
            return []
        return self.addressable_questions_on(hypothesis)

    def gather(
        self,
        hypothesis: Dict[str, Any],
        *,
        cache_dir: Path,
        addressed_question_ids: List[str],
    ):
        meta = hypothesis.get("claim_metadata") or {}
        pdf_anchor = hypothesis.get("source_pdf_anchor") or {}
        pdf_path = Path(pdf_anchor.get("file") or "")
        txt_path = pdf_path.with_suffix(".txt") if pdf_path.suffix == ".pdf" else pdf_path

        text = ""
        if txt_path.is_file():
            text = txt_path.read_text(encoding="utf-8", errors="ignore")
        elif pdf_path.is_file():
            # Fall back to inline pdfplumber if no .txt cache exists.
            try:
                import pdfplumber

                with pdfplumber.open(pdf_path) as pdf:
                    text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            except Exception:
                text = ""

        scan_text = text[: self.SCAN_WINDOW_CHARS]
        positive_matches = _find_token_matches(scan_text, POSITIVE_FRAMING_TOKENS)
        ack_matches = _find_token_matches(scan_text, ACKNOWLEDGEMENT_TOKENS)
        positive_count = sum(len(v) for v in positive_matches.values())
        ack_count = sum(len(v) for v in ack_matches.values())

        revenue_yoy = meta.get("reported_revenue_yoy_pct")
        ebita_margin = meta.get("reported_period_ebita_margin_pct")
        kpis_deteriorated = bool(
            (revenue_yoy is not None and float(revenue_yoy) < 0)
            or (ebita_margin is not None and float(ebita_margin) < 0)
        )

        positive_density = round(
            positive_count / max(len(scan_text.split()), 1) * 1000.0, 2
        )

        verdict, confidence, narrative = self._classify(
            kpis_deteriorated=kpis_deteriorated,
            revenue_yoy=revenue_yoy,
            ebita_margin=ebita_margin,
            positive_count=positive_count,
            ack_count=ack_count,
            positive_density=positive_density,
        )

        offending_sentences = _extract_offending_sentences(
            scan_text, positive_matches, max_sentences=8
        ) if verdict == "refutes" else []

        payload = {
            "method": "v10.explanatory_slippage_scanner",
            "scan_window_chars": self.SCAN_WINDOW_CHARS,
            "scan_actual_chars": len(scan_text),
            "kpi_inputs": {
                "reported_revenue_yoy_pct": revenue_yoy,
                "reported_period_ebita_margin_pct": ebita_margin,
                "kpis_deteriorated": kpis_deteriorated,
            },
            "positive_framing_match_count": positive_count,
            "acknowledgement_match_count": ack_count,
            "positive_density_per_1000_words": positive_density,
            "positive_matches": {
                tok: matches for tok, matches in positive_matches.items() if matches
            },
            "acknowledgement_matches": {
                tok: matches for tok, matches in ack_matches.items() if matches
            },
            "offending_sentences": offending_sentences,
            "source_pdf": str(pdf_path),
        }

        cache_dir.mkdir(parents=True, exist_ok=True)
        out_path = cache_dir / f"{hypothesis['id']}_explanatory_slippage.json"
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        return new_evidence_entry(
            hypothesis_id=hypothesis["id"],
            tap_id=self.tap_id,
            tap_kind=self.tap_kind,
            verdict=verdict,
            confidence=confidence,
            addresses_questions=addressed_question_ids,
            narrative=narrative,
            payload_sha256=sha256_of_json(payload),
            raw_payload_ref=str(out_path),
            payload_summary=payload,
        )

    @staticmethod
    def _classify(
        *,
        kpis_deteriorated: bool,
        revenue_yoy,
        ebita_margin,
        positive_count: int,
        ack_count: int,
        positive_density: float,
    ):
        rev_str = f"{revenue_yoy:+.1f}%" if revenue_yoy is not None else "n/a"
        ebita_str = f"{ebita_margin:+.1f}%" if ebita_margin is not None else "n/a"
        ratio = positive_count / max(ack_count, 1)

        if not kpis_deteriorated:
            return (
                "neutral",
                0.6,
                f"No KPI deterioration detected (revenue YoY={rev_str}, "
                f"EBITA margin={ebita_str}); explanatory_slippage rule does "
                f"not apply.",
            )

        if positive_count >= 8 and ack_count == 0:
            return (
                "refutes",
                0.75,
                f"Revenue declined ({rev_str}) and EBITA margin is {ebita_str}, "
                f"yet the CEO-comment region contains {positive_count} "
                f"positive-framing tokens and zero acknowledgement tokens. "
                f"Pure positive framing despite a negative directional move -- "
                f"classic explanatory slippage.",
            )
        if positive_count >= 5 and ratio >= 3.0:
            return (
                "partial",
                0.55,
                f"Revenue declined ({rev_str}) and EBITA margin is {ebita_str}. "
                f"The CEO-comment region has {positive_count} positive-framing "
                f"tokens vs only {ack_count} acknowledgement tokens "
                f"(ratio={ratio:.1f}x). Tilted toward positive framing but the "
                f"directional message is at least minimally acknowledged.",
            )
        return (
            "confirms",
            0.6,
            f"Revenue declined ({rev_str}) and EBITA margin is {ebita_str}, "
            f"but the narrative explicitly acknowledges weakness "
            f"({ack_count} acknowledgement tokens, {positive_count} "
            f"positive tokens). No slippage detected; the issuer's framing "
            f"matches the KPI direction.",
        )


# --- regex helpers --------------------------------------------------------


def _find_token_matches(text: str, tokens: Sequence[str]) -> Dict[str, List[str]]:
    """Return {token: [list of surrounding context snippets]} for each
    token found in text. Word-bounded for single words, substring for
    phrases containing a space."""
    text_lower = text.lower()
    out: Dict[str, List[str]] = {}
    for tok in tokens:
        if not tok:
            continue
        tok_lower = tok.lower()
        if " " in tok_lower:
            # Phrase match -- plain substring with word boundary either side.
            pattern = r"(?:^|\W)" + re.escape(tok_lower) + r"(?:$|\W)"
        else:
            pattern = r"\b" + re.escape(tok_lower) + r"\b"
        snippets = []
        for m in re.finditer(pattern, text_lower):
            start = max(0, m.start() - 40)
            end = min(len(text), m.end() + 60)
            snippet = text[start:end].replace("\n", " ").strip()
            snippets.append(snippet)
            if len(snippets) >= 4:
                break
        if snippets:
            out[tok] = snippets
    return out


def _extract_offending_sentences(
    text: str, positive_matches: Dict[str, List[str]], *, max_sentences: int = 8
) -> List[str]:
    """Pull at most `max_sentences` distinct sentences from `text` that
    contain at least one positive-framing token."""
    text_clean = re.sub(r"\s+", " ", text)
    # Naive sentence split that respects Scandinavian punctuation.
    sentences = re.split(r"(?<=[\.\!\?])\s+(?=[A-ZÅÄÖÆØÉ])", text_clean)
    tokens_lower = [t.lower() for t in positive_matches.keys()]
    out: List[str] = []
    seen: set[str] = set()
    for s in sentences:
        s_lower = s.lower()
        if any(t in s_lower for t in tokens_lower):
            key = s.strip()[:140]
            if key in seen:
                continue
            seen.add(key)
            out.append(s.strip())
            if len(out) >= max_sentences:
                break
    return out
