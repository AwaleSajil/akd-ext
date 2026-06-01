"""The ONE LLM-bearing step: analyze many citing papers in parallel, then consolidate.

For each citing paper (bounded by ``n_parallel`` concurrent LLM calls):
    skip if a report already exists   (get_report)
    download the PDF to the S3 cache  (download_pdfs_records)
    extract its text                  (get_pdf_text)
    LLM: analyze usage of the target  (AsyncOpenAI.responses.parse -> AgentOutput)
    save the report to S3             (save_report)
then once:
    merge all reports into a master   (consolidate_reports_io)
    presign the master (+ citations)  (get_download_link)

The LLM runs here via the OpenAI API directly (server's OPENAI_API_KEY) — not the
Agents SDK and not MCP sampling. Blocking sub-steps (S3 / network / PDF parsing) run in
worker threads via asyncio.to_thread so the semaphore buys real concurrency instead of
serializing the event loop behind a blocking call.
"""

from __future__ import annotations

import asyncio
from collections import Counter

from openai import AsyncOpenAI

from .consolidate_logic import consolidate_reports_io
from .download_logic import download_pdfs_records
from .env import analyzer_model, openai_api_key, unpaywall_email
from .paper_logic import get_pdf_text, get_report, save_report
from .prompts import build_analyzer_input
from .s3_cache import S3Cache, paper_cache_key
from .schemas import AgentOutput, TargetSpec
from .share_logic import get_download_link

# statuses that mean "the report is in S3 and should be consolidated"
_DONE = ("ok", "from_cache")


def _target_spec(target: dict) -> TargetSpec:
    """Build a TargetSpec defensively from a loosely-shaped dict."""
    t = target or {}
    return TargetSpec(
        name=t.get("name", ""),
        aliases=t.get("aliases") or [],
        domain=t.get("domain", ""),
        what_to_look_for=t.get("what_to_look_for", ""),
    )


async def _analyze_one(
    rec: dict,
    *,
    oai: AsyncOpenAI,
    cache: S3Cache,
    doi_email: str,
    spec: TargetSpec,
    seed_paper_id: str,
    version: str,
    model: str,
    max_chars: int,
    use_report_cache: bool,
    sem: asyncio.Semaphore,
) -> dict:
    """download -> get_text -> LLM analyze -> save, for one citing paper.

    Returns {cache_key, paper_id, status[, detail]}. status is one of:
    ok | from_cache | no_id | no_pdf | failed | no_text | analyze_failed | save_failed.
    """
    cp = rec.get("citingPaper") or {}
    ext = cp.get("externalIds") or {}
    paper_id = cp.get("paperId", "")
    key = paper_cache_key(ext)
    if not key:
        return {"cache_key": None, "paper_id": paper_id, "status": "no_id"}

    async with sem:
        # 1. cache-first: skip papers already analyzed under this seed/version
        if use_report_cache:
            existing = await asyncio.to_thread(get_report, key, cache, seed_paper_id, version)
            if existing.get("exists"):
                return {"cache_key": key, "paper_id": paper_id, "status": "from_cache"}

        # 2. download the PDF into the S3 pdf cache (cache-first internally)
        await asyncio.to_thread(download_pdfs_records, [rec], cache, doi_email)
        pdf_key = cp.get("pdf_cache_key")
        if not pdf_key:
            return {"cache_key": None, "paper_id": paper_id,
                    "status": cp.get("pdf_status") or "no_pdf"}

        # 3. extract page-marked text
        tx = await asyncio.to_thread(get_pdf_text, pdf_key, cache, max_chars)
        if tx.get("status") != "ok":
            return {"cache_key": pdf_key, "paper_id": paper_id,
                    "status": "no_text", "detail": tx.get("status")}

        # 4. THE LLM CALL — structured output constrained to AgentOutput
        try:
            resp = await oai.responses.parse(
                model=model,
                input=build_analyzer_input(spec, tx["text"]),
                text_format=AgentOutput,
            )
            parsed = resp.output_parsed
            if parsed is None:
                return {"cache_key": pdf_key, "paper_id": paper_id,
                        "status": "analyze_failed", "detail": "no_parsed_output"}
            report = parsed.model_dump()
        except Exception as e:  # noqa: BLE001 — surface per-paper, never abort the batch
            return {"cache_key": pdf_key, "paper_id": paper_id,
                    "status": "analyze_failed", "detail": f"{type(e).__name__}: {e}"[:160]}

        # 5. persist the report to S3
        sv = await asyncio.to_thread(
            save_report, pdf_key, report, cache, seed_paper_id, version, paper_id,
        )
        return {"cache_key": pdf_key, "paper_id": paper_id,
                "status": "ok" if sv.get("status") == "ok" else "save_failed"}


async def analyze_citations_batch(
    cache: S3Cache,
    citations: list[dict],
    seed_paper_id: str,
    target: dict,
    *,
    version: str = "v1",
    n_parallel: int = 6,
    model: str = "",
    max_chars: int = 90000,
    use_report_cache: bool = True,
    citations_s3_uri: str = "",
    paper_limit: int = 0,
) -> dict:
    """Analyze every citing paper in parallel, consolidate, and presign the outputs.

    Args:
      cache: an S3Cache (from env.get_cache()).
      citations: the citation records (the ``records`` list from get_citations).
      seed_paper_id: the SEED paper id (reports are namespaced by it).
      target: {name, aliases, domain, what_to_look_for} from the Target Profiler.
      n_parallel: bound on concurrent papers (== concurrent LLM calls).
      model: analyzer model; defaults to env ANALYZER_MODEL / "gpt-5.5".
      use_report_cache: skip papers that already have a saved report (resumable).
      citations_s3_uri: optional S3 URI of the citation JSON, to also return its link.
      paper_limit: cap papers processed (0 = all; use to chunk a huge list).

    Returns: {status, seed_paper_id, summary, total_papers, counts_by_usage_type,
      status_counts, report_download_url, citations_download_url, master_s3_uri,
      results, missing, message}.
    """
    if not cache.enabled:
        return {"status": "error", "message": "S3 is required for analyze_citations"}
    api_key = openai_api_key()
    if not api_key:
        return {"status": "error", "message": "OPENAI_API_KEY is not set on the server"}
    if not seed_paper_id:
        return {"status": "error", "message": "seed_paper_id is required"}
    if not isinstance(citations, list) or not citations:
        return {"status": "error", "message": "citations must be a non-empty list of records"}

    records = citations[:paper_limit] if paper_limit and paper_limit > 0 else citations
    spec = _target_spec(target)
    model = model or analyzer_model()
    doi_email = unpaywall_email()
    oai = AsyncOpenAI(api_key=api_key)
    sem = asyncio.Semaphore(max(1, n_parallel))

    # ── parallel per-paper analysis ──────────────────────────────────────────────
    results = await asyncio.gather(*[
        _analyze_one(
            rec, oai=oai, cache=cache, doi_email=doi_email, spec=spec,
            seed_paper_id=seed_paper_id, version=version, model=model,
            max_chars=max_chars, use_report_cache=use_report_cache, sem=sem,
        )
        for rec in records
    ])

    status_counts = dict(Counter(r["status"] for r in results))
    saved_keys = [r["cache_key"] for r in results if r["status"] in _DONE and r.get("cache_key")]

    # ── consolidate the saved reports into one master JSON ───────────────────────
    co = await asyncio.to_thread(
        consolidate_reports_io, cache, seed_paper_id, version, saved_keys, True,
    )

    # ── presign download links ───────────────────────────────────────────────────
    master_uri = co.get("master_s3_uri")
    report_link = await asyncio.to_thread(get_download_link, cache, master_uri) if master_uri else {}
    citations_link = (
        await asyncio.to_thread(get_download_link, cache, citations_s3_uri)
        if citations_s3_uri else {}
    )

    counts = co.get("counts_by_usage_type", {})
    total = co.get("total_papers", 0)
    summary = (
        f"Analyzed {total} of {len(records)} citing papers for '{spec.name}'. "
        + (", ".join(f"{k}: {v}" for k, v in counts.items()) if counts else "no usage classified.")
    )

    return {
        "status": "ok",
        "seed_paper_id": seed_paper_id,
        "summary": summary,
        "total_papers": total,
        "counts_by_usage_type": counts,
        "status_counts": status_counts,
        "report_download_url": report_link.get("url"),
        "citations_download_url": citations_link.get("url"),
        "master_s3_uri": master_uri,
        "missing": co.get("missing", []),
        "results": list(results),
        "message": None,
    }
