"""GetDownloadLinkTool — presigned download URL for any object in the bucket."""

from akd._base import InputSchema, OutputSchema
from akd.tools import BaseTool
from pydantic import Field

from akd_ext.mcp import mcp_tool

from ._lib.env import get_cache
from ._lib.share_logic import get_download_link as _get_download_link


class GetDownloadLinkInputSchema(InputSchema):
    """Input: the S3 object to presign."""

    s3_uri_or_key: str = Field(
        ...,
        description="An s3://bucket/key URI (e.g. master_s3_uri or citations_s3_uri) or a bare key in the bucket.",
    )
    expires_days: int = Field(
        default=7, ge=1, le=7, description="Link lifetime in days (max 7)."
    )
    download_name: str = Field(
        default="", description="Filename for the download (defaults to the object's basename)."
    )


class GetDownloadLinkOutputSchema(OutputSchema):
    """The presigned URL."""

    status: str = Field(..., description="'ok' | 'bad_uri' | 'not_found' | 'error'.")
    url: str | None = Field(default=None, description="Presigned download URL (browser-openable).")
    expires_at: str | None = Field(default=None, description="When the URL expires (ISO).")
    expires_seconds: int | None = Field(default=None, description="Lifetime in seconds.")
    s3_uri: str | None = Field(default=None, description="The object's s3:// URI.")
    message: str | None = Field(default=None, description="Error detail, if any.")


@mcp_tool
class GetDownloadLinkTool(BaseTool[GetDownloadLinkInputSchema, GetDownloadLinkOutputSchema]):
    """
    Get a presigned download URL for any object in the cache bucket.

    NO LLM, nothing persisted. Accepts an s3://bucket/key URI (e.g. the master_s3_uri
    from consolidate_reports or citations_s3_uri from get_citations) or a bare key.
    Only objects in this bucket can be signed. The presigned URL is the only
    browser-openable way to download from the private bucket; it expires (<= 7 days,
    sooner under temporary STS credentials).
    """

    input_schema = GetDownloadLinkInputSchema
    output_schema = GetDownloadLinkOutputSchema

    async def _arun(self, params: GetDownloadLinkInputSchema) -> GetDownloadLinkOutputSchema:
        r = _get_download_link(
            get_cache(), params.s3_uri_or_key,
            expires_days=params.expires_days,
            download_name=(params.download_name or None),
        )
        return GetDownloadLinkOutputSchema(
            status=r.get("status", "error"),
            url=r.get("url"),
            expires_at=r.get("expires_at"),
            expires_seconds=r.get("expires_seconds"),
            s3_uri=r.get("s3_uri"),
            message=r.get("message"),
        )
