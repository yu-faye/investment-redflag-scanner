"""Crop a sentence-level region out of a PDF and return / save it as a PNG.

This is the v5 "evidence grounding" layer. The detector already records the
page + bbox of the matched sentence (see `excerpt_locator.py`). This module
turns that bbox into a small image snippet so the dashboard can ship the
original-typography proof inline, without embedding a full PDF viewer.

Used by `validation/run_real_report.py` to pre-generate one PNG per finding
under `outputs/evidence/<company>/<key>.png`. Both the static dashboard and
the Streamlit app render that PNG directly.

Coordinate system:
- pdfplumber and PyMuPDF both use top-left origin, units in points (1/72 inch).
- The `bbox` dict is `{x0, top, x1, bottom, width, height}` as produced by
  `excerpt_locator.locate_excerpt`.
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional, Tuple


def _try_import_fitz():
    try:
        import fitz  # type: ignore

        return fitz
    except ImportError:
        return None


def _expanded_rect(fitz_mod, page, bbox: dict, pad_x: float, pad_y: float,
                   expand_to_full_line: bool):
    """Build the clip Rect, padded for legibility and (optionally) expanded
    horizontally to the full text column so the cropped row reads naturally."""
    page_rect = page.rect
    top = max(page_rect.y0, bbox["top"] - pad_y)
    bottom = min(page_rect.y1, bbox["bottom"] + pad_y)
    if expand_to_full_line:
        # most A4 financial reports have ~24-50pt left/right margins; use 24pt
        # as a safe inner-margin so we keep typography hints but drop the
        # whitespace around them.
        x0 = max(page_rect.x0, page_rect.x0 + 24)
        x1 = min(page_rect.x1, page_rect.x1 - 24)
    else:
        x0 = max(page_rect.x0, bbox["x0"] - pad_x)
        x1 = min(page_rect.x1, bbox["x1"] + pad_x)
    return fitz_mod.Rect(x0, top, x1, bottom)


def crop_evidence_snippet(
    pdf_path: str,
    page_no: int,
    bbox: dict,
    pad_x: float = 12.0,
    pad_y: float = 10.0,
    expand_to_full_line: bool = True,
    scale: float = 2.0,
) -> Optional[bytes]:
    """Return PNG bytes for the bbox on `page_no` (1-indexed) of `pdf_path`.

    Returns None when PyMuPDF is unavailable, the file is missing, or the
    page / bbox is invalid. Designed to never raise on dirty inputs."""
    fitz_mod = _try_import_fitz()
    if fitz_mod is None or not pdf_path or not os.path.isfile(pdf_path):
        return None
    if not bbox or "x0" not in bbox:
        return None
    try:
        doc = fitz_mod.open(pdf_path)
    except Exception:
        return None
    try:
        if page_no < 1 or page_no > doc.page_count:
            return None
        page = doc[page_no - 1]
        rect = _expanded_rect(
            fitz_mod, page, bbox, pad_x, pad_y, expand_to_full_line
        )
        if rect.is_empty or rect.width <= 0 or rect.height <= 0:
            return None
        matrix = fitz_mod.Matrix(scale, scale)
        pix = page.get_pixmap(clip=rect, matrix=matrix, alpha=False)
        return pix.tobytes("png")
    except Exception:
        return None
    finally:
        try:
            doc.close()
        except Exception:
            pass


def evidence_key(
    company_id: str, rule_id: str, page_no: int, bbox: dict, excerpt: str
) -> str:
    """Stable 12-char digest used as the snippet filename. Same finding ->
    same filename across runs, so git diffs stay clean."""
    blob = f"{company_id}|{rule_id}|{page_no}|{round(bbox.get('top', 0), 1)}|{round(bbox.get('x0', 0), 1)}|{(excerpt or '')[:80]}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def write_evidence_snippet(
    pdf_path: str,
    page_no: int,
    bbox: dict,
    out_path: str,
    **kwargs,
) -> Optional[str]:
    """Write the snippet to disk. Returns out_path on success, else None."""
    data = crop_evidence_snippet(pdf_path, page_no, bbox, **kwargs)
    if data is None:
        return None
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def snippet_size_bytes(path: str) -> Optional[int]:
    if not path or not os.path.isfile(path):
        return None
    return os.path.getsize(path)


def png_dimensions(png_bytes: bytes) -> Optional[Tuple[int, int]]:
    """Read width/height from PNG header without depending on Pillow."""
    if not png_bytes or len(png_bytes) < 24:
        return None
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    import struct

    width, height = struct.unpack(">II", png_bytes[16:24])
    return int(width), int(height)
