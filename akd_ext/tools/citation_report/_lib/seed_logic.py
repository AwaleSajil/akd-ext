"""Seed-paper text retrieval (NO LLM).

The Target Profiler agent reads the SEED paper to decide what to look for in the
citing papers. This tool gives it the best available text for that:

  1. try the full PDF (download to the pdf cache if needed, then extract text)
  2. fall back to title + abstract if the PDF can't be obtained

Pure IO. Returns the text plus a ``source`` flag so the agent knows what it got.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .s3_cache import S3Cache, paper_cache_key
from .pdf_utils import extract_pdf_text_pages
from .s2_downloader import download_arxiv_paper, download_by_doi

DEFAULT_MAX_CHARS = 90_000


def _abstract_text(title: str, abstract: str) -> str:
    parts = []
    if title:
        parts.append(f"Title: {title}")
    if abstract:
        parts.append(f"Abstract: {abstract}")
    return "\n\n".join(parts)


def get_seed_text(
    cache: S3Cache,
    external_ids: dict,
    title: str = "",
    abstract: str = "",
    doi_email: str = "user@example.com",
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict:
    """Best-available text for the seed paper: full PDF, else title+abstract.

    Pass the seed paper's ``external_ids`` (from resolve_paper) plus its ``title`` and
    ``abstract`` for the fallback. Tries the S3 pdf cache first, then a fresh download
    (arXiv direct -> DOI waterfall); on success the PDF is cached for reuse. If no PDF
    can be obtained, returns title+abstract instead.

    Output:
      {status, source, cache_key, page_count, char_count, truncated, text, message}
      source: "pdf" | "abstract" | "none".
      status: "ok" (got pdf or abstract) | "empty" (nothing available) | "error".
    """
    if not cache.enabled:
        return {"status": "error", "source": "none", "cache_key": None,
                "page_count": 0, "char_count": 0, "truncated": False, "text": "",
                "message": "S3 is required for get_seed_text"}

    key = paper_cache_key(external_ids or {})

    def _truncate(full: str):
        trunc = max_chars and max_chars > 0 and len(full) > max_chars
        return (full[:max_chars] if trunc else full), bool(trunc)

    def _fallback(reason: str):
        text = _abstract_text(title, abstract)
        if not text:
            return {"status": "empty", "source": "none", "cache_key": key,
                    "page_count": 0, "char_count": 0, "truncated": False, "text": "",
                    "message": f"No PDF ({reason}) and no title/abstract available."}
        text, trunc = _truncate(text)
        return {"status": "ok", "source": "abstract", "cache_key": key,
                "page_count": 0, "char_count": len(text), "truncated": trunc,
                "text": text, "message": f"Used title+abstract fallback ({reason})."}

    # No id to download by -> straight to fallback.
    if not key:
        return _fallback("no ArXiv/DOI")

    with tempfile.TemporaryDirectory(prefix="cra_seed_") as tmp:
        tmp_dir = Path(tmp)
        local = tmp_dir / f"{key}.pdf"

        # 1. pdf cache first
        have_pdf = False
        if cache.pdf_cache_exists(key) and cache.pdf_cache_get(key, local):
            have_pdf = True
        else:
            # 2. fresh download (arXiv direct -> DOI waterfall), then cache it
            ext = external_ids or {}
            arxiv = ext.get("ArXiv") or ext.get("arXiv")
            doi = ext.get("DOI") or ext.get("doi")
            ok = False
            if arxiv and download_arxiv_paper(str(arxiv), str(local)):
                ok = True
            elif doi:
                res = download_by_doi(str(doi), str(local), str(tmp_dir), doi_email)
                if res.get("status") == "success":
                    src = Path(res.get("path") or local)
                    if src != local:
                        try:
                            src.rename(local)
                        except Exception:
                            local = src
                    ok = True
            if ok and local.exists() and local.stat().st_size > 1000:
                cache.pdf_cache_put(key, local, source="seed")
                have_pdf = True

        if not have_pdf:
            return _fallback("download failed")

        try:
            pages = extract_pdf_text_pages(local)
        except Exception as e:
            return _fallback(f"pdf parse error: {type(e).__name__}")

    full = "\n\n".join(f"--- page {p['page']} ---\n{p['text']}" for p in pages)
    if not full.strip():
        return _fallback("empty pdf text")
    text, trunc = _truncate(full)
    return {"status": "ok", "source": "pdf", "cache_key": key,
            "page_count": len(pages), "char_count": len(text), "truncated": trunc,
            "text": text, "message": None}
