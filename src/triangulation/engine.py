"""TriangulationEngine: hypothesis state from evidence ledger.

This is the brain of the v9 architecture. It enforces the rules that no
single data source -- however damning -- can drive a hypothesis to
critical on its own. The output is a TriangulationState dict that the
orchestrator translates into a finding payload and the dashboards render
into matrix cells + roadmap rows.

Key invariants (read these before tweaking severity logic):

  1. Single-source rule: a hypothesis with evidence from <=1 tap cannot
     graduate to "critical". The most it can earn from one tap is
     "warning". This is the structural protection against "the database
     didn't collect it" failure mode.

  2. Peer control rule: refuting evidence is only treated at full weight
     when at least one peer hypothesis confirmed via the same tap_id
     (proving the tap reaches this entity class). Without a passing
     peer control, refuting evidence is downgraded one band.

  3. Falsification coverage rule: critical additionally requires that
     every falsification_question marked blocking_for_critical=true has
     at least one EvidenceEntry addressing it. Outstanding blocking
     questions cap severity at warning.

  4. Engine is the only writer: detectors and taps must not set
     derived_severity directly.

v10 category-specific rules (run AFTER the generic rules above):

  R7. revenue_pipeline_support
      - The two derived taps (derived_revenue_support,
        derived_explanatory_slippage) measure two *independent* axes
        (quantitative external order flow vs qualitative narrative
        framing). When BOTH refute, the result is treated as a "two-
        dimensional double fail" and graduates the hypothesis to
        critical -- the single-source rule does not apply because the
        two tap_ids are distinct, and the peer-control rule does not
        apply because derived_revenue_support already aggregates peer
        evidence by construction.
      - When only one of the two derived taps refutes, severity caps
        at warning (one axis broken, the other inconclusive).
      - Blocking falsification questions still cap severity if any
        remain unresolved.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from src.triangulation.types import EvidenceEntry


_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def _max_sev(*sevs: str) -> str:
    return max(sevs, key=lambda s: _SEVERITY_RANK.get(s, 0))


def _min_sev(*sevs: str) -> str:
    return min(sevs, key=lambda s: _SEVERITY_RANK.get(s, 0))


class TriangulationEngine:
    """Stateless engine. All inputs explicit; output is pure data."""

    def derive(
        self,
        hypothesis: Dict[str, Any],
        ledger_entries: List[EvidenceEntry],
        peer_states: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Compute a TriangulationState for one hypothesis.

        Args:
          hypothesis: dict loaded from validation/hypotheses.json
          ledger_entries: all rows from this hypothesis's ledger
          peer_states: hypothesis_id -> already-derived state dict, used
            for peer_control checks. Caller is responsible for derivation
            order (peers first).

        Returns: TriangulationState (see docstring at module level).
        """
        peer_states = peer_states or {}

        # 1) Group ledger by tap_id; latest row per tap wins for current
        # state, but we still keep counts of all rows for reproducibility.
        latest_per_tap = self._latest_per_tap(ledger_entries)
        total_entries = len(ledger_entries)
        distinct_taps = sorted(latest_per_tap.keys())

        # 2) Falsification coverage.
        all_questions = hypothesis.get("falsification_questions") or []
        question_index = {q["id"]: q for q in all_questions}
        addressed_by: Dict[str, List[str]] = {q["id"]: [] for q in all_questions}
        for tap_id, entry in latest_per_tap.items():
            for qid in entry.get("addresses_questions", []) or []:
                if qid in addressed_by:
                    addressed_by[qid].append(tap_id)
        resolved_questions = [qid for qid, taps in addressed_by.items() if taps]
        pending_questions = [
            {
                "question_id": qid,
                "text": question_index[qid].get("text"),
                "blocking_for_critical": question_index[qid].get(
                    "blocking_for_critical", False
                ),
                "relevant_tap_kinds": question_index[qid].get(
                    "relevant_tap_kinds", []
                ),
            }
            for qid in addressed_by
            if not addressed_by[qid]
        ]
        blocking_unresolved = [
            q for q in pending_questions if q["blocking_for_critical"]
        ]

        # 3) Peer control: did at least one peer hypothesis get a
        # `confirms` verdict from one of the taps we used? That proves
        # the tap reaches this entity class.
        peer_control_taps_passing: Set[str] = set()
        for peer_id in hypothesis.get("peer_controls", []) or []:
            peer = peer_states.get(peer_id)
            if not peer:
                continue
            for tap_id in distinct_taps:
                peer_entry = (peer.get("latest_per_tap") or {}).get(tap_id)
                if peer_entry and peer_entry.get("verdict") == "confirms":
                    peer_control_taps_passing.add(tap_id)
        peer_control_status = {
            "checked": bool(hypothesis.get("peer_controls")),
            "peer_ids": list(hypothesis.get("peer_controls") or []),
            "taps_with_passing_peer": sorted(peer_control_taps_passing),
        }

        # 4) Score the verdict landscape.
        verdict_summary: Dict[str, int] = {}
        for entry in latest_per_tap.values():
            v = entry.get("verdict")
            if v:
                verdict_summary[v] = verdict_summary.get(v, 0) + 1
        confirming_taps = [
            tap
            for tap, entry in latest_per_tap.items()
            if entry.get("verdict") in ("confirms", "partial")
        ]
        refuting_taps = [
            tap
            for tap, entry in latest_per_tap.items()
            if entry.get("verdict") in ("refutes", "not_found")
        ]

        # 5) Base severity from verdict landscape (single-source guarded).
        base_sev = "info"
        if refuting_taps:
            # Refuting always raises a concern even with one source,
            # because the cost of missing a real concern is higher than
            # the cost of investigating a false alarm. But it caps at
            # warning when only one source is present.
            base_sev = "warning"
            # Critical requires:
            #   (a) >=2 refuting sources OR (>=1 refuting + >=1 peer
            #       control passed on a different tap)
            #   AND no blocking falsification question outstanding
            two_or_more_refuting = len(refuting_taps) >= 2
            refuted_and_peer_passes = (
                len(refuting_taps) >= 1
                and len(peer_control_taps_passing) >= 1
                and not blocking_unresolved
            )
            if (
                (two_or_more_refuting and not blocking_unresolved)
                or (refuted_and_peer_passes and len(refuting_taps) >= 2)
            ):
                base_sev = "critical"
        elif confirming_taps:
            # No refuting evidence, only confirming: info (the claim
            # checks out so far; not a red flag).
            base_sev = "info"
        else:
            # All neutral / errors / pending.
            base_sev = "info"

        # 6) Severity cap from blocking falsification coverage.
        if blocking_unresolved and base_sev == "critical":
            # Outstanding blocking question -> cap at warning.
            base_sev = "warning"

        # 7) If single source AND no peer control, force-cap at warning.
        if len(distinct_taps) <= 1 and not peer_control_taps_passing:
            if base_sev == "critical":
                base_sev = "warning"

        # --- v10 category-specific rules (R7) ------------------------------
        # Applied AFTER the generic single-source / peer-control caps so the
        # invariants above remain the floor; category rules can only relax
        # them when the category structure justifies it.
        category_rule_notes: List[str] = []
        if hypothesis.get("claim_category") == "revenue_pipeline_support":
            base_sev, note = self._apply_revenue_pipeline_rule(
                base_sev=base_sev,
                latest_per_tap=latest_per_tap,
                blocking_unresolved=blocking_unresolved,
            )
            if note:
                category_rule_notes.append(note)

        derived_severity = base_sev

        # 8) Reasons why critical was not awarded (or why it was). This
        # is shown to analysts as the "what would graduate this" panel.
        blockers_for_critical: List[str] = []
        if blocking_unresolved:
            blockers_for_critical.append(
                f"{len(blocking_unresolved)} blocking falsification question(s) "
                f"unresolved: "
                + ", ".join(q["question_id"] for q in blocking_unresolved)
            )
        if len(distinct_taps) <= 1:
            blockers_for_critical.append(
                "single-source rule: <=1 tap has reported; critical requires >=2 "
                "independent confirming or refuting taps."
            )
        # Peer-control blocker does not apply to revenue_pipeline_support
        # (the derived_revenue_support tap already aggregates peer ledgers).
        if (
            refuting_taps
            and not peer_control_taps_passing
            and hypothesis.get("peer_controls")
            and hypothesis.get("claim_category") != "revenue_pipeline_support"
        ):
            blockers_for_critical.append(
                "peer-control rule: no peer hypothesis has yet returned a "
                "`confirms` verdict from any tap we used, so we cannot rule "
                "out a tap coverage gap."
            )

        # 9) Next recommended taps -- maximise resolution of blocking
        # unresolved falsification questions first.
        tap_used_kinds = {
            entry.get("tap_kind") for entry in latest_per_tap.values()
        }
        next_recommended_taps: List[Dict[str, Any]] = []
        seen_kinds: Set[str] = set()
        for q in pending_questions:
            for kind in q.get("relevant_tap_kinds", []) or []:
                if kind in tap_used_kinds or kind in seen_kinds:
                    continue
                next_recommended_taps.append(
                    {
                        "tap_kind": kind,
                        "addresses_question_id": q["question_id"],
                        "blocking_for_critical": q["blocking_for_critical"],
                        "expected_information_gain": (
                            0.7 if q["blocking_for_critical"] else 0.3
                        ),
                    }
                )
                seen_kinds.add(kind)
        next_recommended_taps.sort(
            key=lambda r: r["expected_information_gain"], reverse=True
        )

        # 10) Final TriangulationState.
        return {
            "hypothesis_id": hypothesis["id"],
            "claim_category": hypothesis.get("claim_category"),
            "derived_severity": derived_severity,
            "verdict_summary": verdict_summary,
            "confirming_taps": confirming_taps,
            "refuting_taps": refuting_taps,
            "distinct_taps": distinct_taps,
            "total_ledger_entries": total_entries,
            "resolved_falsification_questions": resolved_questions,
            "pending_falsification_questions": pending_questions,
            "blockers_for_critical": blockers_for_critical,
            "peer_control_status": peer_control_status,
            "next_recommended_taps": next_recommended_taps,
            "latest_per_tap": latest_per_tap,
            "category_rule_notes": category_rule_notes,
        }

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _latest_per_tap(entries: List[EvidenceEntry]) -> Dict[str, EvidenceEntry]:
        out: Dict[str, EvidenceEntry] = {}
        for e in sorted(entries, key=lambda r: r.get("gathered_at_utc", "")):
            tap = e.get("tap_id")
            if tap:
                out[tap] = e
        return out

    @staticmethod
    def _apply_revenue_pipeline_rule(
        *,
        base_sev: str,
        latest_per_tap: Dict[str, EvidenceEntry],
        blocking_unresolved: List[Dict[str, Any]],
    ) -> Tuple[str, Optional[str]]:
        """v10 R7: revenue_pipeline_support category-specific rule.

        Returns the new severity and an optional explanation note.

        Logic:
          - rev_support_verdict := derived_revenue_support tap's verdict
          - slip_verdict        := derived_explanatory_slippage tap's verdict
          - Both refute AND no blocking unresolved -> critical
          - Exactly one refutes                    -> warning (no relaxation)
          - Neither refutes                        -> leave base_sev as-is
        """
        rev_entry = latest_per_tap.get("derived_revenue_support")
        slip_entry = latest_per_tap.get("derived_explanatory_slippage")
        rev_v = (rev_entry or {}).get("verdict") if rev_entry else None
        slip_v = (slip_entry or {}).get("verdict") if slip_entry else None
        rev_refutes = rev_v in ("refutes", "not_found")
        slip_refutes = slip_v in ("refutes", "not_found")

        if rev_refutes and slip_refutes:
            if blocking_unresolved:
                return (
                    base_sev,
                    "R7 (revenue_pipeline_support): both derived dimensions "
                    "refute, but blocking falsification questions remain "
                    "unresolved -- severity capped at the pre-R7 value.",
                )
            note = (
                "R7 (revenue_pipeline_support): both derived dimensions "
                f"refute (revenue_support={rev_v}, explanatory_slippage="
                f"{slip_v}). Two independent axes failing in the same "
                "direction is treated as critical even without peer-tap "
                "confirms."
            )
            return ("critical", note)
        if rev_refutes ^ slip_refutes:
            single = "revenue_support" if rev_refutes else "explanatory_slippage"
            note = (
                f"R7 (revenue_pipeline_support): only one derived axis refutes "
                f"({single}); severity capped at warning until the other axis "
                "also resolves."
            )
            new_sev = base_sev if _SEVERITY_RANK.get(base_sev, 0) >= 1 else "warning"
            # Never escalate beyond warning from this rule alone.
            if _SEVERITY_RANK.get(new_sev, 0) > 1:
                new_sev = "warning"
            return (new_sev, note)
        return (base_sev, None)


# --- Public utility -------------------------------------------------------


def derive_all(
    hypotheses: List[Dict[str, Any]],
    ledger_store,
) -> Dict[str, Dict[str, Any]]:
    """Convenience wrapper used by the orchestrator and dashboards."""
    engine = TriangulationEngine()
    # Two-pass derivation so peer states are available when needed.
    # Pass 1: compute states ignoring peer_controls.
    states: Dict[str, Dict[str, Any]] = {}
    for hyp in hypotheses:
        states[hyp["id"]] = engine.derive(
            hyp, ledger_store.read(hyp["id"]), peer_states={}
        )
    # Pass 2: redo, this time with peer_states injected.
    final: Dict[str, Dict[str, Any]] = {}
    for hyp in hypotheses:
        final[hyp["id"]] = engine.derive(
            hyp, ledger_store.read(hyp["id"]), peer_states=states
        )
    return final
