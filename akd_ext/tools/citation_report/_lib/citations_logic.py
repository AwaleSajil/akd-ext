"""Step 1: get citing papers, with an S3 citation cache (completeness-aware).

get_citations_records — pull from cache OR fetch fresh from S2 (returns records inline).
The cache status (peek) is folded in via the ``peek_only`` flag.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from .s3_cache import S3Cache
from .s2_fetch import fetch_citations as _fetch_citations


def _age_days(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        return round((datetime.now(timezone.utc) - ts).total_seconds() / 86400, 2)
    except Exception:
        return None


def _is_complete(meta: dict | None) -> bool:
    """A cache entry is complete when it was fetched with no paper_limit (0)."""
    if not meta:
        return False
    # Default to complete for legacy entries that predate the paper_limit field.
    return int(meta.get("paper_limit", 0) or 0) == 0


def _cache_satisfies(meta: dict | None, requested_limit: int) -> bool:
    """Does the cached list satisfy a request for ``requested_limit`` (0 = all)?

    - A complete cache satisfies any request (slice as needed).
    - A partial cache of C records satisfies only a bounded request L where C >= L.
    - Nothing partial can satisfy an unlimited request (L == 0).
    """
    if not meta:
        return False
    if _is_complete(meta):
        return True
    if requested_limit <= 0:
        return False
    return int(meta.get("record_count", 0) or 0) >= requested_limit


def _should_write(existing_meta: dict | None, new_count: int, new_complete: bool) -> bool:
    """No-downgrade write policy: only replace the cache with a more-complete entry.

    - No existing entry -> write.
    - New complete -> write (refresh/upgrade).
    - New partial -> write only if existing is also partial AND has more records.
      Never clobber a complete cache with a partial one.
    """
    if not existing_meta:
        return True
    if new_complete:
        return True
    if _is_complete(existing_meta):
        return False
    return new_count > int(existing_meta.get("record_count", 0) or 0)


def _cache_status_dict(
    paper_id: str,
    cache: S3Cache,
    citation_count: int | None,
    min_interval_s: float,
) -> dict:
    """Cache peek as a plain dict (folds check_citation_cache into get_citations)."""
    est = None
    if citation_count:
        est = int(math.ceil(citation_count / 100) * min_interval_s) + 2

    meta = cache.citation_cache_meta(paper_id) if cache.enabled else None
    if not meta:
        return {
            "cached": False, "complete": False, "fetched_at": None, "age_days": None,
            "record_count": None, "estimated_fresh_seconds": est,
        }
    return {
        "cached": True,
        "complete": _is_complete(meta),
        "fetched_at": meta.get("fetched_at"),
        "age_days": _age_days(meta.get("fetched_at")),
        "record_count": meta.get("record_count"),
        "estimated_fresh_seconds": est,
    }


def get_citations_records(
    paper_id: str,
    cache: S3Cache,
    api_key: str | None = None,
    use_cache: bool = True,
    paper_limit: int = 0,
    min_interval_s: float = 2.0,
    citation_count: int | None = None,
    peek_only: bool = False,
) -> dict:
    """Stateless citation fetch for MCP. Returns the records inline (dict out).

    - ``peek_only=True``: only report the S3 cache status (no fetch, no records).
      Use this to warn the user before a slow fresh fetch.
    - ``use_cache=True``: serve the S3 cached list if present, else fetch fresh and
      cache it.
    - ``use_cache=False``: always fetch fresh from Semantic Scholar and refresh cache.
    """
    if not paper_id:
        return {"status": "error", "message": "paper_id is required",
                "from_cache": False, "record_count": 0, "fetched_at": None,
                "cache": {}, "records": []}

    cache_info = _cache_status_dict(paper_id, cache, citation_count, min_interval_s)

    def _citations_uri() -> str | None:
        # URI of the cached citation list in S3 (for get_download_link). Only meaningful
        # when an object actually exists in the cache.
        if cache.enabled and cache_info.get("cached"):
            return cache.citation_cache_uri(paper_id)
        return None

    if peek_only:
        return {
            "status": "ok", "peek": True, "from_cache": False, "served": "peek",
            "record_count": cache_info.get("record_count"),
            "fetched_at": cache_info.get("fetched_at"),
            "citations_s3_uri": _citations_uri(),
            "cache": cache_info, "records": [], "message": None,
        }

    records = None
    from_cache = False
    served = "fresh"
    fetched_at = cache_info.get("fetched_at")
    existing_meta = cache.citation_cache_meta(paper_id) if cache.enabled else None

    # Serve from cache only if it actually satisfies the requested limit.
    if use_cache and cache.enabled and _cache_satisfies(existing_meta, paper_limit):
        cached = cache.citation_cache_get(paper_id)
        if cached is not None:
            records = cached[:paper_limit] if paper_limit > 0 else cached
            from_cache = True
            served = "cache_sliced" if (paper_limit > 0 and len(cached) > len(records)) else "cache"

    # Otherwise fetch fresh (cache miss, partial-can't-satisfy, or use_cache=False).
    if records is None:
        try:
            fresh = _fetch_citations(
                paper_id, api_key=api_key, paper_limit=paper_limit,
                min_interval_s=min_interval_s,
            )
        except Exception as e:
            return {"status": "error", "message": f"{type(e).__name__}: {e}",
                    "from_cache": False, "served": "error", "record_count": 0,
                    "fetched_at": None, "cache": cache_info, "records": []}

        new_complete = paper_limit <= 0
        if _should_write(existing_meta, len(fresh), new_complete):
            cache.citation_cache_put(paper_id, fresh, {"paper_limit": paper_limit})
        records = fresh
        served = "fresh"
        cache_info = _cache_status_dict(paper_id, cache, citation_count, min_interval_s)
        fetched_at = cache_info.get("fetched_at")

    return {
        "status": "ok", "peek": False, "from_cache": from_cache, "served": served,
        "record_count": len(records), "fetched_at": fetched_at,
        "citations_s3_uri": _citations_uri(),
        "cache": cache_info, "records": records, "message": None,
    }
