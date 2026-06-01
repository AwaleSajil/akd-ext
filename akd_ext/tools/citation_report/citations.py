"""GetCitationsTool — fetch citing papers, with a completeness-aware S3 cache."""

from akd._base import InputSchema, OutputSchema
from akd.tools import BaseTool
from pydantic import Field

from akd_ext.mcp import mcp_tool

from ._lib.env import get_cache, min_interval_s, s2_api_key
from ._lib.citations_logic import get_citations_records


class GetCitationsInputSchema(InputSchema):
    """Input for fetching the papers that cite a seed paper."""

    paper_id: str = Field(..., description="Canonical Semantic Scholar paperId (from resolve_paper).")
    use_cache: bool = Field(
        default=True,
        description="True: serve the S3 cache if it satisfies the request, else fetch fresh. False: always re-fetch.",
    )
    paper_limit: int = Field(
        default=0, ge=0,
        description="Max number of citing papers to return (0 = all).",
    )
    citation_count: int = Field(
        default=0, ge=0,
        description="Total citations from resolve_paper; used only to estimate fresh-fetch time.",
    )
    peek_only: bool = Field(
        default=False,
        description="True: report cache status only (no fetch). Use to warn before a slow fresh fetch.",
    )


class GetCitationsOutputSchema(OutputSchema):
    """The citing-paper records, or a cache-status peek."""

    status: str = Field(..., description="'ok' | 'error'.")
    served: str = Field(default="", description="'cache' | 'cache_sliced' | 'fresh' | 'peek' | 'error'.")
    from_cache: bool = Field(default=False, description="Whether records came from the S3 cache.")
    record_count: int | None = Field(default=None, description="Number of records returned.")
    fetched_at: str | None = Field(default=None, description="When the cached list was fetched (ISO).")
    citations_s3_uri: str | None = Field(
        default=None, description="S3 URI of the cached citation list (pass to get_download_link)."
    )
    cache: dict = Field(default_factory=dict, description="Cache status: cached, complete, age_days, record_count, estimated_fresh_seconds.")
    records: list[dict] = Field(
        default_factory=list,
        description="Citing-paper records: [{citingPaper: {paperId, title, year, externalIds, openAccessPdf, ...}}]. Empty on peek.",
    )
    message: str | None = Field(default=None, description="Error detail, if any.")


@mcp_tool
class GetCitationsTool(BaseTool[GetCitationsInputSchema, GetCitationsOutputSchema]):
    """
    Get the list of papers that cite a seed paper (with an S3 citation cache).

    The cache is completeness-aware: a partial (limited) cached list won't satisfy a
    request for more papers. Set peek_only=True first to show the user the cache age /
    count / estimated fresh-fetch time, then call again to fetch. Records are returned
    inline so the workflow can pass them to enrich_citations.
    """

    input_schema = GetCitationsInputSchema
    output_schema = GetCitationsOutputSchema

    async def _arun(self, params: GetCitationsInputSchema) -> GetCitationsOutputSchema:
        r = get_citations_records(
            params.paper_id,
            get_cache(),
            api_key=s2_api_key(),
            use_cache=params.use_cache,
            paper_limit=params.paper_limit,
            min_interval_s=min_interval_s(),
            citation_count=(params.citation_count or None),
            peek_only=params.peek_only,
        )
        return GetCitationsOutputSchema(
            status=r.get("status", "error"),
            served=r.get("served", ""),
            from_cache=r.get("from_cache", False),
            record_count=r.get("record_count"),
            fetched_at=r.get("fetched_at"),
            citations_s3_uri=r.get("citations_s3_uri"),
            cache=r.get("cache", {}) or {},
            records=r.get("records", []) or [],
            message=r.get("message"),
        )
