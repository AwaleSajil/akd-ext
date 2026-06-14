"""S3-backed caches: citation lists, PDFs, and per-paper reports.

Three layers, three policies (see DESIGN.md §4):
  - citations: {prefix}/citations/{paper_id}/all_citations.json (+ meta.json)
  - pdfs:      {prefix}/pdfs/{paper_cache_key}.pdf
  - reports:   {prefix}/reports/{target}/{model}/{prompt_ver}/{paper_cache_key}.json

If no bucket is configured the cache is "disabled": reads miss, writes no-op, so the
pipeline still runs against local files only. AWS credentials come from the standard
boto3 chain (the AWS_* vars in .env are picked up automatically).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import boto3
    from botocore.exceptions import ClientError
except Exception:  # pragma: no cover - boto3 is a hard dep, but degrade gracefully
    boto3 = None
    ClientError = Exception


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_doi_filename(doi: str) -> str:
    """Filesystem/-key-safe rendering of a DOI (matches the existing downloader)."""
    return re.sub(r'[<>:"/\\|?*]', "_", doi)[:200]


def paper_cache_key(external_ids: dict | None) -> str | None:
    """Content key for a paper, shared by the local filename and S3 key.

    ArXiv id (slashes -> underscores) preferred; else cleaned DOI. None if neither.
    """
    ids = external_ids or {}
    arxiv = ids.get("ArXiv") or ids.get("arXiv") or ids.get("arxiv")
    if arxiv:
        return str(arxiv).replace("/", "_")
    doi = ids.get("DOI") or ids.get("doi")
    if doi:
        return clean_doi_filename(str(doi))
    return None


def short_hash(*parts: str, n: int = 12) -> str:
    h = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return h[:n]


class S3Cache:
    def __init__(self, bucket: str | None, prefix: str = "fm-report-cache",
                 region: str | None = None, profile: str | None = None):
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.region = region
        self.profile = profile
        self._client = None

    # ── plumbing ──────────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return bool(self.bucket) and boto3 is not None

    @property
    def client(self):
        if self._client is None:
            if not self.enabled:
                raise RuntimeError("S3 cache is disabled (no bucket configured).")
            # Use an explicit Session so an SSO/named profile is honored and its
            # short-lived credentials are auto-refreshed from the SSO cache. Falls
            # back to the default credential chain when no profile is set.
            session = boto3.Session(profile_name=self.profile) if self.profile else boto3.Session()
            self._client = session.client("s3", region_name=self.region)
        return self._client

    def check_credentials(self) -> dict:
        """Cheap liveness probe: confirm S3 is reachable with current creds.

        Returns {"ok": True} on success, else {"ok": False, "error": ..., "hint": ...}.
        Used at server boot so an expired/missing token surfaces as a clear message
        instead of a raw traceback on the first tool call.
        """
        if not self.enabled:
            return {"ok": False, "error": "S3 disabled (FM_S3_BUCKET not set)",
                    "hint": "Set FM_S3_BUCKET and AWS credentials."}
        try:
            self.client.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix, MaxKeys=1)
            return {"ok": True}
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code", type(e).__name__)
            hint = (
                "AWS credentials expired — refresh them. Local: `aws sso login "
                "--profile akd && aws configure export-credentials --profile akd "
                "--format env` then update .env (or set AWS_PROFILE=akd)."
                if code in ("ExpiredToken", "ExpiredTokenException", "InvalidToken")
                else "Check AWS credentials / bucket name / region."
            )
            return {"ok": False, "error": f"{code}: {e}", "hint": hint}

    def _key(self, *parts: str) -> str:
        return "/".join([self.prefix, *[p.strip("/") for p in parts]])

    def s3_uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def object_exists(self, key: str) -> bool:
        """Public HEAD check for an arbitrary key in this bucket."""
        return self._exists(key)

    def key_from_uri(self, uri_or_key: str) -> str | None:
        """Normalize an ``s3://bucket/key`` URI or a bare key to a key in THIS bucket.

        Returns the key, or None if the URI names a different bucket (rejected so we
        only ever presign objects in our own bucket).
        """
        s = (uri_or_key or "").strip()
        if not s:
            return None
        if s.startswith("s3://"):
            rest = s[len("s3://"):]
            bucket, _, key = rest.partition("/")
            if bucket != self.bucket or not key:
                return None
            return key
        return s.lstrip("/")

    def _exists(self, key: str) -> bool:
        if not self.enabled:
            return False
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def _get_json(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return json.loads(obj["Body"].read().decode("utf-8", "replace"))

    def _put_json(self, key: str, data: Any) -> None:
        if not self.enabled:
            return
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(data, indent=2).encode("utf-8"),
            ContentType="application/json",
        )

    # ── citations ─────────────────────────────────────────────────────────────

    def _citation_key(self, paper_id: str) -> str:
        return self._key("citations", paper_id, "all_citations.json")

    def _citation_meta_key(self, paper_id: str) -> str:
        return self._key("citations", paper_id, "meta.json")

    def citation_cache_meta(self, paper_id: str) -> dict | None:
        return self._get_json(self._citation_meta_key(paper_id))

    def citation_cache_get(self, paper_id: str) -> list[dict] | None:
        return self._get_json(self._citation_key(paper_id))

    def citation_cache_put(self, paper_id: str, records: list[dict], meta: dict | None = None) -> str | None:
        if not self.enabled:
            return None
        key = self._citation_key(paper_id)
        self._put_json(key, records)
        full_meta = {
            "paper_id": paper_id,
            "fetched_at": _utc_now_iso(),
            "record_count": len(records),
            **(meta or {}),
        }
        self._put_json(self._citation_meta_key(paper_id), full_meta)
        return self.s3_uri(key)

    def citation_cache_uri(self, paper_id: str) -> str:
        return self.s3_uri(self._citation_key(paper_id))

    # ── hugging face metrics (read-only) ──────────────────────────────────────
    # Daily snapshot CSVs uploaded out-of-band under
    #   {prefix}/Hugging_Face_Metrics/metrics_daily/YYYY/MM/<ts>/hf_downloads_last_month_*.csv
    # kinds: all_repos | models_<org> | datasets_<org>. We only read them.

    def _hf_metrics_prefix(self) -> str:
        return self._key("Hugging_Face_Metrics", "metrics_daily") + "/"

    def hf_list_snapshot_keys(self, kind: str, org: str | None = None) -> list[str]:
        """Keys of every snapshot CSV of the given kind (and org for models/datasets)."""
        if not self.enabled:
            return []
        if kind == "all_repos":
            needle = "hf_downloads_last_month_all_repos_"
        else:
            needle = f"hf_downloads_last_month_{kind}_{org}_"
        prefix = self._hf_metrics_prefix()
        keys: list[str] = []
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kw)
            for obj in resp.get("Contents", []):
                name = obj["Key"].rsplit("/", 1)[-1]
                if name.startswith(needle) and name.endswith(".csv"):
                    keys.append(obj["Key"])
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken")
            else:
                break
        return keys

    def hf_read_csv(self, key: str) -> bytes:
        """Raw bytes of one snapshot CSV (caller parses with pandas)."""
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    # ── pdfs ──────────────────────────────────────────────────────────────────

    def _pdf_key(self, cache_key: str) -> str:
        return self._key("pdfs", f"{cache_key}.pdf")

    def pdf_cache_exists(self, cache_key: str) -> bool:
        return self._exists(self._pdf_key(cache_key))

    def pdf_cache_get(self, cache_key: str, dest_path: str | Path) -> bool:
        if not self.enabled:
            return False
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.client.download_file(self.bucket, self._pdf_key(cache_key), str(dest))
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound", "403"):
                return False
            raise

    def pdf_cache_put(self, cache_key: str, local_path: str | Path, source: str = "") -> None:
        if not self.enabled:
            return
        self.client.upload_file(
            str(local_path), self.bucket, self._pdf_key(cache_key),
            ExtraArgs={"ContentType": "application/pdf"},
        )
        if source:
            self._put_json(
                self._key("pdfs", f"{cache_key}.meta.json"),
                {"source": source, "uploaded_at": _utc_now_iso()},
            )

    # ── reports ───────────────────────────────────────────────────────────────

    def _report_key(self, target: str, model: str, prompt_ver: str, cache_key: str) -> str:
        return self._key("reports", target, model, prompt_ver, f"{cache_key}.json")

    def report_cache_get(self, target: str, model: str, prompt_ver: str, cache_key: str) -> dict | None:
        return self._get_json(self._report_key(target, model, prompt_ver, cache_key))

    def report_cache_put(self, target: str, model: str, prompt_ver: str, cache_key: str, report: dict) -> None:
        self._put_json(self._report_key(target, model, prompt_ver, cache_key), report)

    # ── agent-written reports (LLM call done by the agent, not the tool) ────────
    # Namespaced by target + a caller-supplied version label, since the tool does not
    # know which model/prompt produced the report. Layout:
    #   {prefix}/reports/{target}/{version}/{cache_key}.json

    def _agent_report_key(self, target: str, version: str, cache_key: str) -> str:
        return self._key("reports", target, version, f"{cache_key}.json")

    def agent_report_exists(self, target: str, version: str, cache_key: str) -> bool:
        return self._exists(self._agent_report_key(target, version, cache_key))

    def agent_report_get(self, target: str, version: str, cache_key: str) -> dict | None:
        return self._get_json(self._agent_report_key(target, version, cache_key))

    def agent_report_put(self, target: str, version: str, cache_key: str, report: dict) -> str | None:
        if not self.enabled:
            return None
        key = self._agent_report_key(target, version, cache_key)
        self._put_json(key, report)
        return self.s3_uri(key)

    def agent_report_list_keys(self, target: str, version: str) -> list[str]:
        """List cache_keys of every report under reports/{target}/{version}/.

        Excludes the master file (``_master.json``) and any non-".json" objects.
        """
        if not self.enabled:
            return []
        prefix = self._key("reports", target, version) + "/"
        keys: list[str] = []
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kw)
            for obj in resp.get("Contents", []):
                name = obj["Key"].rsplit("/", 1)[-1]
                if name.endswith(".json") and name != "_master.json":
                    keys.append(name[:-5])  # strip ".json"
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken")
            else:
                break
        return keys

    def master_report_put_v2(self, target: str, version: str, report: dict) -> str | None:
        """Write the consolidated master JSON to reports/{target}/{version}/_master.json."""
        if not self.enabled:
            return None
        key = self._key("reports", target, version, "_master.json")
        self._put_json(key, report)
        return self.s3_uri(key)

    def master_report_get_v2(self, target: str, version: str) -> dict | None:
        return self._get_json(self._key("reports", target, version, "_master.json"))

    def report_html_put(self, target: str, version: str, html_text: str, name: str = "_report.html") -> str | None:
        """Write a rendered HTML report to reports/{target}/{version}/{name}."""
        if not self.enabled:
            return None
        key = self._key("reports", target, version, name)
        self.upload_bytes(key, html_text.encode("utf-8"), content_type="text/html; charset=utf-8")
        return self.s3_uri(key)

    def master_report_key(self, target: str, version: str) -> str:
        return self._key("reports", target, version, "_master.json")

    def master_report_exists(self, target: str, version: str) -> bool:
        return self._exists(self.master_report_key(target, version))

    # ── master report + share bundle ─────────────────────────────────────────

    def master_report_put(self, target: str, model: str, prompt_ver: str, paper_id: str, report: dict) -> str | None:
        if not self.enabled:
            return None
        key = self._key("reports", target, model, prompt_ver, "_master", f"{paper_id}.json")
        self._put_json(key, report)
        return self.s3_uri(key)

    def upload_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def presign(self, key: str, expires_seconds: int, download_name: str | None = None) -> str:
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
        return self.client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=expires_seconds
        )

    def bundle_key(self, paper_id: str, timestamp: str) -> str:
        return self._key("bundles", paper_id, f"{timestamp}_bundle.zip")


def get_cache(settings) -> S3Cache:
    """Build an S3Cache from a Settings object."""
    return S3Cache(
        bucket=settings.s3_bucket,
        prefix=settings.s3_prefix,
        region=settings.aws_region,
        profile=getattr(settings, "aws_profile", None),
    )
