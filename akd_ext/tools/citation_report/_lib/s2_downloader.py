"""Resolve external ids and download PDFs for Semantic Scholar citation records.

Vendored from s2_paper_downloader/{s2_client,downloader,waterfall}.py and merged
into one module. Behavior preserved:
  - external-id lookup with 429 back-off
  - download waterfall: ArXiv direct -> Unpaywall -> S2 SDK -> doi2pdf -> scihub -> PyPaperBot

The high-level download here is per-record and pure (no in-place list mutation);
the tools layer handles iteration, the S3 pdf cache, and writing JSON.
"""

from __future__ import annotations

import os
import re
import subprocess
import time

import requests

_SESSION = requests.Session()


# ── external ids ───────────────────────────────────────────────────────────────

def extract_paper_id(url: str) -> str | None:
    match = re.search(r"/paper/(?:[^/]+/)?([a-f0-9]{40}|[A-Za-z0-9_-]+)", url or "")
    return match.group(1) if match else None


def get_external_ids(paper_url: str, api_key: str | None = None, max_retries: int = 6) -> dict:
    """Fetch externalIds for a paper URL, retrying on HTTP 429."""
    paper_id = extract_paper_id(paper_url)
    if not paper_id:
        return {}
    headers = {"x-api-key": api_key} if api_key else {}
    api_uri = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}?fields=externalIds"
    for attempt in range(max_retries + 1):
        try:
            resp = _SESSION.get(api_uri, headers=headers, timeout=30)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After", "")
                wait_s = int(retry_after) if retry_after.isdigit() else min(60, 2 ** attempt)
                if attempt < max_retries:
                    time.sleep(wait_s)
                    continue
            resp.raise_for_status()
            return resp.json().get("externalIds", {}) or {}
        except requests.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", "unknown")
            if status == 429 and attempt < max_retries:
                time.sleep(min(60, 2 ** attempt))
                continue
            return {}
    return {}


# ── pdf download ────────────────────────────────────────────────────────────────

def _clean_filename(doi: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", doi)[:200]


def _download_pdf(url: str, save_path: str) -> bool:
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    try:
        resp = requests.get(url, headers=headers, stream=True, timeout=30)
        content_type = resp.headers.get("content-type", "").lower()
        if resp.status_code == 200 and ("pdf" in content_type or resp.content[:4] == b"%PDF"):
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        return False
    except Exception:
        return False


def download_arxiv_paper(arxiv_id: str, save_path: str) -> bool:
    url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    try:
        resp = _SESSION.get(url, timeout=30, stream=True)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except requests.RequestException:
        return False


def download_by_doi(doi: str, save_path: str, download_dir: str, email: str) -> dict:
    """Waterfall DOI download. Returns {status, source, path, errors}."""
    errors: list[str] = []

    # 1. Unpaywall
    try:
        r = requests.get(f"https://api.unpaywall.org/v2/{doi}?email={email}", timeout=15)
        if r.status_code == 200:
            pdf_url = (r.json().get("best_oa_location") or {}).get("url_for_pdf")
            if pdf_url and _download_pdf(pdf_url, save_path):
                return {"status": "success", "source": "unpaywall", "path": save_path, "errors": errors}
            errors.append("unpaywall: no OA pdf")
        else:
            errors.append(f"unpaywall: HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"unpaywall: {e}")

    # 2. Semantic Scholar SDK
    try:
        from semanticscholar import SemanticScholar
        paper = SemanticScholar().get_paper(f"DOI:{doi}")
        if paper and paper.openAccessPdf:
            pdf_url = paper.openAccessPdf.get("url")
            if pdf_url and _download_pdf(pdf_url, save_path):
                return {"status": "success", "source": "semantic_scholar", "path": save_path, "errors": errors}
            errors.append("semantic_scholar: no OA pdf")
        else:
            errors.append("semantic_scholar: not found / no OA pdf")
    except Exception as e:
        errors.append(f"semantic_scholar: {e}")

    # 3. doi2pdf
    try:
        import doi2pdf
        doi2pdf.doi2pdf(doi, output=save_path)
        if os.path.exists(save_path) and os.path.getsize(save_path) > 1000:
            with open(save_path, "rb") as f:
                if f.read(4) == b"%PDF":
                    return {"status": "success", "source": "doi2pdf", "path": save_path, "errors": errors}
            errors.append("doi2pdf: not a valid pdf")
            os.remove(save_path)
        else:
            errors.append("doi2pdf: missing/too small")
    except Exception as e:
        errors.append(f"doi2pdf: {e}")

    # 4. scihub CLI
    try:
        subprocess.run(["scihub", "-s", doi, "-O", download_dir],
                       capture_output=True, text=True, timeout=60)
        doi_suffix = doi.split("/")[-1] if "/" in doi else doi
        for fname in os.listdir(download_dir):
            if (_clean_filename(doi) in fname or doi_suffix in fname) and fname.endswith(".pdf"):
                found = os.path.join(download_dir, fname)
                if os.path.getsize(found) > 1000:
                    with open(found, "rb") as f:
                        if f.read(4) == b"%PDF":
                            return {"status": "success", "source": "scihub_cli", "path": found, "errors": errors}
                    os.remove(found)
        errors.append("scihub_cli: no matching pdf")
    except Exception as e:
        errors.append(f"scihub_cli: {e}")

    # 5. PyPaperBot
    try:
        subprocess.run(["python", "-m", "PyPaperBot", f"--doi={doi}", f"--dwn-dir={download_dir}"],
                       capture_output=True, text=True)
        for fname in os.listdir(download_dir):
            if _clean_filename(doi) in fname and fname.endswith(".pdf"):
                found = os.path.join(download_dir, fname)
                with open(found, "rb") as f:
                    if f.read(4) == b"%PDF":
                        return {"status": "success", "source": "pypaperbot", "path": found, "errors": errors}
                os.remove(found)
        errors.append("pypaperbot: no matching pdf")
    except Exception as e:
        errors.append(f"pypaperbot: {e}")

    return {"status": "failed", "source": None, "path": None, "errors": errors}
