"""Citation-report tools for akd-ext.

A pipeline of pure-IO tools (no LLM inside any tool) that, given a seed paper, fetch
its citing papers, download their PDFs, and persist per-paper + consolidated reports in
S3. The LLM steps (target profiling + per-citation analysis) live in the agent layer.

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
]
