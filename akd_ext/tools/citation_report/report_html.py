"""GenerateReportTool — render the consolidated master JSON into one portable HTML report.

NO LLM — pure IO + deterministic rendering. Reads reports/{seed_paper_id}/{version}/_master.json
(the ConsolidateReportsTool output), flattens its papers[], builds the usage charts + tables +
ranked impactful statements, and writes a single self-contained HTML file (charts embedded as
base64) to reports/{seed_paper_id}/{version}/_report.html. Presign it with get_download_link.
"""

from akd._base import InputSchema, OutputSchema
from akd.tools import BaseTool
from pydantic import Field

from akd_ext.mcp import mcp_tool

from ._lib.env import get_cache
from ._lib.report_logic import generate_report_io as _generate_report_io
from ._lib.report_sections import DEFAULT_OBJECTIVE, DEFAULT_TITLE


class GenerateReportInputSchema(InputSchema):
    """Input: which consolidated master to render."""

    seed_paper_id: str = Field(..., description="The SEED paper's paper_id (report namespace).")
    version: str = Field(default="v1", description="Report version label (matches consolidate_reports).")
    title: str = Field(default=DEFAULT_TITLE, description="Report title / cover heading.")
    objective: str = Field(default=DEFAULT_OBJECTIVE, description="Objective paragraph for the cover (markdown).")
    store: bool = Field(default=True, description="Write the HTML to S3 (reports/{seed}/{version}/_report.html).")


class GenerateReportOutputSchema(OutputSchema):
    """Where the HTML report was stored, plus headline counts."""

    status: str = Field(..., description="'ok' | 'no_seed' | 'no_master' | 'error'.")
    seed_paper_id: str = Field(default="", description="The seed paper's id.")
    version: str = Field(default="", description="Report version label.")
    title: str = Field(default="", description="Report title used.")
    total_papers: int = Field(default=0, description="Papers rendered into the report.")
    n_errors: int = Field(default=0, description="Paper entries that failed to flatten.")
    counts_by_usage_type: dict = Field(default_factory=dict, description="Paper counts per usage_type_primary.")
    html_s3_uri: str | None = Field(default=None, description="S3 URI of the HTML (pass to get_download_link).")
    html_bytes: int = Field(default=0, description="Size of the rendered HTML in bytes.")
    message: str | None = Field(default=None, description="Error / status detail, if any.")


@mcp_tool
class GenerateReportTool(BaseTool[GenerateReportInputSchema, GenerateReportOutputSchema]):
    """
    Render the consolidated master report (per-paper JSON) into one portable HTML file.

    NO LLM — deterministic. Loads reports/{seed_paper_id}/{version}/_master.json (run
    consolidate_reports first), aggregates the papers[] into usage counts, derived signals,
    ranked impactful statements, and adaptation / benchmark tables, and embeds the matplotlib
    charts as base64 so the HTML is self-contained. Writes it to
    reports/{seed_paper_id}/{version}/_report.html and returns html_s3_uri — presign that with
    get_download_link for a browser-openable URL.
    """

    input_schema = GenerateReportInputSchema
    output_schema = GenerateReportOutputSchema

    async def _arun(self, params: GenerateReportInputSchema) -> GenerateReportOutputSchema:
        r = _generate_report_io(
            get_cache(), params.seed_paper_id,
            version=params.version,
            title=params.title,
            objective=params.objective,
            store=params.store,
        )
        return GenerateReportOutputSchema(
            status=r.get("status", "error"),
            seed_paper_id=r.get("seed_paper_id", ""),
            version=r.get("version", ""),
            title=r.get("title", ""),
            total_papers=r.get("total_papers", 0),
            n_errors=r.get("n_errors", 0),
            counts_by_usage_type=r.get("counts_by_usage_type", {}) or {},
            html_s3_uri=r.get("html_s3_uri"),
            html_bytes=r.get("html_bytes", 0),
            message=r.get("message"),
        )
