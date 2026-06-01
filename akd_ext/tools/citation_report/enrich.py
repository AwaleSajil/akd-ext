"""EnrichCitationsTool — validate/fill external ids on citation records."""

from akd._base import InputSchema, OutputSchema
from akd.tools import BaseTool
from pydantic import Field

from akd_ext.mcp import mcp_tool

from ._lib.env import min_interval_s, s2_api_key
from ._lib.enrich_logic import enrich_citations_records


class EnrichCitationsInputSchema(InputSchema):
    """Input: the citation records from get_citations."""

    records: list[dict] = Field(
        ...,
        description="Citing-paper records from get_citations: [{citingPaper: {...}}].",
    )
    request_delay: float = Field(
        default=2.0, ge=0.0,
        description="Seconds between Semantic Scholar lookups for records missing ids.",
    )


class EnrichCitationsOutputSchema(OutputSchema):
    """Records with external ids validated/filled, plus counts."""

    status: str = Field(..., description="'ok' | 'error'.")
    record_count: int = Field(default=0, description="Total records processed.")
    enriched: int = Field(default=0, description="Records that now have a usable ArXiv/DOI id.")
    already_had: int = Field(default=0, description="Records that already had ids (no API call made).")
    filled: int = Field(default=0, description="Records whose ids were filled via a lookup.")
    flagged_no_id: int = Field(default=0, description="Records still without any usable id (undownloadable).")
    flagged_no_oa: int = Field(default=0, description="Records with no id and no open-access PDF (paywalled).")
    records: list[dict] = Field(
        default_factory=list,
        description="The same records with externalIds filled in place; pass to download_pdfs.",
    )
    message: str | None = Field(default=None, description="Error detail, if any.")


@mcp_tool
class EnrichCitationsTool(BaseTool[EnrichCitationsInputSchema, EnrichCitationsOutputSchema]):
    """
    Validate and fill external ids (ArXiv/DOI) on citation records.

    Records that already carry an id are skipped (no API call). Records missing ids get
    a Semantic Scholar lookup from their url. Records still without a usable id are
    flagged as undownloadable; those also lacking an open-access PDF are flagged paywalled.
    """

    input_schema = EnrichCitationsInputSchema
    output_schema = EnrichCitationsOutputSchema

    async def _arun(self, params: EnrichCitationsInputSchema) -> EnrichCitationsOutputSchema:
        r = enrich_citations_records(
            params.records, api_key=s2_api_key(), request_delay=params.request_delay,
        )
        return EnrichCitationsOutputSchema(
            status=r.get("status", "error"),
            record_count=r.get("record_count", 0),
            enriched=r.get("enriched", 0),
            already_had=r.get("already_had", 0),
            filled=r.get("filled", 0),
            flagged_no_id=r.get("flagged_no_id", 0),
            flagged_no_oa=r.get("flagged_no_oa", 0),
            records=r.get("records", []) or [],
            message=r.get("message"),
        )
