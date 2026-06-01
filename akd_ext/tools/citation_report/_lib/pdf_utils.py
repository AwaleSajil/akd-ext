"""PDF text extraction using PyMuPDF.

Vendored from prithvi_usuage_from_pdf_openai/pdf_utils.py (unchanged).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def extract_pdf_text_pages(pdf_path: Path) -> list[dict[str, Any]]:
    """Extract text from a PDF, one dict per page: [{"page": 1, "text": "..."}, ...]."""
    try:
        import fitz  # type: ignore  # PyMuPDF
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "Missing dependency for PDF extraction. Install PyMuPDF:\n"
            "  pip install pymupdf\n"
            f"Original error: {e}"
        ) from e

    doc = fitz.open(str(pdf_path))
    pages: list[dict[str, Any]] = []
    for i in range(len(doc)):
        page = doc.load_page(i)
        text = page.get_text("text") or ""
        text = text.replace("\r\n", "\n")
        text = re.sub(r"[ \t]+\n", "\n", text)
        pages.append({"page": i + 1, "text": text})
    doc.close()
    return pages


def pages_to_text(pages: list[dict[str, Any]], max_chars: int = 90_000) -> str:
    """Join page dicts into one string with page markers; truncate at max_chars."""
    full_text = "\n\n".join(f"--- page {p['page']} ---\n{p['text']}" for p in pages)
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "\n\n[TEXT TRUNCATED]"
    return full_text
