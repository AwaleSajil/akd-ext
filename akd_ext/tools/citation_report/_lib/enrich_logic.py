"""Step 2: validate / fill external ids on citation records.

The fetch step already requests externalIds, so this is mostly validation + gap
filling: records lacking both ArXiv and DOI get an extra S2 lookup from their url;
records still without a usable id (or without an OA pdf) are flagged.
"""

from __future__ import annotations

import time

from .s2_downloader import get_external_ids


def _has_usable_id(ext: dict) -> bool:
    return bool(ext.get("ArXiv") or ext.get("arXiv") or ext.get("DOI") or ext.get("doi"))


# ── stateless MCP-facing variant (records in / records out, no filesystem) ────────


def enrich_citations_records(
    records: list[dict],
    api_key: str | None = None,
    request_delay: float = 2.0,
) -> dict:
    """Validate/fill external ids on citation records. Stateless: records in, out.

    For each record it FIRST checks whether usable external ids (ArXiv/DOI) are
    already present — fetched alongside the citation list — and only calls Semantic
    Scholar for the records that are missing them. Records still without any usable id
    are flagged (undownloadable); those also lacking an open-access PDF are flagged as
    likely paywalled.

    Returns ``{status, enriched, filled, already_had, flagged_no_id, flagged_no_oa,
    record_count, records}`` where ``records`` is the same list with ``externalIds``
    filled in place.
    """
    if not isinstance(records, list):
        return {"status": "error", "message": "records must be a list",
                "enriched": 0, "filled": 0, "already_had": 0,
                "flagged_no_id": 0, "flagged_no_oa": 0,
                "record_count": 0, "records": []}

    filled = 0
    already_had = 0
    flagged_no_id = 0
    flagged_no_oa = 0

    for rec in records:
        cp = rec.setdefault("citingPaper", {})
        ext = cp.get("externalIds") or {}

        if _has_usable_id(ext):
            already_had += 1                      # short-circuit: no API call needed
        else:
            url = cp.get("url")
            if url:
                fetched = get_external_ids(url, api_key=api_key)
                if fetched:
                    ext = {**ext, **fetched}
                    cp["externalIds"] = ext
                    if _has_usable_id(ext):
                        filled += 1
                time.sleep(request_delay)

        has_id = _has_usable_id(ext)
        has_oa = bool((cp.get("openAccessPdf") or {}).get("url"))
        if not has_id:
            flagged_no_id += 1
            if not has_oa:
                flagged_no_oa += 1

    enriched = sum(
        1 for r in records
        if _has_usable_id((r.get("citingPaper") or {}).get("externalIds") or {})
    )
    return {
        "status": "ok",
        "record_count": len(records),
        "enriched": enriched,
        "already_had": already_had,
        "filled": filled,
        "flagged_no_id": flagged_no_id,
        "flagged_no_oa": flagged_no_oa,
        "records": records,
        "message": None,
    }
