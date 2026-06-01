"""Pure-IO, per-paper tools (NO LLM calls).

The agent itself runs the LLM extraction. These two tools just move bytes:

  - ``get_pdf_text``  : pull a PDF from the S3 pdf cache and extract its text.
  - ``save_report``   : write the agent-produced JSON report to the S3 report store.

A small ``get_report`` reader is included so the agent can do cache-first behavior
(skip re-analysis when a report already exists) without any LLM call.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .s3_cache import S3Cache
from .pdf_utils import extract_pdf_text_pages

# Default char cap so a huge PDF can't blow up the agent's context window. The agent
# can override; pages carry "--- page N ---" markers so page hints survive truncation.
DEFAULT_MAX_CHARS = 90_000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_pdf_text(
    pdf_cache_key: str,
    cache: S3Cache,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> dict:
    """Pull a PDF from the S3 pdf cache (by ``pdf_cache_key``) and extract its text.

    No LLM. Returns the page-marked text for the agent to analyze.

    Output:
      {status, cache_key, page_count, char_count, truncated, text, message}
      status: "ok" | "no_key" | "no_pdf" | "failed" | "error".
    """
    if not cache.enabled:
        return {"status": "error", "cache_key": pdf_cache_key,
                "message": "S3 is required for get_pdf_text",
                "page_count": 0, "char_count": 0, "truncated": False, "text": ""}
    if not pdf_cache_key:
        return {"status": "no_key", "cache_key": None,
                "message": "pdf_cache_key is required",
                "page_count": 0, "char_count": 0, "truncated": False, "text": ""}

    if not cache.pdf_cache_exists(pdf_cache_key):
        return {"status": "no_pdf", "cache_key": pdf_cache_key,
                "message": "PDF not in cache; run download_pdfs first",
                "page_count": 0, "char_count": 0, "truncated": False, "text": ""}

    with tempfile.TemporaryDirectory(prefix="cra_txt_") as tmp:
        pdf_path = Path(tmp) / f"{pdf_cache_key}.pdf"
        if not cache.pdf_cache_get(pdf_cache_key, pdf_path):
            return {"status": "no_pdf", "cache_key": pdf_cache_key,
                    "message": "PDF fetch from cache failed",
                    "page_count": 0, "char_count": 0, "truncated": False, "text": ""}
        try:
            pages = extract_pdf_text_pages(pdf_path)
        except Exception as e:
            return {"status": "failed", "cache_key": pdf_cache_key,
                    "message": f"{type(e).__name__}: {e}",
                    "page_count": 0, "char_count": 0, "truncated": False, "text": ""}

    full = "\n\n".join(f"--- page {p['page']} ---\n{p['text']}" for p in pages)
    truncated = max_chars is not None and max_chars > 0 and len(full) > max_chars
    text = (full[:max_chars] + "\n\n[TEXT TRUNCATED]") if truncated else full

    return {
        "status": "ok",
        "cache_key": pdf_cache_key,
        "page_count": len(pages),
        "char_count": len(text),
        "truncated": truncated,
        "text": text,
        "message": None,
    }


def save_report(
    cache_key: str,
    report: dict,
    cache: S3Cache,
    seed_paper_id: str,
    version: str = "v1",
    paper_id: str = "",
) -> dict:
    """Write an agent-produced JSON ``report`` to the S3 report store.

    No LLM. The agent does the analysis and hands the finished report dict here.
    Stored at reports/{seed_paper_id}/{version}/{cache_key}.json — namespaced by the
    SEED paper so a new seed paper gets its own report space. A small ``saved_at`` /
    ``paper_id`` envelope is added under ``report["_meta"]`` without touching the
    agent's content. (``cache_key``/``paper_id`` here identify the CITING paper.)

    Output: {status, cache_key, s3_uri, message}.
    """
    if not cache.enabled:
        return {"status": "error", "cache_key": cache_key,
                "s3_uri": None, "message": "S3 is required for save_report"}
    if not seed_paper_id:
        return {"status": "no_seed", "cache_key": cache_key,
                "s3_uri": None, "message": "seed_paper_id is required"}
    if not cache_key:
        return {"status": "no_key", "cache_key": None,
                "s3_uri": None, "message": "cache_key is required"}
    if not isinstance(report, dict):
        return {"status": "error", "cache_key": cache_key,
                "s3_uri": None, "message": "report must be a JSON object"}

    enveloped = dict(report)
    meta = dict(enveloped.get("_meta") or {})
    meta.update({"saved_at": _utc_now_iso(), "paper_id": paper_id or meta.get("paper_id", ""),
                 "cache_key": cache_key, "seed_paper_id": seed_paper_id, "version": version})
    enveloped["_meta"] = meta

    uri = cache.agent_report_put(seed_paper_id, version, cache_key, enveloped)
    return {"status": "ok", "cache_key": cache_key, "s3_uri": uri, "message": None}


def get_report(
    cache_key: str,
    cache: S3Cache,
    seed_paper_id: str,
    version: str = "v1",
) -> dict:
    """Read a previously-saved report from the S3 report store (cache-first helper).

    No LLM. Lets the agent skip re-analysis when a report already exists. Reports are
    namespaced by the SEED paper: reports/{seed_paper_id}/{version}/{cache_key}.json.

    Output: {status, cache_key, exists, report, message}. status "ok" | "no_key" |
    "no_seed" | "error".
    """
    if not cache.enabled:
        return {"status": "error", "cache_key": cache_key, "exists": False,
                "report": None, "message": "S3 is required for get_report"}
    if not seed_paper_id:
        return {"status": "no_seed", "cache_key": cache_key, "exists": False,
                "report": None, "message": "seed_paper_id is required"}
    if not cache_key:
        return {"status": "no_key", "cache_key": None, "exists": False,
                "report": None, "message": "cache_key is required"}

    doc = cache.agent_report_get(seed_paper_id, version, cache_key)
    return {"status": "ok", "cache_key": cache_key, "exists": doc is not None,
            "report": doc, "message": None}
