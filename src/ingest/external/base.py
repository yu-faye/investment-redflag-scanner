"""Common abstract base for v9 EvidenceTaps.

Each external data source implements one EvidenceTap subclass. The tap
takes a hypothesis and decides whether it can address it; if yes, it
calls the upstream API, caches the raw response, and emits an
EvidenceEntry through the engine's standard vocabulary. The tap does NOT
set a derived_severity -- only TriangulationEngine does.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.triangulation.types import EvidenceEntry


class EvidenceTap(ABC):
    """Abstract base class. Every external data source subclasses this."""

    #: Stable identifier for this tap instance, e.g. "doffin".
    tap_id: str = "abstract"

    #: One of TAP_KINDS in src/triangulation/types.py.
    tap_kind: str = "abstract"

    #: Falsification questions this tap can address. The engine cross-
    #: references this against a hypothesis's declared falsification
    #: questions to decide if `gather` should be called.
    addressable_question_ids: List[str] = []

    @abstractmethod
    def can_address(self, hypothesis: Dict[str, Any]) -> List[str]:
        """Return the subset of the hypothesis's falsification_questions
        this tap can address. Empty list -> tap is not applicable to this
        hypothesis."""

    @abstractmethod
    def gather(
        self,
        hypothesis: Dict[str, Any],
        *,
        cache_dir: Path,
        addressed_question_ids: List[str],
    ) -> EvidenceEntry:
        """Run the upstream call and emit one EvidenceEntry. Must cache
        the raw response to `cache_dir` and store the sha256 + path on
        the returned entry."""

    # ---- shared helpers for subclasses -----------------------------------

    @staticmethod
    def _slug(name: str) -> str:
        return (
            name.lower()
            .replace("/", "-")
            .replace(" ", "-")
            .replace(",", "")
            .replace(".", "")
            .replace("\\", "-")
        )

    def addressable_questions_on(self, hypothesis: Dict[str, Any]) -> List[str]:
        """Default implementation of can_address based on
        addressable_question_ids + tap_kind match against the
        hypothesis's falsification questions."""
        out: List[str] = []
        for q in hypothesis.get("falsification_questions") or []:
            if q["id"] in self.addressable_question_ids and self.tap_kind in (
                q.get("relevant_tap_kinds") or []
            ):
                out.append(q["id"])
        return out
