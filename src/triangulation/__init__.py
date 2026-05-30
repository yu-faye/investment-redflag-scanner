"""Triangulation engine (v9).

Architecture pivot: in v8 each external data source was its own detector
that produced a self-contained finding with a self-assigned severity. In
v9, the first-class object is the *audit hypothesis*. Each data source
becomes an `EvidenceTap` that appends standardised `EvidenceEntry` rows to
the hypothesis's append-only ledger. A single `TriangulationEngine`
computes the hypothesis state from the ledger:

  - derived_severity  (info | warning | critical, NEVER set by a single
                       tap; engine alone owns this)
  - resolved_questions  (which falsification questions are answered)
  - pending_questions   (which are still open + which tap_kind could
                         address them)
  - peer_control_status (did at least one peer hypothesis confirm via the
                         same tap family, so absence isn't a coverage gap?)
  - next_recommended_taps (info-gain ranked roadmap)

The point is to make adding a new data source (BRREG today, Newsweb next)
a localised plug-in: implement one EvidenceTap, declare which
falsification questions it can address, and every existing hypothesis
automatically gets re-triangulated. This is what turns the project from a
collection of cases into a system.
"""
from src.triangulation.types import (
    VERDICTS,
    TAP_KINDS,
    DERIVED_SEVERITIES,
    EvidenceEntry,
    new_evidence_entry,
)
from src.triangulation.ledger import LedgerStore
from src.triangulation.engine import TriangulationEngine

__all__ = [
    "VERDICTS",
    "TAP_KINDS",
    "DERIVED_SEVERITIES",
    "EvidenceEntry",
    "new_evidence_entry",
    "LedgerStore",
    "TriangulationEngine",
]
