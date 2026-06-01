"""DownloadPdfsTool — download citing-paper PDFs into the S3 pdf cache."""

from akd._base import InputSchema, OutputSchema
from akd.tools import BaseTool
from pydantic import Field

from akd_ext.mcp import mcp_tool

from ._lib.env import get_cache, unpaywall_email
from ._lib.download_logic import download_pdfs_records


class DownloadPdfsInputSchema(InputSchema):
    """Input: enriched citation records from enrich_citations."""

    records: list[dict] = Field(
        ..., description="Enriched citing-paper records (with externalIds)."
    )
    request_delay: float = Field(
        default=1.0, ge=0.0, description="Seconds between fresh downloads."
    )
    force: bool = Field(
        default=False, description="Re-download even if the PDF is already cached in S3."
    )
    limit: int = Field(
        default=0, ge=0, description="Cap how many records to process (0 = all; for testing)."
    )


class DownloadPdfsOutputSchema(OutputSchema):
    """Download outcome; PDFs are stored in S3, not returned."""

    status: str = Field(..., description="'ok' | 'error'.")
    record_count: int = Field(default=0, description="Total records.")
    downloaded: int = Field(default=0, description="PDFs freshly downloaded this run.")
    from_cache: int = Field(default=0, description="PDFs already present in the S3 pdf cache.")
    failed: int = Field(default=0, description="PDFs that could not be obtained.")
    no_id: int = Field(default=0, description="Records with no ArXiv/DOI to download by.")
    records: list[dict] = Field(
        default_factory=list,
        description="Records annotated with pdf_cache_key / pdf_status / pdf_source. Pass to extraction.",
    )
    message: str | None = Field(default=None, description="Error detail, if any.")


@mcp_tool
class DownloadPdfsTool(BaseTool[DownloadPdfsInputSchema, DownloadPdfsOutputSchema]):
    """
    Download citing-paper PDFs into the S3 pdf cache (cache-first, silent).

    PDFs are binary and are NOT returned — each is content-addressed in the S3 pdf
    cache by a key derived from ArXiv id or DOI. Records are annotated with
    pdf_cache_key (the S3 key), pdf_status (cached/downloaded/failed/no_id), and
    pdf_source. Download order per paper: ArXiv direct, then a DOI waterfall (Unpaywall,
    Semantic Scholar, doi2pdf, scihub, PyPaperBot). Requires S3.
    """

    input_schema = DownloadPdfsInputSchema
    output_schema = DownloadPdfsOutputSchema

    async def _arun(self, params: DownloadPdfsInputSchema) -> DownloadPdfsOutputSchema:
        r = download_pdfs_records(
            params.records,
            get_cache(),
            doi_email=unpaywall_email(),
            request_delay=params.request_delay,
            force=params.force,
            limit=(params.limit or None),
        )
        return DownloadPdfsOutputSchema(
            status=r.get("status", "error"),
            record_count=r.get("record_count", 0),
            downloaded=r.get("downloaded", 0),
            from_cache=r.get("from_cache", 0),
            failed=r.get("failed", 0),
            no_id=r.get("no_id", 0),
            records=r.get("records", []) or [],
            message=r.get("message"),
        )
