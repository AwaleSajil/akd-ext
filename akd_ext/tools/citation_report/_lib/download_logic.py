"""Step 3: download citing-paper PDFs, S3-pdf-cache first (silent, no user prompt).

Per record: compute paper_cache_key; if present in the S3 pdf cache, pull it;
else run the download waterfall (ArXiv direct -> Unpaywall -> S2 SDK -> doi2pdf ->
scihub -> PyPaperBot) and upload the result. Sets citingPaper.downloadedPath
(relative to the run dir) and writes all_citations_with_downloads.json.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from .s3_cache import S3Cache, paper_cache_key
from .s2_downloader import download_arxiv_paper, download_by_doi


def _download_one(cp: dict, downloads_dir: Path, doi_email: str) -> tuple[str | None, str | None]:
    """Return (abs_path, source) on success, else (None, None)."""
    ext = cp.get("externalIds") or {}
    arxiv = ext.get("ArXiv") or ext.get("arXiv")
    doi = ext.get("DOI") or ext.get("doi")
    key = paper_cache_key(ext)
    if not key:
        return None, None

    save_path = downloads_dir / f"{key}.pdf"

    if arxiv and download_arxiv_paper(str(arxiv), str(save_path)):
        return str(save_path), "arxiv"

    if doi:
        res = download_by_doi(str(doi), str(save_path), str(downloads_dir), doi_email)
        if res.get("status") == "success":
            return res.get("path"), res.get("source")

    return None, None


# ── stateless MCP-facing variant (records in / records out; PDFs go to S3) ────────


def download_pdfs_records(
    records: list[dict],
    cache: S3Cache,
    doi_email: str = "user@example.com",
    request_delay: float = 1.0,
    force: bool = False,
    limit: int | None = None,
) -> dict:
    """Download citing-paper PDFs into the S3 pdf cache. Stateless: records in, out.

    PDFs are binary and cannot ride in a workflow variable, so they are NOT returned;
    each PDF is content-addressed in the S3 pdf cache by ``paper_cache_key`` (derived
    from ArXiv id or DOI). Each record is annotated in place with:

      - ``pdf_cache_key``: the S3 cache key for the PDF (None if unavailable)
      - ``pdf_status``: "cached" | "downloaded" | "failed" | "no_id"
      - ``pdf_source``: which source produced a fresh download (arxiv/unpaywall/...)

    ``get_pdf_text`` later re-materializes each PDF from the cache by key. Requires
    S3 (``cache.enabled``). Set ``force=True`` to re-download even if cached; ``limit``
    caps how many records are processed (testing).

    Output: {status, record_count, downloaded, from_cache, failed, no_id, records}.
    """
    if not isinstance(records, list):
        return {"status": "error", "message": "records must be a list",
                "record_count": 0, "downloaded": 0, "from_cache": 0,
                "failed": 0, "no_id": 0, "records": []}
    if not cache.enabled:
        return {"status": "error", "message": "S3 is required for download_pdfs",
                "record_count": len(records), "downloaded": 0, "from_cache": 0,
                "failed": 0, "no_id": 0, "records": records}

    downloaded = 0
    from_cache = 0
    failed = 0
    no_id = 0
    processed = 0

    with tempfile.TemporaryDirectory(prefix="cra_dl_") as tmp:
        tmp_dir = Path(tmp)
        for rec in records:
            cp = rec.setdefault("citingPaper", {})
            ext = cp.get("externalIds") or {}
            key = paper_cache_key(ext)

            if not key:
                cp["pdf_cache_key"] = None
                cp["pdf_status"] = "no_id"
                no_id += 1
                continue

            if limit is not None and processed >= limit:
                # leave unprocessed records unannotated; they can be resumed later
                continue
            processed += 1

            # S3 pdf cache first (unless force)
            if not force and cache.pdf_cache_exists(key):
                cp["pdf_cache_key"] = key
                cp["pdf_status"] = "cached"
                from_cache += 1
                continue

            # fresh download into the temp dir, then push to S3
            abs_path, source = _download_one(cp, tmp_dir, doi_email)
            if abs_path:
                final = tmp_dir / f"{key}.pdf"
                if Path(abs_path) != final:
                    try:
                        Path(abs_path).rename(final)
                    except Exception:
                        final = Path(abs_path)
                cache.pdf_cache_put(key, final, source=source or "")
                cp["pdf_cache_key"] = key
                cp["pdf_status"] = "downloaded"
                cp["pdf_source"] = source
                downloaded += 1
                try:
                    final.unlink()  # bound temp-dir size during a long run
                except Exception:
                    pass
                time.sleep(request_delay)
            else:
                cp["pdf_cache_key"] = None
                cp["pdf_status"] = "failed"
                failed += 1

    return {
        "status": "ok",
        "record_count": len(records),
        "downloaded": downloaded,
        "from_cache": from_cache,
        "failed": failed,
        "no_id": no_id,
        "records": records,
        "message": None,
    }
