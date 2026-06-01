"""Step 1b: resolve a user's paper reference to a canonical S2 paperId.

Accepts a Semantic Scholar URL, arXiv id/URL, DOI, or free-text title and returns
the canonical 40-char paperId (the stable handle used for caching + citations).

Promoted from fetch_citation/test.ipynb (normalize_to_s2_id / resolve_paper_id).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse

from .s2_fetch import API, _http_get_json

PAPER_FIELDS = "title,abstract,year,externalIds,url,citationCount"


def normalize_to_s2_id(text: str) -> str | None:
    """URL / bare id -> something /paper/{id} accepts; None => treat as a title."""
    t = (text or "").strip()
    m = re.search(r"semanticscholar\.org/paper/(?:[^/]+/)?([a-f0-9]{40})", t)
    if m:
        return m.group(1)
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})", t)
    if m:
        return f"ARXIV:{m.group(1)}"
    if re.fullmatch(r"[0-9]{4}\.[0-9]{4,5}", t):
        return f"ARXIV:{t}"
    m = re.search(r"(?:doi\.org/|^)(10\.\d{4,9}/\S+)", t)
    if m:
        return f"DOI:{m.group(1)}"
    if re.fullmatch(r"[a-f0-9]{40}", t):
        return t
    return None


# ── pure MCP-facing resolver (dict in / dict out) ────────────────────────────────


def _paper_shape(raw: dict) -> dict:
    """Normalize a raw Semantic Scholar paper record into our flat shape."""
    return {
        "paper_id": raw.get("paperId", ""),
        "title": raw.get("title") or "",
        "abstract": raw.get("abstract") or "",
        "year": raw.get("year"),
        "url": raw.get("url") or "",
        "external_ids": raw.get("externalIds") or {},
        "citation_count": raw.get("citationCount"),
    }


def _get_json_safe(url: str, api_key: str | None) -> tuple[dict | None, int | None, str | None]:
    """GET JSON, returning (data, http_status, error_message). Never raises."""
    try:
        return _http_get_json(url, api_key), 200, None
    except urllib.error.HTTPError as e:
        return None, e.code, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return None, None, f"network error: {e.reason}"
    except Exception as e:  # malformed JSON, etc.
        return None, None, f"{type(e).__name__}: {e}"


def resolve_paper(user_input: str, api_key: str | None, limit: int = 5) -> dict:
    """Resolve a paper reference to a canonical Semantic Scholar paper.

    Pure function (dict in / dict out) for use as an MCP tool: no filesystem, no S3,
    no shared state. ``api_key`` is supplied by the caller from the environment.

    Returns a dict shaped as::

        {
          "status": "ok" | "ambiguous" | "not_found" | "error",
          "input_kind": "id/url" | "title",
          "paper": {paper_id, title, year, url, external_ids, citation_count} | None,
          "candidates": [ {same fields}, ... ],   # only when status == "ambiguous"
          "message": str | None,                  # only when not_found / error
        }
    """
    text = (user_input or "").strip()
    if not text:
        return {"status": "error", "input_kind": None, "paper": None,
                "candidates": [], "message": "empty input"}

    fields_q = urllib.parse.quote(PAPER_FIELDS, safe=",")
    norm = normalize_to_s2_id(text)

    # ── id / url path ────────────────────────────────────────────────────────────
    if norm:
        data, status, err = _get_json_safe(
            f"{API}/paper/{urllib.parse.quote(norm)}?fields={fields_q}", api_key
        )
        if data and data.get("paperId"):
            return {"status": "ok", "input_kind": "id/url",
                    "paper": _paper_shape(data), "candidates": [], "message": None}
        if status == 404:
            return {"status": "not_found", "input_kind": "id/url", "paper": None,
                    "candidates": [], "message": f"no paper for '{norm}'"}
        return {"status": "error", "input_kind": "id/url", "paper": None,
                "candidates": [], "message": err or "lookup failed"}

    # ── title path: confident match first ────────────────────────────────────────
    q = urllib.parse.quote(text)
    data, status, err = _get_json_safe(
        f"{API}/paper/search/match?query={q}&fields={fields_q}", api_key
    )
    rows = (data or {}).get("data") or []
    if rows and rows[0].get("paperId"):
        return {"status": "ok", "input_kind": "title",
                "paper": _paper_shape(rows[0]), "candidates": [], "message": None}

    # match endpoint returned nothing confident (404 / {"error": ...}) → candidates
    n = max(1, min(int(limit), 20))
    cdata, cstatus, cerr = _get_json_safe(
        f"{API}/paper/search?query={q}&limit={n}&fields={fields_q}", api_key
    )
    candidates = [_paper_shape(r) for r in ((cdata or {}).get("data") or []) if r.get("paperId")]
    if candidates:
        return {"status": "ambiguous", "input_kind": "title", "paper": None,
                "candidates": candidates,
                "message": "no single confident match; choose from candidates"}
    if cstatus and cstatus >= 500 or cerr:
        return {"status": "error", "input_kind": "title", "paper": None,
                "candidates": [], "message": cerr or "search failed"}
    return {"status": "not_found", "input_kind": "title", "paper": None,
            "candidates": [], "message": f"no papers found for '{text}'"}
