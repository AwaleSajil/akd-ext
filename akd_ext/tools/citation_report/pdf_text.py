"""GetPdfTextTool — pull a citing paper's PDF from S3 and extract its text."""

from akd._base import InputSchema, OutputSchema
from akd.tools import BaseTool
from pydantic import Field

from akd_ext.mcp import mcp_tool

from ._lib.env import get_cache
from ._lib.paper_logic import get_pdf_text as _get_pdf_text


class GetPdfTextInputSchema(InputSchema):
    """Input: a citing paper's pdf_cache_key from download_pdfs."""

    pdf_cache_key: str = Field(
        ..., description="The pdf_cache_key annotated on a record by download_pdfs."
    )
    max_chars: int = Field(
        default=90000, ge=0, description="Truncate the returned text to this many chars (0 = no cap)."
    )


class GetPdfTextOutputSchema(OutputSchema):
    """Extracted page-marked text for the Citation Analyzer agent."""

    status: str = Field(..., description="'ok' | 'no_key' | 'no_pdf' | 'failed' | 'error'.")
    cache_key: str | None = Field(default=None, description="The pdf cache key.")
    page_count: int = Field(default=0, description="Number of pages extracted.")
    char_count: int = Field(default=0, description="Length of the returned text.")
    truncated: bool = Field(default=False, description="Whether the text was cut at max_chars.")
    text: str = Field(default="", description="Page-marked paper text ('--- page N ---').")
    message: str | None = Field(default=None, description="Error detail, if any.")


@mcp_tool
class GetPdfTextTool(BaseTool[GetPdfTextInputSchema, GetPdfTextOutputSchema]):
    """
    Pull a citing paper's PDF from the S3 cache and return its extracted text.

    NO LLM — pure IO. The Citation Analyzer agent runs the analysis on this text, then
    calls save_report. Text is page-marked so the agent can cite page hints. Returns
    no_pdf if the PDF is not in the cache (run download_pdfs first).
    """

    input_schema = GetPdfTextInputSchema
    output_schema = GetPdfTextOutputSchema

    async def _arun(self, params: GetPdfTextInputSchema) -> GetPdfTextOutputSchema:
        r = _get_pdf_text(params.pdf_cache_key, get_cache(), max_chars=params.max_chars)
        return GetPdfTextOutputSchema(
            status=r.get("status", "error"),
            cache_key=r.get("cache_key"),
            page_count=r.get("page_count", 0),
            char_count=r.get("char_count", 0),
            truncated=r.get("truncated", False),
            text=r.get("text", ""),
            message=r.get("message"),
        )
