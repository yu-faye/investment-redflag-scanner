"""External public-data clients (v8).

Each module here wraps one publicly-accessible registry / regulator endpoint
the scanner can cross-reference against company-self-reported PDFs. The
shared design rules:

  1. Cache every raw API response to disk under data/external/<source>/...
     with sha256, fetched_at_utc and source_url so the finding is
     reproducible without re-hitting the source.
  2. Never silently swallow auth changes or schema drifts -- raise with a
     small reproduction recipe (URL + status code + first 200 bytes).
  3. Stay close to "thin client" -- detectors do the matching / scoring;
     these clients just fetch and persist.

v8 ships only the Doffin client (Norwegian public procurement). B (insider
trading / Newsweb) and C (BRREG related-party) will live alongside it
later.
"""
