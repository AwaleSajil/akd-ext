"""Environment-driven config for the citation-report tools.

Reads the same env vars the standalone package used. On akd-ext / FastMCP deployments
these come from the platform's environment (or a local .env loaded by the app).
"""

from __future__ import annotations

import os

from .s3_cache import S3Cache


def s2_api_key() -> str | None:
    return (os.getenv("SEMANTIC_SCHOLAR_API_KEY") or "").strip() or None


def openai_api_key() -> str | None:
    return (os.getenv("OPENAI_API_KEY") or "").strip() or None


def analyzer_model() -> str:
    return (os.getenv("ANALYZER_MODEL") or "gpt-5.5").strip()


def unpaywall_email() -> str:
    return (os.getenv("UNPAYWALL_EMAIL") or "user@example.com").strip()


def min_interval_s() -> float:
    return float(os.getenv("SEMANTIC_SCHOLAR_MIN_INTERVAL_S", "2") or "2")


def get_cache() -> S3Cache:
    """Build an S3Cache from FM_S3_* / AWS_* env vars."""
    return S3Cache(
        bucket=(os.getenv("FM_S3_BUCKET") or "").strip() or None,
        prefix=(os.getenv("FM_S3_PREFIX") or "fm-report-cache").strip().strip("/"),
        region=(os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "").strip() or None,
        profile=(os.getenv("AWS_PROFILE") or "").strip() or None,
    )
