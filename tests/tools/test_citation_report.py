"""Tests for the citation-report tools.

Unit tests (no network/S3) cover registration, schema wiring, and error paths.
Integration tests hit Semantic Scholar and S3; they skip cleanly when the required
env vars are absent, so a CI run without secrets still passes.
"""

import os

import pytest

from akd_ext.mcp.registry import MCPToolRegistry
from akd_ext.tools import (
    ResolvePaperTool,
    GetCitationsTool,
    EnrichCitationsTool,
    DownloadPdfsTool,
    GetSeedTextTool,
    GetPdfTextTool,
    SaveReportTool,
    GetReportTool,
    ConsolidateReportsTool,
    GetDownloadLinkTool,
)
from akd_ext.tools.citation_report.resolve import ResolvePaperInputSchema
from akd_ext.tools.citation_report.enrich import EnrichCitationsInputSchema
from akd_ext.tools.citation_report.pdf_text import GetPdfTextInputSchema

CITATION_TOOLS = [
    ResolvePaperTool,
    GetCitationsTool,
    EnrichCitationsTool,
    DownloadPdfsTool,
    GetSeedTextTool,
    GetPdfTextTool,
    SaveReportTool,
    GetReportTool,
    ConsolidateReportsTool,
    GetDownloadLinkTool,
]

_HAS_S3 = bool(os.getenv("FM_S3_BUCKET"))
_SEED_ARXIV = "2310.18660"  # Prithvi geospatial FM paper
_SEED_PAPER_ID = "e966bd21790a6c6d0a07b4917325767451c9e23e"

needs_s3 = pytest.mark.skipif(not _HAS_S3, reason="FM_S3_BUCKET not set; skipping S3 integration test")


# ── unit: registration + naming ──────────────────────────────────────────────────

@pytest.mark.unit
def test_all_tools_registered():
    """Importing akd_ext.tools registers all 10 citation-report tools."""
    registered = MCPToolRegistry().get_tools()
    for tool_cls in CITATION_TOOLS:
        assert tool_cls in registered, f"{tool_cls.__name__} not registered"


@pytest.mark.unit
def test_tool_names_are_snake_case():
    """Each tool derives a snake_case name from its class name."""
    expected = {
        ResolvePaperTool: "resolve_paper_tool",
        GetCitationsTool: "get_citations_tool",
        SaveReportTool: "save_report_tool",
        ConsolidateReportsTool: "consolidate_reports_tool",
        GetDownloadLinkTool: "get_download_link_tool",
    }
    for tool_cls, name in expected.items():
        assert tool_cls().name == name


@pytest.mark.unit
def test_tools_convert_to_mcp_functions():
    """Every tool exposes an MCP-compatible callable via as_function."""
    for tool_cls in CITATION_TOOLS:
        fn = tool_cls().as_function(mode="python")
        assert callable(fn)


# ── unit: input validation ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_resolve_requires_user_input():
    with pytest.raises(Exception):
        ResolvePaperInputSchema()  # missing required user_input


@pytest.mark.unit
def test_enrich_requires_records():
    with pytest.raises(Exception):
        EnrichCitationsInputSchema()  # missing required records


# ── unit: error paths that need no network/S3 ────────────────────────────────────

@pytest.mark.unit
async def test_resolve_empty_input_is_error():
    tool = ResolvePaperTool()
    result = await tool.arun(ResolvePaperInputSchema(user_input=""))
    assert result.status == "error"


@pytest.mark.unit
@pytest.mark.skipif(_HAS_S3, reason="only meaningful when S3 is NOT configured")
async def test_get_pdf_text_without_s3_errors():
    tool = GetPdfTextTool()
    result = await tool.arun(GetPdfTextInputSchema(pdf_cache_key="anything"))
    assert result.status == "error"


# ── integration: Semantic Scholar (no S3 needed) ─────────────────────────────────

@pytest.mark.integration
async def test_resolve_arxiv_id():
    tool = ResolvePaperTool()
    result = await tool.arun(ResolvePaperInputSchema(user_input=_SEED_ARXIV))
    assert result.status == "ok"
    assert result.paper_id == _SEED_PAPER_ID
    assert result.input_kind == "id/url"
    assert len(result.abstract) > 0


@pytest.mark.integration
async def test_resolve_bad_id_not_found():
    tool = ResolvePaperTool()
    result = await tool.arun(ResolvePaperInputSchema(user_input="9999.99999"))
    assert result.status in ("not_found", "error")


# ── integration: full S3-backed pipeline ─────────────────────────────────────────

@pytest.mark.integration
@needs_s3
async def test_full_pipeline_end_to_end():
    """resolve -> seed_text -> citations -> enrich -> download -> pdf_text ->
    save_report -> get_report -> consolidate -> download_link, then clean up."""
    from akd_ext.tools.citation_report._lib.env import get_cache
    from akd_ext.tools.citation_report.citations import GetCitationsInputSchema
    from akd_ext.tools.citation_report.enrich import EnrichCitationsInputSchema as EnrichIn
    from akd_ext.tools.citation_report.download import DownloadPdfsInputSchema
    from akd_ext.tools.citation_report.seed_text import GetSeedTextInputSchema
    from akd_ext.tools.citation_report.reports import (
        SaveReportInputSchema, GetReportInputSchema, ConsolidateReportsInputSchema,
    )
    from akd_ext.tools.citation_report.download_link import GetDownloadLinkInputSchema

    cache = get_cache()
    if not cache.check_credentials()["ok"]:
        pytest.skip("S3 credentials missing/expired")

    version = "pytest"

    # 1. resolve
    r = await ResolvePaperTool().arun(ResolvePaperInputSchema(user_input=_SEED_ARXIV))
    assert r.status == "ok"
    pid = r.paper_id

    # 2. seed text
    st = await GetSeedTextTool().arun(GetSeedTextInputSchema(
        external_ids=r.external_ids, title=r.title, abstract=r.abstract, max_chars=20000))
    assert st.status == "ok"
    assert st.source in ("pdf", "abstract")

    # 3. citations (small)
    gc = await GetCitationsTool().arun(GetCitationsInputSchema(paper_id=pid, paper_limit=2))
    assert gc.status == "ok"
    records = gc.records
    assert len(records) >= 1

    # 4. enrich
    en = await EnrichCitationsTool().arun(EnrichIn(records=records))
    assert en.status == "ok"
    records = en.records

    # 5. download
    dl = await DownloadPdfsTool().arun(DownloadPdfsInputSchema(records=records))
    assert dl.status == "ok"
    ready = [
        rec["citingPaper"]["pdf_cache_key"]
        for rec in dl.records
        if rec["citingPaper"].get("pdf_status") in ("cached", "downloaded")
    ]
    if not ready:
        pytest.skip("no downloadable PDFs for the sampled citations")

    saved = []
    try:
        # 6/7. pdf text + save a (dummy) report per paper
        for key in ready:
            tx = await GetPdfTextTool().arun(GetPdfTextInputSchema(pdf_cache_key=key, max_chars=8000))
            assert tx.status == "ok"
            report = {
                "report": {"usage_type": "benchmark", "metrics": {
                    "usage_type_primary": "benchmark",
                    "evidence_quality": {"confidence": 0.5},
                    "results_signal": {"reported_improvement": "yes"},
                    "downstream_tasks": []}},
                "evidence": [{"quote": "q", "page_hint": 1}],
            }
            sv = await SaveReportTool().arun(SaveReportInputSchema(
                cache_key=key, report=report, seed_paper_id=pid, version=version, paper_id=key))
            assert sv.status == "ok"
            saved.append(key)

        # 8. get_report
        g = await GetReportTool().arun(GetReportInputSchema(
            cache_key=saved[0], seed_paper_id=pid, version=version))
        assert g.exists is True

        # 9. consolidate (full)
        co = await ConsolidateReportsTool().arun(ConsolidateReportsInputSchema(
            seed_paper_id=pid, cache_keys=saved, version=version))
        assert co.status == "ok"
        assert co.total_papers == len(saved)
        entry = co.master["papers"][0]
        assert "report" in entry and "evidence" in entry  # full embedded

        # 10. download link for the master
        lk = await GetDownloadLinkTool().arun(GetDownloadLinkInputSchema(
            s3_uri_or_key=co.master_s3_uri, expires_days=1))
        assert lk.status == "ok"
        assert lk.url and "Signature" in lk.url
    finally:
        # cleanup whatever we wrote under the pytest version
        for key in saved:
            cache.client.delete_object(
                Bucket=cache.bucket, Key=cache._agent_report_key(pid, version, key))
        cache.client.delete_object(
            Bucket=cache.bucket, Key=cache.master_report_key(pid, version))
