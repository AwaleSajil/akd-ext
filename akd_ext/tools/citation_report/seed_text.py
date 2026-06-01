"""GetSeedTextTool — extract the seed paper's text (full PDF, abstract fallback)."""

from akd._base import InputSchema, OutputSchema
from akd.tools import BaseTool
from pydantic import Field

from akd_ext.mcp import mcp_tool

from ._lib.env import get_cache, unpaywall_email
from ._lib.seed_logic import get_seed_text as _get_seed_text


class GetSeedTextInputSchema(InputSchema):
    """Input: the seed paper's identifiers + metadata (from resolve_paper)."""

    external_ids: dict = Field(
        ..., description="Seed paper external ids (ArXiv/DOI) from resolve_paper."
    )
    title: str = Field(default="", description="Seed paper title (fallback material).")
    abstract: str = Field(default="", description="Seed paper abstract (fallback material).")
    max_chars: int = Field(
        default=90000, ge=0, description="Truncate the returned text to this many chars (0 = no cap)."
    )


class GetSeedTextOutputSchema(OutputSchema):
    """Seed paper text for the Target Profiler agent."""

    status: str = Field(..., description="'ok' | 'empty' | 'error'.")
    source: str = Field(default="", description="'pdf' | 'abstract' | 'none' — where the text came from.")
    page_count: int = Field(default=0, description="Pages, if the source was a PDF.")
    char_count: int = Field(default=0, description="Length of the returned text.")
    truncated: bool = Field(default=False, description="Whether the text was cut at max_chars.")
    text: str = Field(default="", description="The seed paper text (page-marked if from a PDF).")
    message: str | None = Field(default=None, description="Error detail, if any.")


@mcp_tool
class GetSeedTextTool(BaseTool[GetSeedTextInputSchema, GetSeedTextOutputSchema]):
    """
    Get the seed paper's text for the Target Profiler agent.

    Tries the full PDF first (S3 pdf-cache first, else download), and falls back to
    title + abstract if no PDF can be obtained. The `source` field tells the profiler
    how much material it got. Requires S3 for the PDF path; the abstract fallback works
    without it.
    """

    input_schema = GetSeedTextInputSchema
    output_schema = GetSeedTextOutputSchema

    async def _arun(self, params: GetSeedTextInputSchema) -> GetSeedTextOutputSchema:
        r = _get_seed_text(
            get_cache(),
            external_ids=params.external_ids,
            title=params.title,
            abstract=params.abstract,
            doi_email=unpaywall_email(),
            max_chars=params.max_chars,
        )
        return GetSeedTextOutputSchema(
            status=r.get("status", "error"),
            source=r.get("source", ""),
            page_count=r.get("page_count", 0),
            char_count=r.get("char_count", 0),
            truncated=r.get("truncated", False),
            text=r.get("text", ""),
            message=r.get("message"),
        )
