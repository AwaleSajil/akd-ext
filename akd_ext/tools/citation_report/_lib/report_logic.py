"""Orchestrate master JSON -> single portable HTML report (NO LLM).

Pulls the consolidated master from S3, flattens its ``papers[]`` into a rollup, renders
the deterministic charts to in-memory base64, assembles the markdown sections, and converts
to a self-contained HTML document (charts embedded as data URIs). Optionally writes the HTML
back to S3 and returns its URI.
"""

from __future__ import annotations

import html as _html
from typing import Any

import markdown

from .report_aggregate import build_rollup_and_summary
from .report_figures import generate_usage_figures
from .report_sections import (
    DEFAULT_OBJECTIVE,
    DEFAULT_TITLE,
    assemble_full_report_markdown,
    build_adaptation_table_md,
    build_benchmark_table_md,
    build_cover_markdown,
    build_impactful_statements_section,
    build_usage_corpus_figures_section,
    hf_skipped_md,
    metrics_summary_to_markdown_table,
    rank_impactful_statements,
    stub_narrative,
    utc_report_datetime,
)
from .s3_cache import S3Cache

_HTML_STYLE = """
    body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }
    img { max-width: 100%; height: auto; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #ccc; padding: 0.35rem 0.5rem; }
    code { background: #f4f4f4; padding: 0.1rem 0.3rem; }
"""


def markdown_to_html_document(md_text: str, *, figures: dict[str, str], title: str) -> str:
    """Convert markdown; replace ``figures/<name>`` refs with base64 data URIs (portable file)."""
    text = md_text
    for name, data_uri in figures.items():
        text = text.replace(f"](figures/{name})", f"]({data_uri})")

    body = markdown.markdown(text, extensions=["tables", "fenced_code", "nl2br"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{_html.escape(title)}</title>
  <style>{_HTML_STYLE}  </style>
</head>
<body>
{body}
</body>
</html>
"""


def render_report_html(
    master: dict[str, Any],
    *,
    title: str = DEFAULT_TITLE,
    objective: str = DEFAULT_OBJECTIVE,
    source_desc: str = "",
    hf_figures_md: str | None = None,
    hf_figures: dict[str, str] | None = None,
    narrative_md: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """master dict -> (html_document, summary). Pure: no I/O."""
    papers = master.get("papers") if isinstance(master.get("papers"), list) else []
    rollup, summary = build_rollup_and_summary(papers)

    figures = generate_usage_figures(summary, rollup)
    if hf_figures:
        figures = {**figures, **hf_figures}

    iso, date_line = utc_report_datetime()
    if not source_desc:
        seed = master.get("seed_paper_id") or "?"
        ver = master.get("version") or "?"
        source_desc = f"Consolidated master JSON (seed {seed}, version {ver})"

    cover = build_cover_markdown(
        title=title,
        report_date_line=date_line,
        report_generated_iso=iso,
        source_desc=source_desc,
        n_papers=int(summary.get("n_papers") or 0),
        n_errors=int(summary.get("n_errors") or 0),
        year_min=summary.get("corpus_year_min"),
        year_max=summary.get("corpus_year_max"),
        objective=objective,
    )
    metrics_md = metrics_summary_to_markdown_table(summary)
    ranked = rank_impactful_statements(rollup, max_n=15)
    impactful_md = build_impactful_statements_section(ranked)
    usage_fig_md = build_usage_corpus_figures_section(summary, set(figures.keys()))
    adaptation_md = build_adaptation_table_md(rollup)
    benchmark_md = build_benchmark_table_md(rollup)

    master_md = assemble_full_report_markdown(
        title=title,
        cover_md=cover,
        usage_figures_md=usage_fig_md,
        hf_figures_md=(hf_figures_md if hf_figures_md is not None else hf_skipped_md()),
        metrics_md=metrics_md,
        impactful_md=impactful_md,
        narrative_md=(narrative_md if narrative_md is not None else stub_narrative()),
        adaptation_md=adaptation_md,
        benchmark_md=benchmark_md,
    )
    html_doc = markdown_to_html_document(master_md, figures=figures, title=title)
    return html_doc, summary


def generate_report_io(
    cache: S3Cache,
    seed_paper_id: str,
    *,
    version: str = "v1",
    title: str = DEFAULT_TITLE,
    objective: str = DEFAULT_OBJECTIVE,
    store: bool = True,
    html_name: str = "_report.html",
    target: dict[str, Any] | None = None,
    include_hf_trends: bool = False,
) -> dict[str, Any]:
    """Load master from S3, render the HTML report, optionally store it back to S3.

    When ``include_hf_trends`` and a ``target`` are given, the Hugging Face download-trend
    section is populated by matching the target against the metrics_daily snapshots; any
    failure there is swallowed (the section degrades to a skipped note) so HF never breaks
    the core report.

    Output: {status, seed_paper_id, version, title, total_papers, n_errors, html_s3_uri,
    counts_by_usage_type, hf_found, message}. status: "ok" | "no_seed" | "no_master" |
    "error".
    """
    if not cache.enabled:
        return {"status": "error", "message": "S3 is required to generate the report"}
    if not seed_paper_id:
        return {"status": "no_seed", "message": "seed_paper_id is required"}

    master = cache.master_report_get_v2(seed_paper_id, version)
    if master is None:
        return {
            "status": "no_master",
            "seed_paper_id": seed_paper_id,
            "version": version,
            "message": (
                f"No consolidated master at reports/{seed_paper_id}/{version}/_master.json. "
                "Run consolidate_reports first."
            ),
        }

    hf_figures_md: str | None = None
    hf_figures: dict[str, str] | None = None
    hf_found = False
    if include_hf_trends and target:
        try:
            from .hf_trends_logic import S3Reader, build_hf_section, get_hf_trends

            hf = get_hf_trends(target, S3Reader(cache))
            hf_found = bool(hf.get("found"))
            hf_figures_md, hf_figures = build_hf_section(hf, str(target.get("name") or "Target"))
        except Exception:  # never let HF break the core report
            hf_figures_md, hf_figures, hf_found = None, None, False

    source_desc = f"Consolidated master JSON (seed {seed_paper_id}, version {version})"
    html_doc, summary = render_report_html(
        master, title=title, objective=objective, source_desc=source_desc,
        hf_figures_md=hf_figures_md, hf_figures=hf_figures,
    )

    html_s3_uri = None
    if store:
        html_s3_uri = cache.report_html_put(seed_paper_id, version, html_doc, name=html_name)

    return {
        "status": "ok",
        "seed_paper_id": seed_paper_id,
        "version": version,
        "title": title,
        "total_papers": int(summary.get("n_papers") or 0),
        "n_errors": int(summary.get("n_errors") or 0),
        "counts_by_usage_type": summary.get("usage_type_primary_counts") or {},
        "html_s3_uri": html_s3_uri,
        "html_bytes": len(html_doc.encode("utf-8")),
        "hf_found": hf_found,
        "message": None,
    }
