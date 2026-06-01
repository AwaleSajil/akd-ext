"""Citation-report tools for akd-ext.

A pipeline that, given a seed paper, fetches its citing papers, downloads their PDFs,
and persists per-paper + consolidated reports in S3. Most tools are pure-IO; target
profiling lives in the agent layer.

The one exception is AnalyzeCitationsTool: it folds the per-citation analysis loop and
consolidation into a single tool that calls the LLM itself (OpenAI, in parallel), so a
workflow can fan out over many papers in one call instead of a sequential loop.

Importing this package registers every tool via the @mcp_tool decorator.
"""

from .resolve import (
    ResolvePaperTool,
    ResolvePaperInputSchema,
    ResolvePaperOutputSchema,
)
from .citations import (
    GetCitationsTool,
    GetCitationsInputSchema,
    GetCitationsOutputSchema,
)
from .enrich import (
    EnrichCitationsTool,
    EnrichCitationsInputSchema,
    EnrichCitationsOutputSchema,
)
from .download import (
    DownloadPdfsTool,
    DownloadPdfsInputSchema,
    DownloadPdfsOutputSchema,
)
from .seed_text import (
    GetSeedTextTool,
    GetSeedTextInputSchema,
    GetSeedTextOutputSchema,
)
from .pdf_text import (
    GetPdfTextTool,
    GetPdfTextInputSchema,
    GetPdfTextOutputSchema,
)
from .reports import (
    SaveReportTool,
    SaveReportInputSchema,
    SaveReportOutputSchema,
    GetReportTool,
    GetReportInputSchema,
    GetReportOutputSchema,
    ConsolidateReportsTool,
    ConsolidateReportsInputSchema,
    ConsolidateReportsOutputSchema,
)
from .download_link import (
    GetDownloadLinkTool,
    GetDownloadLinkInputSchema,
    GetDownloadLinkOutputSchema,
)
from .analyze import (
    AnalyzeCitationsTool,
    AnalyzeCitationsInputSchema,
    AnalyzeCitationsOutputSchema,
)

__all__ = [
    "ResolvePaperTool",
    "GetCitationsTool",
    "EnrichCitationsTool",
    "DownloadPdfsTool",
    "GetSeedTextTool",
    "GetPdfTextTool",
    "SaveReportTool",
    "GetReportTool",
    "ConsolidateReportsTool",
    "GetDownloadLinkTool",
    "AnalyzeCitationsTool",
]
