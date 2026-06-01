"""Report tools — save / get / consolidate per-paper reports in S3.

Reports are namespaced by the SEED paper: reports/{seed_paper_id}/{version}/.
The Citation Analyzer agent produces each report; these tools only move JSON (no LLM).
"""

from akd._base import InputSchema, OutputSchema
from akd.tools import BaseTool
from pydantic import Field

from akd_ext.mcp import mcp_tool

from ._lib.env import get_cache
from ._lib.paper_logic import get_report as _get_report
from ._lib.paper_logic import save_report as _save_report
from ._lib.consolidate_logic import consolidate_reports_io as _consolidate_reports_io


# ── save_report ──────────────────────────────────────────────────────────────────

class SaveReportInputSchema(InputSchema):
    """Input: one citing paper's report produced by the analyzer agent."""

    cache_key: str = Field(..., description="The citing paper's pdf_cache_key (from download_pdfs).")
    report: dict = Field(..., description="The agent's report object ({report, evidence}).")
    seed_paper_id: str = Field(..., description="The SEED paper's paper_id (report namespace).")
    version: str = Field(default="v1", description="Report version label (bump to re-run cleanly).")
    paper_id: str = Field(default="", description="The citing paper's own paperId (provenance).")


class SaveReportOutputSchema(OutputSchema):
    """Where the report was stored."""

    status: str = Field(..., description="'ok' | 'no_seed' | 'no_key' | 'error'.")
    cache_key: str | None = Field(default=None, description="The citing paper's cache key.")
    s3_uri: str | None = Field(default=None, description="S3 URI of the saved report.")
    message: str | None = Field(default=None, description="Error detail, if any.")


@mcp_tool
class SaveReportTool(BaseTool[SaveReportInputSchema, SaveReportOutputSchema]):
    """
    Write an agent-produced report for one citing paper to the S3 report store.

    NO LLM — pure IO. Stored at reports/{seed_paper_id}/{version}/{cache_key}.json, so
    each seed paper gets its own report space. consolidate_reports later merges these.
    """

    input_schema = SaveReportInputSchema
    output_schema = SaveReportOutputSchema

    async def _arun(self, params: SaveReportInputSchema) -> SaveReportOutputSchema:
        r = _save_report(
            params.cache_key, params.report, get_cache(),
            seed_paper_id=params.seed_paper_id, version=params.version, paper_id=params.paper_id,
        )
        return SaveReportOutputSchema(
            status=r.get("status", "error"),
            cache_key=r.get("cache_key"),
            s3_uri=r.get("s3_uri"),
            message=r.get("message"),
        )


# ── get_report ───────────────────────────────────────────────────────────────────

class GetReportInputSchema(InputSchema):
    """Input: which citing paper's report to read (cache-first helper)."""

    cache_key: str = Field(..., description="The citing paper's pdf_cache_key.")
    seed_paper_id: str = Field(..., description="The SEED paper's paper_id (report namespace).")
    version: str = Field(default="v1", description="Report version label.")


class GetReportOutputSchema(OutputSchema):
    """The saved report, if it exists."""

    status: str = Field(..., description="'ok' | 'no_seed' | 'no_key' | 'error'.")
    cache_key: str | None = Field(default=None, description="The citing paper's cache key.")
    exists: bool = Field(default=False, description="Whether a report is already stored.")
    report: dict | None = Field(default=None, description="The saved report, or null if none.")
    message: str | None = Field(default=None, description="Error detail, if any.")


@mcp_tool
class GetReportTool(BaseTool[GetReportInputSchema, GetReportOutputSchema]):
    """
    Read a previously-saved report from the S3 report store (cache-first helper).

    NO LLM — pure IO. Lets the analyzer skip re-analysis when a report already exists
    for this citing paper under this seed paper. Returns exists=False if none yet.
    """

    input_schema = GetReportInputSchema
    output_schema = GetReportOutputSchema

    async def _arun(self, params: GetReportInputSchema) -> GetReportOutputSchema:
        r = _get_report(
            params.cache_key, get_cache(),
            seed_paper_id=params.seed_paper_id, version=params.version,
        )
        return GetReportOutputSchema(
            status=r.get("status", "error"),
            cache_key=r.get("cache_key"),
            exists=r.get("exists", False),
            report=r.get("report"),
            message=r.get("message"),
        )


# ── consolidate_reports ──────────────────────────────────────────────────────────

class ConsolidateReportsInputSchema(InputSchema):
    """Input: which reports to merge into the master."""

    seed_paper_id: str = Field(..., description="The SEED paper's paper_id (report namespace).")
    cache_keys: list[str] = Field(
        default_factory=list,
        description="Specific citing-paper cache keys to merge. Empty = merge ALL under this seed/version.",
    )
    version: str = Field(default="v1", description="Report version label.")
    include_full: bool = Field(
        default=True,
        description="True: each master entry embeds the full report + evidence. False: summary only.",
    )


class ConsolidateReportsOutputSchema(OutputSchema):
    """The consolidated master report."""

    status: str = Field(..., description="'ok' | 'no_seed' | 'error'.")
    seed_paper_id: str = Field(default="", description="The seed paper's id.")
    version: str = Field(default="", description="Report version label.")
    total_papers: int = Field(default=0, description="Number of papers in the master.")
    missing: list[str] = Field(default_factory=list, description="Requested keys with no saved report.")
    counts_by_usage_type: dict = Field(default_factory=dict, description="Paper counts per usage_type.")
    master_s3_uri: str | None = Field(default=None, description="S3 URI of the master JSON (pass to get_download_link).")
    master: dict = Field(default_factory=dict, description="The full master report object.")
    message: str | None = Field(default=None, description="Error detail, if any.")


@mcp_tool
class ConsolidateReportsTool(BaseTool[ConsolidateReportsInputSchema, ConsolidateReportsOutputSchema]):
    """
    Merge the per-paper reports saved in S3 into ONE master JSON.

    NO LLM — pure IO. By default each master entry carries the COMPLETE agent output
    (report + evidence + meta) so the master is self-contained. Writes the master to
    reports/{seed_paper_id}/{version}/_master.json and returns it inline.
    """

    input_schema = ConsolidateReportsInputSchema
    output_schema = ConsolidateReportsOutputSchema

    async def _arun(self, params: ConsolidateReportsInputSchema) -> ConsolidateReportsOutputSchema:
        r = _consolidate_reports_io(
            get_cache(), params.seed_paper_id,
            version=params.version,
            cache_keys=(params.cache_keys or None),
            include_full=params.include_full,
        )
        return ConsolidateReportsOutputSchema(
            status=r.get("status", "error"),
            seed_paper_id=r.get("seed_paper_id", ""),
            version=r.get("version", ""),
            total_papers=r.get("total_papers", 0),
            missing=r.get("missing", []) or [],
            counts_by_usage_type=r.get("counts_by_usage_type", {}) or {},
            master_s3_uri=r.get("master_s3_uri"),
            master=r.get("master", {}) or {},
            message=r.get("message"),
        )
