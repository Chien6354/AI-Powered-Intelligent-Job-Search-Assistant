from __future__ import annotations

from pathlib import Path


def extract_pdf_text(path: Path) -> tuple[str, list[str]]:
    """Return (text, quality_flags). No OCR."""
    import fitz  # PyMuPDF

    flags: list[str] = []
    doc = fitz.open(path)
    parts: list[str] = []
    try:
        for page in doc:
            parts.append(page.get_text() or "")
        page_count = doc.page_count or 1
    finally:
        doc.close()
    text = "\n".join(parts).strip()
    if len(text) < 20:
        flags.append("empty_text")
    pages = page_count
    if len(text) / pages < 30:
        flags.append("low_text_density")
    return text, flags
