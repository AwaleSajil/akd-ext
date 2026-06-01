"""ResolvePaperTool — resolve a seed paper reference to a canonical S2 paper."""

from akd._base import InputSchema, OutputSchema
from akd.tools import BaseTool
from pydantic import Field

from akd_ext.mcp import mcp_tool

from ._lib.env import s2_api_key
from ._lib.resolve_logic import resolve_paper as _resolve_paper


class ResolvePaperInputSchema(InputSchema):
    """Input for resolving a seed paper reference."""

    user_input: str = Field(
        ...,
        description="A Semantic Scholar URL, an arXiv id or URL, a DOI, or a free-text paper title.",
    )
    limit: int = Field(
        default=5, ge=1, le=20,
        description="Max number of candidates to return when the title match is ambiguous.",
    )


class ResolvePaperCandidate(OutputSchema):
    """A single candidate paper (when the title match is ambiguous)."""

    paper_id: str = Field(default="", description="Canonical Semantic Scholar paperId.")
    title: str = Field(default="", description="Paper title.")
    year: int | None = Field(default=None, description="Publication year.")
    url: str = Field(default="", description="Semantic Scholar URL.")
    citation_count: int | None = Field(default=None, description="Total citing papers.")


class ResolvePaperOutputSchema(OutputSchema):
    """Resolved seed paper, or candidates/error."""

    status: str = Field(..., description="'ok' | 'ambiguous' | 'not_found' | 'error'.")
    input_kind: str | None = Field(default=None, description="'id/url' or 'title'.")
    paper_id: str = Field(default="", description="Canonical paperId when status is 'ok'.")
    title: str = Field(default="", description="Resolved paper title.")
    abstract: str = Field(default="", description="Abstract (feeds the target profiler).")
    year: int | None = Field(default=None, description="Publication year.")
    url: str = Field(default="", description="Semantic Scholar URL.")
    external_ids: dict = Field(default_factory=dict, description="External ids (ArXiv, DOI, ...).")
    citation_count: int | None = Field(
        default=None, description="Total citing papers (for fresh-fetch time estimate)."
    )
    candidates: list[ResolvePaperCandidate] = Field(
        default_factory=list, description="Ranked options when status is 'ambiguous'."
    )
    message: str | None = Field(
        default=None, description="Explanation when status is 'not_found' or 'error'."
    )


@mcp_tool
class ResolvePaperTool(BaseTool[ResolvePaperInputSchema, ResolvePaperOutputSchema]):
    """
    Resolve a paper reference to a canonical Semantic Scholar paper.

    Accepts a Semantic Scholar URL, an arXiv id or URL, a DOI, or a free-text title,
    and returns the canonical paper_id (the stable handle used for fetching citations
    and caching) plus title, abstract, year, url, external ids, and citation count.
    On an ambiguous title, returns ranked candidates instead.
    """

    input_schema = ResolvePaperInputSchema
    output_schema = ResolvePaperOutputSchema

    async def _arun(self, params: ResolvePaperInputSchema) -> ResolvePaperOutputSchema:
        r = _resolve_paper(params.user_input, api_key=s2_api_key(), limit=params.limit)
        paper = r.get("paper") or {}
        candidates = [
            ResolvePaperCandidate(
                paper_id=c.get("paper_id", ""), title=c.get("title", ""),
                year=c.get("year"), url=c.get("url", ""),
                citation_count=c.get("citation_count"),
            )
            for c in (r.get("candidates") or [])
        ]
        return ResolvePaperOutputSchema(
            status=r.get("status", "error"),
            input_kind=r.get("input_kind"),
            paper_id=paper.get("paper_id", ""),
            title=paper.get("title", ""),
            abstract=paper.get("abstract", ""),
            year=paper.get("year"),
            url=paper.get("url", ""),
            external_ids=paper.get("external_ids", {}) or {},
            citation_count=paper.get("citation_count"),
            candidates=candidates,
            message=r.get("message"),
        )
