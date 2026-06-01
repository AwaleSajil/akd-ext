"""Presigned download links (NO LLM, nothing persisted).

A presigned URL is computed in memory from (bucket + key + creds + expiry) and
returned as a string. We never write the link anywhere — the object it points to
(e.g. the master JSON) already exists in S3 from a prior step.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .s3_cache import S3Cache

MAX_PRESIGN_SECONDS = 7 * 24 * 3600  # SigV4 hard cap


def _expires_at(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)) \
        .replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_download_link(
    cache: S3Cache,
    s3_uri_or_key: str,
    expires_days: int = 7,
    download_name: str | None = None,
) -> dict:
    """Presign ANY object in the bucket for browser download (generic, reusable).

    Accepts an ``s3://bucket/key`` URI or a bare key. Only objects in the configured
    bucket can be signed (a URI naming another bucket is rejected). Returns a URL
    string only — nothing is saved.

    Output: {status, url, expires_at, expires_seconds, s3_uri, message}. status:
    "ok" | "bad_uri" | "not_found" | "error".
    """
    if not cache.enabled:
        return {"status": "error", "url": None, "expires_at": None, "expires_seconds": None,
                "s3_uri": None, "message": "S3 is required for download links"}

    key = cache.key_from_uri(s3_uri_or_key)
    if not key:
        return {"status": "bad_uri", "url": None, "expires_at": None, "expires_seconds": None,
                "s3_uri": s3_uri_or_key,
                "message": f"Not a valid key/URI for bucket '{cache.bucket}'."}

    if not cache.object_exists(key):
        return {"status": "not_found", "url": None, "expires_at": None, "expires_seconds": None,
                "s3_uri": cache.s3_uri(key),
                "message": "Object does not exist in the bucket."}

    seconds = min(max(int(expires_days * 86400), 60), MAX_PRESIGN_SECONDS)
    name = download_name or key.rsplit("/", 1)[-1]
    url = cache.presign(key, seconds, download_name=name)

    return {
        "status": "ok",
        "url": url,
        "expires_at": _expires_at(seconds),
        "expires_seconds": seconds,
        "s3_uri": cache.s3_uri(key),
        "message": ("If signed with temporary STS credentials, this link stops "
                    "working when those credentials expire (often sooner than the "
                    "stated expiry)."),
    }


