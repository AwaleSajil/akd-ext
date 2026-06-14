"""Hugging Face download trends for a seed paper's target.

Given the profiler's ``target`` ({name, aliases, domain, ...}), find the matching HF
repo family in the daily-metrics snapshots, build a month-end cumulative-download series
per repo, and render one trend chart (returned as a base64 data URI ready to embed in the
HTML report). If nothing matches, ``get_hf_trends`` returns {"found": False} and the
report simply omits the HF section.

Data layout (local mirror and S3 share the same tree):
  Hugging_Face_Metrics/metrics_daily/YYYY/MM/<ts>/hf_downloads_last_month_<kind>_<...>.csv
  kinds: all_repos | models_<org> | datasets_<org>

Aggregation note: we take the LATEST snapshot per calendar month as that month's
month-end cumulative ``downloads_all_time`` — same series the Prithvi notebook plots, but
a handful of reads per org instead of every snapshot (matters for S3). Logic ported and
generalized from the standalone report_generation hf_prithvi_monthly_downloads.

``build_hf_section`` adapts a get_hf_trends result into the akd-ext report's
(markdown, {figure_name: data_uri}) figure convention (see report_logic).
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Protocol

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

# ── snapshot readers (local dir or S3) ─────────────────────────────────────────

_TS_RE = re.compile(r"_(\d{14})\.csv$")


def _parse_ts(name: str) -> pd.Timestamp | None:
    m = _TS_RE.search(name)
    if not m:
        return None
    return pd.to_datetime(m.group(1), format="%Y%m%d%H%M%S", utc=True)


def _filename_glob(kind: str, org: str | None) -> str:
    if kind == "all_repos":
        return "hf_downloads_last_month_all_repos_*.csv"
    return f"hf_downloads_last_month_{kind}_{org}_*.csv"


class SnapshotReader(Protocol):
    """Lists snapshot CSVs (by kind/org) and reads them into DataFrames.

    ``list_handles`` returns [(timestamp, handle)]; ``read`` turns a handle into a frame.
    A handle is a Path (local) or an S3 key (s3) — opaque to the caller.
    """

    def list_handles(self, kind: str, org: str | None = None) -> list[tuple[pd.Timestamp, object]]: ...

    def read(self, handle: object) -> pd.DataFrame: ...


class LocalReader:
    """Reads from a local ``metrics_daily`` directory (offline tests)."""

    def __init__(self, metrics_daily_dir: str | Path):
        self.root = Path(metrics_daily_dir).expanduser().resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"metrics_daily dir not found: {self.root}")

    def list_handles(self, kind: str, org: str | None = None) -> list[tuple[pd.Timestamp, object]]:
        out: list[tuple[pd.Timestamp, object]] = []
        for f in self.root.glob(f"**/{_filename_glob(kind, org)}"):
            ts = _parse_ts(f.name)
            if ts is not None:
                out.append((ts, f))
        return sorted(out, key=lambda t: t[0])

    def read(self, handle: object) -> pd.DataFrame:
        return pd.read_csv(handle)  # type: ignore[arg-type]


class S3Reader:
    """Reads snapshot CSVs from S3 via the project's S3Cache HF helpers."""

    def __init__(self, cache):
        self.cache = cache

    def list_handles(self, kind: str, org: str | None = None) -> list[tuple[pd.Timestamp, object]]:
        out: list[tuple[pd.Timestamp, object]] = []
        for key in self.cache.hf_list_snapshot_keys(kind, org=org):
            ts = _parse_ts(key.rsplit("/", 1)[-1])
            if ts is not None:
                out.append((ts, key))
        return sorted(out, key=lambda t: t[0])

    def read(self, handle: object) -> pd.DataFrame:
        return pd.read_csv(io.BytesIO(self.cache.hf_read_csv(handle)))  # type: ignore[arg-type]


# ── matching: target -> repo family ────────────────────────────────────────────

# Tokens that are too generic / structural to identify a model by name.
_STOP_TOKENS = {
    "the", "and", "for", "with", "model", "models", "dataset", "datasets",
    "base", "large", "small", "tiny", "mini", "version", "data", "bench",
    "benchmark", "nasa", "ibm", "geospatial", "ai4science", "impact",
    "foundation", "pretrained", "checkpoint", "weights",
}
_SIZE_RE = re.compile(r"^\d+[mbk]?$")  # 100m, 300m, 2300m, 7, ...


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _candidate_tokens(target: dict) -> set[str]:
    """Distinctive name tokens from the target's name + aliases."""
    toks: set[str] = set()
    parts = [target.get("name", "")] + list(target.get("aliases") or [])
    for p in parts:
        for t in _tokens(p):
            if len(t) >= 3 and t not in _STOP_TOKENS and not _SIZE_RE.match(t):
                toks.add(t)
    return toks


def match_target_to_repos(target: dict, all_repos: pd.DataFrame) -> list[dict]:
    """Return the HF repo family matching ``target`` (possibly empty).

    A repo matches if a distinctive target token appears in its post-slash name, or an
    alias equals the repo name / id exactly (scored higher). Returns one dict per repo:
    {repo_id, name, repo_type, org, score, matched_on}, best score first.
    """
    cand = _candidate_tokens(target)
    if not cand:
        return []

    alias_norms = {re.sub(r"[^a-z0-9]", "", a.lower()) for a in
                   ([target.get("name", "")] + list(target.get("aliases") or [])) if a}

    matches: list[dict] = []
    for _, row in all_repos.iterrows():
        repo_id = str(row.get("repo_id", ""))
        if not repo_id:
            continue
        name = str(row.get("name", "")) or repo_id.split("/", 1)[-1]
        name_toks = set(_tokens(name))
        name_norm = re.sub(r"[^a-z0-9]", "", name.lower())
        id_norm = re.sub(r"[^a-z0-9]", "", repo_id.lower())

        score, matched_on = 0.0, None
        if alias_norms & {name_norm, id_norm}:
            score, matched_on = 1.0, "exact"
        else:
            hits = sorted(t for t in cand if t in name_toks or t in name_norm)
            if hits:
                score, matched_on = 0.6, ",".join(hits)

        if score > 0:
            org = repo_id.split("/", 1)[0]
            matches.append({
                "repo_id": repo_id,
                "name": name,
                "repo_type": (str(row.get("repo_type")) if pd.notna(row.get("repo_type")) else None),
                "org": org,
                "score": score,
                "matched_on": matched_on,
            })

    matches.sort(key=lambda m: (-m["score"], m["repo_id"]))
    return matches


# ── aggregation: month-end cumulative downloads per repo ────────────────────────

def load_all_repos_latest(reader: SnapshotReader) -> pd.DataFrame:
    """Most recent ``all_repos`` snapshot as a DataFrame (raises if none found)."""
    handles = reader.list_handles("all_repos")
    if not handles:
        raise FileNotFoundError("no all_repos snapshots found")
    _, latest = handles[-1]
    return reader.read(latest)


def _latest_per_month(handles: list[tuple[pd.Timestamp, object]]) -> list[tuple[pd.Timestamp, object]]:
    by_month: dict[tuple[int, int], tuple[pd.Timestamp, object]] = {}
    for ts, h in handles:
        key = (ts.year, ts.month)
        if key not in by_month or ts > by_month[key][0]:
            by_month[key] = (ts, h)
    return [by_month[k] for k in sorted(by_month)]


def _kind_for(repo_type: str | None) -> str:
    return "datasets" if str(repo_type).lower() == "dataset" else "models"


def build_family_monthly(matches: list[dict], reader: SnapshotReader) -> dict[str, list[dict]]:
    """Month-end cumulative-download series for every matched repo.

    Reads the latest snapshot per calendar month ONCE per (org, kind) and extracts all
    matched repos' rows from it — so a 15-repo Prithvi family costs ~6 reads, not 6×15.
    Returns {repo_id: [{month, downloads_all_time, likes, downloads_last_30d}, ...]}.
    """
    # Group the repos we care about by the (org, kind) snapshot that holds them.
    groups: dict[tuple[str, str], set[str]] = {}
    for m in matches:
        groups.setdefault((m["org"], _kind_for(m["repo_type"])), set()).add(m["repo_id"])

    monthly: dict[str, list[dict]] = {}
    for (org, kind), repo_ids in groups.items():
        for ts, h in _latest_per_month(reader.list_handles(kind, org=org)):
            try:
                df = reader.read(h)
            except Exception:
                continue
            if "repo_id" not in df.columns:
                continue
            hit = df.loc[df["repo_id"].isin(repo_ids)]
            month = f"{ts.year:04d}-{ts.month:02d}"
            for _, r in hit.iterrows():
                dl = pd.to_numeric(r.get("downloads_all_time"), errors="coerce")
                if pd.isna(dl):
                    continue
                monthly.setdefault(str(r["repo_id"]), []).append({
                    "month": month,
                    "downloads_all_time": int(dl),
                    "likes": int(pd.to_numeric(r.get("likes"), errors="coerce") or 0),
                    "downloads_last_30d": int(pd.to_numeric(r.get("downloads_last_30d"), errors="coerce") or 0),
                })
    return monthly


# ── chart ──────────────────────────────────────────────────────────────────────

_PALETTE = ["tab:red", "tab:green", "tab:blue", "tab:orange", "tab:purple",
            "tab:brown", "tab:pink", "tab:olive", "tab:cyan", "tab:gray"]


def render_family_chart(series_by_repo: dict[str, list[dict]], target_name: str) -> bytes | None:
    """One line chart: month-end all-time downloads, one line per repo. PNG bytes."""
    series = {k: v for k, v in series_by_repo.items() if v}
    if not series:
        return None

    plt.rcParams.update({"font.size": 12})
    fig, ax = plt.subplots(figsize=(11, 6))
    for i, (repo_id, rows) in enumerate(sorted(series.items())):
        months = [r["month"] for r in rows]
        vals = [r["downloads_all_time"] for r in rows]
        label = repo_id.split("/", 1)[-1]
        ax.plot(months, vals, marker="o", linewidth=2, color=_PALETTE[i % len(_PALETTE)], label=label)
        ax.annotate(f"{vals[-1]:,}", xy=(months[-1], vals[-1]), xytext=(4, 4),
                    textcoords="offset points", fontsize=9, fontweight="bold")

    ax.set_title(f"{target_name} — Hugging Face downloads (all-time, month-end)", fontweight="bold")
    ax.set_xlabel("Month")
    ax.set_ylabel("All-time downloads")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=9, loc="upper left")
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(45)
        lbl.set_ha("right")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _variant_color(repo_id: str) -> str:
    """EO repos -> red, WxC -> green, else blue (matches the master report palette)."""
    low = repo_id.lower()
    if "wxc" in low or "weather" in low:
        return "tab:green"
    if "-eo" in low or "_eo" in low or "eo-" in low or "geospatial" in low:
        return "tab:red"
    return "tab:blue"


def render_repo_bar_chart(repo_id: str, rows: list[dict], repo_type: str | None = None) -> bytes | None:
    """One bar chart for a single repo: month-end cumulative all-time downloads. PNG bytes.

    Mirrors the master report's per-repo figure (one bar per month, value labels, EO/WxC
    colouring, dataset rows hatched).
    """
    rows = [r for r in rows if r]
    if not rows:
        return None

    months = [r["month"] for r in rows]
    vals = [r["downloads_all_time"] for r in rows]
    is_dataset = str(repo_type).lower() == "dataset"

    plt.rcParams.update({"font.size": 13})
    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(
        months, vals, width=0.65, color=_variant_color(repo_id), alpha=0.6,
        hatch=("///" if is_dataset else None),
        edgecolor=((0, 0, 0, 0.4) if is_dataset else None),
    )
    for b, v in zip(bars, vals):
        ax.annotate(f"{int(v):,}", xy=(b.get_x() + b.get_width() / 2, b.get_height()),
                    xytext=(0, 3), textcoords="offset points", ha="center",
                    fontsize=9, fontweight="bold")
    if vals:
        ax.set_ylim(min(vals) * 0.5, max(vals) * 1.12)

    ax.set_title(f"{repo_id} — month-end all-time downloads", fontweight="bold", fontsize=12)
    ax.set_xlabel("Month")
    ax.set_ylabel("All-time downloads")
    ax.grid(True, axis="y", alpha=0.3)
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(45)
        lbl.set_ha("right")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def png_to_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.standard_b64encode(png).decode("ascii")


# ── orchestrator ────────────────────────────────────────────────────────────────

def get_hf_trends(target: dict, reader: SnapshotReader, max_repos: int = 8) -> dict:
    """Find the HF repo family for ``target`` and build its download-trend chart.

    Returns {found, repos, monthly, latest, chart_data_uri, snapshot_month}. ``found`` is
    False (and the rest empty/None) when no repo matches the target. The chart and tables
    are capped to the ``max_repos`` most-downloaded repos so tiny task-head repos don't
    swamp the primary models.
    """
    empty = {"found": False, "repos": [], "monthly": {}, "latest": {},
             "chart_data_uri": None, "snapshot_month": None}

    all_repos = load_all_repos_latest(reader)
    matches = match_target_to_repos(target, all_repos)
    if not matches:
        return empty

    monthly_all = build_family_monthly(matches, reader)
    if not monthly_all:
        return empty

    # Rank by latest all-time downloads; keep the top max_repos for chart + tables.
    ranked = sorted(monthly_all, key=lambda rid: monthly_all[rid][-1]["downloads_all_time"], reverse=True)
    keep = ranked[:max_repos]
    monthly = {rid: monthly_all[rid] for rid in keep}

    by_id = {m["repo_id"]: m for m in matches}
    latest = {rid: {
        "downloads_all_time": rows[-1]["downloads_all_time"],
        "downloads_last_30d": rows[-1]["downloads_last_30d"],
        "likes": rows[-1]["likes"],
    } for rid, rows in monthly.items()}

    png = render_family_chart(monthly, str(target.get("name", "Target")))
    snapshot_month = max(r[-1]["month"] for r in monthly.values())
    return {
        "found": True,
        "repos": [by_id[rid] for rid in keep if rid in by_id],
        "monthly": monthly,
        "latest": latest,
        "chart_data_uri": png_to_data_uri(png) if png else None,
        "snapshot_month": snapshot_month,
    }


# ── report integration: (markdown, figures) in the akd-ext convention ───────────

def _hf_figure_name(target_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (target_name or "target").lower()).strip("-") or "target"
    return f"hf_{slug}_downloads.png"


def _repo_figure_name(repo_id: str) -> str:
    return f"hf_{re.sub(r'[^a-z0-9]+', '_', repo_id.lower()).strip('_')}_monthly_downloads.png"


def build_hf_section(hf: dict, target_name: str) -> tuple[str, dict[str, str]]:
    """Turn a ``get_hf_trends`` result into (hf_figures_md, figures).

    Renders ONE bar chart per matched repo (interleaved, Figure HF-1..N) — the layout of
    the master report — followed by a summary table. ``figures`` maps {figure_name:
    data_uri} and the markdown references each as ``figures/<figure_name>`` (the same
    mechanism the usage charts use; see report_logic.markdown_to_html_document). When
    ``hf`` is not found, returns the skipped-note markdown and an empty figures dict.
    """
    from .report_sections import hf_skipped_md

    if not hf or not hf.get("found"):
        return hf_skipped_md(), {}

    monthly = hf.get("monthly") or {}
    repos = {r["repo_id"]: r for r in hf.get("repos") or []}
    snap = hf.get("snapshot_month") or "?"

    # Render one figure per repo, most-downloaded first.
    ordered = sorted(monthly, key=lambda rid: (monthly[rid][-1]["downloads_all_time"] if monthly[rid] else 0),
                     reverse=True)
    figures: dict[str, str] = {}
    repo_figs: list[tuple[str, str]] = []  # (repo_id, figure_name)
    for rid in ordered:
        png = render_repo_bar_chart(rid, monthly[rid], repo_type=repos.get(rid, {}).get("repo_type"))
        if not png:
            continue
        fname = _repo_figure_name(rid)
        figures[fname] = png_to_data_uri(png)
        repo_figs.append((rid, fname))

    lines = [
        "## Hugging Face: model download trends (monthly)",
        "",
        f"Month-end **cumulative all-time downloads** for the Hugging Face repo family matching "
        f"**{target_name}**, computed from archived `metrics_daily` snapshots (latest snapshot per "
        f"calendar month; not a live API call). Latest snapshot: **{snap}**.",
        "",
    ]
    for i, (rid, fname) in enumerate(repo_figs, start=1):
        lines += [
            f"### Figure HF-{i}. `{rid}`",
            "",
            f"Month-by-month growth of **cumulative downloads** for **{rid}** across the snapshot "
            "history. Spacing of months reflects how often `metrics_daily` snapshots were collected; "
            "bar labels show the month-end totals.",
            "",
            f"![Monthly downloads: {rid}](figures/{fname})",
            "",
        ]

    latest = hf.get("latest") or {}
    if latest:
        lines += [
            "### Summary",
            "",
            "| Repo | All-time downloads | Last 30d | Likes | Matched on |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
        for rid, l in sorted(latest.items(), key=lambda kv: -(kv[1].get("downloads_all_time") or 0)):
            meta = repos.get(rid, {})
            lines.append(
                f"| `{rid}` | {int(l.get('downloads_all_time') or 0):,} "
                f"| {int(l.get('downloads_last_30d') or 0):,} "
                f"| {int(l.get('likes') or 0):,} | {meta.get('matched_on') or '—'} |"
            )
        lines.append("")

    return "\n".join(lines), figures
