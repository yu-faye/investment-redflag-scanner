"""Append-only evidence ledger per hypothesis.

One JSONL file per hypothesis under data/ledger/<hypothesis_id>.jsonl.
Every EvidenceTap.gather() call appends one line. Nothing ever rewrites
or deletes a row -- that's what makes this auditable. If a tap is re-run
later (e.g. fresh Doffin pull next quarter) it appends a new row; the
engine reads all rows but typically prefers the most recent per tap.

The ledger is git-tracked in the working repo (see README), so the audit
trail is itself a git commit history.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from src.triangulation.types import EvidenceEntry


class LedgerStore:
    """Filesystem-backed append-only ledger keyed by hypothesis_id."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, hypothesis_id: str) -> Path:
        # Normalise hypothesis_id so it's a safe filename.
        safe = hypothesis_id.replace("/", "_").replace("\\", "_")
        return self.root / f"{safe}.jsonl"

    def append(self, entry: EvidenceEntry) -> None:
        if "hypothesis_id" not in entry:
            raise ValueError("EvidenceEntry missing hypothesis_id")
        p = self.path_for(entry["hypothesis_id"])
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def read(self, hypothesis_id: str) -> List[EvidenceEntry]:
        p = self.path_for(hypothesis_id)
        if not p.is_file():
            return []
        out: List[EvidenceEntry] = []
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # Skip malformed lines but never raise -- ledger
                    # readability must survive partial writes.
                    continue
        return out

    def latest_per_tap(self, hypothesis_id: str) -> Dict[str, EvidenceEntry]:
        """Most recent EvidenceEntry per tap_id for the given hypothesis."""
        rows = self.read(hypothesis_id)
        rows.sort(key=lambda r: r.get("gathered_at_utc", ""))
        out: Dict[str, EvidenceEntry] = {}
        for r in rows:
            tap = r.get("tap_id")
            if tap:
                out[tap] = r
        return out

    def truncate(self, hypothesis_id: str) -> None:
        """Wipe one hypothesis's ledger. Use for repeatable test runs;
        never call from production code paths."""
        p = self.path_for(hypothesis_id)
        if p.is_file():
            p.unlink()
