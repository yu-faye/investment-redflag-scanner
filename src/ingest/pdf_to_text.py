"""PDF to text extraction.

Optional dependency on pypdf. If a .txt sibling already exists (pre-extracted),
prefer that to avoid PDF parsing churn during demos.
"""
import os


def pdf_to_text(pdf_path):
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(pdf_path)
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "pypdf not installed. Run: pip install -r requirements.txt"
        ) from exc

    reader = PdfReader(pdf_path)
    parts = []
    for idx, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        parts.append(f"<<<PAGE {idx}>>>\n{text}")
    return "\n".join(parts)


def load_text_or_pdf(path):
    """Accept .txt or .pdf. Plain text is returned as-is."""
    lower = path.lower()
    if lower.endswith(".txt"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    if lower.endswith(".pdf"):
        return pdf_to_text(path)
    raise ValueError(f"Unsupported file extension: {path}")
