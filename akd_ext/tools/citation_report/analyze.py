"""AnalyzeCitationsTool — the one LLM-bearing citation-report tool.

Folds the per-paper analysis loop (AGENT B) and consolidation (AGENT C) into a single
tool so an agent / Agent Builder workflow can fan out over many citing papers in one
call instead of a sequential loop. The fan-out and the LLM call live in code (see
_lib/analyze_logic.py); every other citation_report tool stays pure-IO.
"""

from akd._base import InputSchema, OutputSchema
from akd.tools import BaseTool
from pydantic import BaseModel, Field

from akd_ext.mcp import mcp_tool

from ._lib.analyze_logic import analyze_citations_batch
from ._lib.env import get_cache


class TargetProfile(BaseModel):
    """The seed paper's target profile (from the Target Profiler agent)."""

    name: str = Field(..., description="Canonical name of the target model/method/tool, e.g. 'Prithvi'.")
    aliases: list[str] = Field(
        default_factory=list,
        description="All spellings/abbreviations/version names a citing paper might use (include the bare name).",
    )
    domain: str = Field(default="", description="Short field/modality phrase, e.g. 'geospatial foundation model'.")
    what_to_look_for: str = Field(
        default="",
        description="2-5 sentences guiding the analyzer on how to detect and classify usage of this target.",
    )


class AnalyzeCitationsInputSchema(InputSchema):
    """Input: the citation records + the seed paper id + its target profile."""

    citations: list[dict] = Field(
        ...,
        description="The citing-paper records (the `records` list from get_citations).",
    )
    seed_paper_id: str = Field(
        ..., description="The SEED paper's paper_id (from resolve_paper); reports are namespaced by it."
    )
    target: TargetProfile = Field(
        ..., description="The target profile {name, aliases, domain, what_to_look_for}."
    )
    version: str = Field(default="v1", description="Report version label (bump to re-run cleanly).")
    n_parallel: int = Field(
        default=6, ge=1, le=32, description="Max concurrent papers / LLM calls."
    )
    model: str = Field(default="", description="Analyzer model (default env ANALYZER_MODEL or 'gpt-5.5').")
    max_chars: int = Field(
        default=90000, ge=0, description="Truncate each paper's text fed to the LLM (0 = no cap)."
    )
    use_report_cache: bool = Field(
        default=True, description="Skip papers that already have a saved report (idempotent / resumable)."
    )
    citations_s3_uri: str = Field(
        default="", description="Optional S3 URI of the citation JSON, to also return its download link."
    )
    paper_limit: int = Field(
        default=0, ge=0,
        description="Cap papers processed (0 = all). Use to chunk a huge list and stay under the request timeout.",
    )


class AnalyzeCitationsOutputSchema(OutputSchema):
    """Consolidated outcome: links, summary, counts, and per-paper handles."""

    status: str = Field(..., description="'ok' | 'error'.")
    seed_paper_id: str = Field(default="", description="The seed paper's id.")
    summary: str = Field(default="", description="One-line human summary of the run.")
    total_papers: int = Field(default=0, description="Papers in the consolidated master.")
    counts_by_usage_type: dict = Field(default_factory=dict, description="Paper counts per usage_type.")
    status_counts: dict = Field(default_factory=dict, description="Per-paper outcome counts (ok/from_cache/failed/...).")
    report_download_url: str | None = Field(default=None, description="Presigned URL for the master report JSON.")
    citations_download_url: str | None = Field(default=None, description="Presigned URL for the citation JSON (if citations_s3_uri given).")
    master_s3_uri: str | None = Field(default=None, description="S3 URI of the master report JSON.")
    missing: list[str] = Field(default_factory=list, description="Saved keys not found at consolidation.")
    results: list[dict] = Field(
        default_factory=list,
        description="Per-paper handles: [{cache_key, paper_id, status[, detail]}].",
    )
    message: str | None = Field(default=None, description="Error detail, if any.")


@mcp_tool
class AnalyzeCitationsTool(BaseTool[AnalyzeCitationsInputSchema, AnalyzeCitationsOutputSchema]):
    """
    Analyze ALL citing papers in ONE call: download → LLM analyze → save (in parallel),
    then consolidate + presign. The only LLM-bearing tool in citation_report.

    For each citing paper (bounded by n_parallel concurrent LLM calls): skip if a report
    already exists, download the PDF to the S3 cache, extract the text, run the Citation
    Analyzer LLM (structured output), and save the report. Then merge all reports into a
    master JSON and presign the download links. Pass `target` from the Target Profiler.
    Idempotent (use_report_cache) and chunkable (paper_limit) for large citation lists.
    """

    input_schema = AnalyzeCitationsInputSchema
    output_schema = AnalyzeCitationsOutputSchema

    async def _arun(self, params: AnalyzeCitationsInputSchema) -> AnalyzeCitationsOutputSchema:
        r = await analyze_citations_batch(
            get_cache(),
            params.citations,
            params.seed_paper_id,
            params.target.model_dump(),
            version=params.version,
            n_parallel=params.n_parallel,
            model=params.model,
            max_chars=params.max_chars,
            use_report_cache=params.use_report_cache,
            citations_s3_uri=params.citations_s3_uri,
            paper_limit=params.paper_limit,
        )
        return AnalyzeCitationsOutputSchema(
            status=r.get("status", "error"),
            seed_paper_id=r.get("seed_paper_id", ""),
            summary=r.get("summary", ""),
            total_papers=r.get("total_papers", 0),
            counts_by_usage_type=r.get("counts_by_usage_type", {}) or {},
            status_counts=r.get("status_counts", {}) or {},
            report_download_url=r.get("report_download_url"),
            citations_download_url=r.get("citations_download_url"),
            master_s3_uri=r.get("master_s3_uri"),
            missing=r.get("missing", []) or [],
            results=r.get("results", []) or [],
            message=r.get("message"),
        )
