"""Pure-IO consolidation (NO LLM).

Reads the per-paper reports the agent saved in S3 and merges them into one master
JSON document, written back to S3 at reports/{target}/{version}/_master.json.

Two selection modes:
  - pass ``cache_keys`` -> consolidate exactly those reports.
  - omit it          -> consolidate every report under reports/{target}/{version}/.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from .s3_cache import S3Cache


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _paper_entry(cache_key: str, doc: dict, include_full: bool) -> dict:
    """Build one master entry for a saved report.

    Always includes a flat summary (for quick scanning / spreadsheets). When
    ``include_full`` is true it also embeds the COMPLETE agent output for the paper —
    the full ``report``, ``evidence``, and ``_meta`` — so the master is self-contained
    with all information for every paper. Defensive about shape: the agent owns it.
    """
    meta = doc.get("_meta") or {}
    report = doc.get("report") or {}
    metrics = report.get("metrics") or {}
    rs = metrics.get("results_signal") or {}
    eq = metrics.get("evidence_quality") or {}
    tasks = metrics.get("downstream_tasks") or []

    entry = {
        "cache_key": cache_key,
        "paper_id": meta.get("paper_id", ""),
        "title": meta.get("title", "") or report.get("title", ""),
        "year": meta.get("year"),
        "url": meta.get("url", ""),
        "usage_type": report.get("usage_type", "unclear"),
        "confidence": eq.get("confidence"),
        "reported_improvement": rs.get("reported_improvement"),
        "n_evidence": len(doc.get("evidence") or []),
        "downstream_tasks": [t.get("task", "") for t in tasks if isinstance(t, dict) and t.get("task")],
        "saved_at": meta.get("saved_at"),
    }
    if include_full:
        entry["meta"] = meta
        entry["report"] = report
        entry["evidence"] = doc.get("evidence") or []
    return entry


def consolidate_reports_io(
    cache: S3Cache,
    seed_paper_id: str,
    version: str = "v1",
    cache_keys: list[str] | None = None,
    include_full: bool = True,
) -> dict:
    """Merge per-paper reports from S3 into one master JSON (and write it back to S3).

    Reports are namespaced by the SEED paper at reports/{seed_paper_id}/{version}/.

    - ``cache_keys=None`` -> consolidate ALL reports under that prefix.
    - ``cache_keys=[...]`` -> consolidate just those (missing ones are reported).
    - ``include_full=True`` (default) -> each ``papers[]`` entry carries the COMPLETE
      agent output (summary fields + full ``report`` + ``evidence`` + ``meta``), so the
      master is self-contained with all information for every paper.
    - ``include_full=False`` -> summary fields only (smaller, scan-friendly).

    Output: {status, seed_paper_id, version, total_papers, found, missing,
    counts_by_usage_type, master_s3_uri, master}.
    """
    if not cache.enabled:
        return {"status": "error", "message": "S3 is required for consolidate",
                "total_papers": 0, "found": 0, "missing": [], "master": None}
    if not seed_paper_id:
        return {"status": "no_seed", "message": "seed_paper_id is required",
                "total_papers": 0, "found": 0, "missing": [], "master": None}

    if cache_keys is None:
        keys = cache.agent_report_list_keys(seed_paper_id, version)
        requested_all = True
    else:
        keys = [k for k in cache_keys if k]
        requested_all = False

    papers = []
    missing = []
    for k in keys:
        doc = cache.agent_report_get(seed_paper_id, version, k)
        if doc is None:
            missing.append(k)
            continue
        papers.append(_paper_entry(k, doc, include_full))

    counts = Counter(p["usage_type"] for p in papers)

    master = {
        "seed_paper_id": seed_paper_id,
        "version": version,
        "generated_at": _utc_now_iso(),
        "selection": "all" if requested_all else "explicit",
        "includes_full_reports": include_full,
        "total_papers": len(papers),
        "counts_by_usage_type": dict(counts),
        "papers": papers,
    }

    uri = cache.master_report_put_v2(seed_paper_id, version, master)

    return {
        "status": "ok",
        "seed_paper_id": seed_paper_id,
        "version": version,
        "total_papers": len(papers),
        "found": len(papers),
        "missing": missing,
        "counts_by_usage_type": dict(counts),
        "master_s3_uri": uri,
        "master": master,
        "message": None,
    }
