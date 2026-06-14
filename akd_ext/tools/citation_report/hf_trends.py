"""GetHfTrendsTool — Hugging Face download trends for a seed paper's target.

NO LLM — pure IO + deterministic rendering. Given a profiled ``target`` ({name, aliases,
domain, ...}), match it to an HF repo family in the archived metrics_daily snapshots
(S3), build a month-end cumulative-download series per repo, and return the structured
series plus an embeddable trend chart. Returns found=False when no repo matches.

This is the "Tool B" the report pipeline expects: the returned ``hf_figures_md`` /
``hf_figures`` plug straight into the report's HF section (see report_sections /
report_logic), or pass include_hf_trends=True + target to generate_report to wire it
in automatically.
"""

from akd._base import InputSchema, OutputSchema
from akd.tools import BaseTool
from pydantic import Field

from akd_ext.mcp import mcp_tool

from ._lib.env import get_cache
from ._lib.hf_trends_logic import S3Reader, build_hf_section, get_hf_trends


class GetHfTrendsInputSchema(InputSchema):
    """Input: the profiled target to find on Hugging Face."""

    target: dict = Field(
        ...,
        description=(
            "Profiled target with at least {name, aliases}. Distinctive name/alias tokens "
            "are matched against HF repo ids in the metrics snapshots."
        ),
    )
    max_repos: int = Field(
        default=8, ge=1,
        description="Cap the chart + tables to this many most-downloaded repos.",
    )


class GetHfTrendsOutputSchema(OutputSchema):
    """HF download trends; the chart is embedded as a base64 data URI."""

    status: str = Field(..., description="'ok' | 'not_found' | 'error'.")
    found: bool = Field(default=False, description="Whether any HF repo matched the target.")
    repos: list[dict] = Field(
        default_factory=list,
        description="Matched repos: {repo_id, name, repo_type, org, score, matched_on}.",
    )
    latest: dict = Field(
        default_factory=dict,
        description="Per-repo latest snapshot {repo_id: {downloads_all_time, downloads_last_30d, likes}}.",
    )
    monthly: dict = Field(
        default_factory=dict,
        description="Per-repo month-end series {repo_id: [{month, downloads_all_time, ...}]}.",
    )
    snapshot_month: str | None = Field(
        default=None, description="Latest snapshot month (YYYY-MM) across the family."
    )
    chart_data_uri: str | None = Field(
        default=None, description="Trend chart as a data:image/png;base64 URI (embeddable)."
    )
    hf_figures_md: str = Field(
        default="",
        description="Report-ready markdown for the HF section (pass to generate_report).",
    )
    hf_figures: dict = Field(
        default_factory=dict,
        description="{figure_name: data_uri} referenced by hf_figures_md.",
    )
    message: str | None = Field(default=None, description="Error / status detail, if any.")


@mcp_tool
class GetHfTrendsTool(BaseTool[GetHfTrendsInputSchema, GetHfTrendsOutputSchema]):
    """
    Hugging Face download trends for a seed paper's target (NO LLM, deterministic).

    Matches the profiled target's distinctive name/alias tokens to an HF repo family in the
    archived metrics_daily snapshots in S3, builds each repo's month-end cumulative
    all-time-download series (latest snapshot per calendar month — not a live HF API call),
    and renders one trend chart embedded as a base64 data URI. Returns found=False (status
    'not_found') when nothing matches, so the report can omit the section. The hf_figures_md
    / hf_figures outputs drop straight into the report's HF slot. Requires S3.
    """

    input_schema = GetHfTrendsInputSchema
    output_schema = GetHfTrendsOutputSchema

    async def _arun(self, params: GetHfTrendsInputSchema) -> GetHfTrendsOutputSchema:
        cache = get_cache()
        if not cache.enabled:
            return GetHfTrendsOutputSchema(
                status="error", message="S3 is required to read HF metrics snapshots."
            )

        target = params.target or {}
        target_name = str(target.get("name") or "Target")
        try:
            hf = get_hf_trends(target, S3Reader(cache), max_repos=params.max_repos)
        except Exception as e:  # never raise — the report degrades to a skipped note
            return GetHfTrendsOutputSchema(
                status="error", message=f"{type(e).__name__}: {e}"
            )

        hf_figures_md, hf_figures = build_hf_section(hf, target_name)
        if not hf.get("found"):
            return GetHfTrendsOutputSchema(
                status="not_found", found=False,
                hf_figures_md=hf_figures_md, hf_figures=hf_figures,
                message="No Hugging Face repo matched the target.",
            )

        return GetHfTrendsOutputSchema(
            status="ok",
            found=True,
            repos=hf.get("repos", []) or [],
            latest=hf.get("latest", {}) or {},
            monthly=hf.get("monthly", {}) or {},
            snapshot_month=hf.get("snapshot_month"),
            chart_data_uri=hf.get("chart_data_uri"),
            hf_figures_md=hf_figures_md,
            hf_figures=hf_figures,
        )
