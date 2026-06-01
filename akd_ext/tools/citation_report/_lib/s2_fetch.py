"""Fetch citing papers from Semantic Scholar (Graph API).

Vendored and lightly refactored from
fetch_citation/fetch_semantic_scholar_citations.py. The CLI/file-writing wrapper is
dropped; this module exposes pure functions that return Python objects so the tools
layer decides where things get written/cached.

Stdlib only (urllib).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.semanticscholar.org/graph/v1"

FIELDS_PAPER = ",".join(
    ["paperId", "title", "year", "venue", "url", "authors", "openAccessPdf",
     "externalIds", "citationCount"]
)

FIELDS_CITATION = ",".join(
    ["citingPaper.paperId", "citingPaper.title", "citingPaper.year",
     "citingPaper.venue", "citingPaper.url", "citingPaper.authors",
     "citingPaper.openAccessPdf", "citingPaper.externalIds"]
)


def _http_get_json(url: str, api_key: str | None, timeout_s: int = 60) -> dict:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "citation-report-agent/1.0")
    if api_key:
        req.add_header("x-api-key", api_key)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


def _throttle(last_ts: float | None, min_interval_s: float) -> float:
    now = time.time()
    if last_ts is not None:
        sleep_s = (last_ts + min_interval_s) - now
        if sleep_s > 0:
            time.sleep(sleep_s)
    return time.time()


def _sleep_backoff(attempt: int) -> None:
    time.sleep(min(30, 2 ** (attempt + 1)))


def get_paper_meta(paper_id: str, api_key: str | None, min_interval_s: float = 2.0) -> dict:
    """Fetch seed-paper metadata (title, year, externalIds, citationCount, ...)."""
    url = (
        f"{API}/paper/" + urllib.parse.quote(paper_id)
        + "?fields=" + urllib.parse.quote(FIELDS_PAPER, safe=",")
    )
    last = None
    for attempt in range(6):
        try:
            last = _throttle(last, min_interval_s)
            return _http_get_json(url, api_key)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _sleep_backoff(attempt)
                continue
            raise
    raise RuntimeError(f"Rate limited fetching paper metadata for {paper_id}")


def fetch_citations(
    paper_id: str,
    api_key: str | None,
    paper_limit: int = 0,
    min_interval_s: float = 2.0,
    progress=None,
) -> list[dict]:
    """Return a flat list of ``{"citingPaper": {...}}`` records for ``paper_id``.

    Each citingPaper carries externalIds + openAccessPdf, so the separate enrich
    step is mostly validation. ``paper_limit`` is honored exactly (the list is
    trimmed after the page that crosses the cap), fixing the page-granular behavior
    of the original script. ``paper_limit=0`` means no limit.
    """
    citations: list[dict] = []
    offset = 0
    limit = 100

    while True:
        url = (
            f"{API}/paper/" + urllib.parse.quote(paper_id)
            + "/citations?fields=" + urllib.parse.quote(FIELDS_CITATION, safe=",")
            + f"&limit={limit}&offset={offset}"
        )
        payload = None
        last = None
        for attempt in range(6):
            try:
                last = _throttle(last, min_interval_s)
                payload = _http_get_json(url, api_key)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    _sleep_backoff(attempt)
                    continue
                raise
        if payload is None:
            break

        page_items = payload.get("data") or []
        for item in page_items:
            cp = (item or {}).get("citingPaper")
            if cp:
                citations.append({"citingPaper": cp})

        if progress:
            progress(len(citations))

        if len(page_items) < limit:
            break
        if paper_limit > 0 and len(citations) >= paper_limit:
            break
        offset += limit

    if paper_limit > 0:
        citations = citations[:paper_limit]
    return citations
