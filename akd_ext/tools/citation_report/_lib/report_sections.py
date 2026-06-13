"""Deterministic Markdown blocks for the master citation report.

Ported from the standalone ``report_sections.py``. Parameterized so the report is not
hardcoded to Prithvi: ``title`` / ``objective`` / ``source_desc`` are passed in. The HF
download-trends section is supplied by the caller (Tool B) or rendered as a skipped note.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any


def utc_report_datetime() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z"), now.strftime("%Y-%m-%d (UTC)")


DEFAULT_TITLE = "Prithvi in the research literature: usage, adaptation, and community traction"

DEFAULT_OBJECTIVE = (
    "Summarize how peer-reviewed and preprint literature cites and uses NASA–IBM **Prithvi** "
    "foundation models (EO and WxC variants), including contextual mentions, benchmarking, "
    "and adaptation to downstream tasks, datasets, or resolutions. Combine **deterministic** "
    "counts from per-paper JSON reports with a concise narrative and **Hugging Face** download "
    "trends from archived `metrics_daily` snapshots."
)


def _humanize_token(s: str) -> str:
    parts = [p for p in str(s).strip().lower().replace("-", "_").split("_") if p]
    return " ".join(p.capitalize() for p in parts) if parts else str(s)


def _md_cell(s: str, *, max_len: int = 420) -> str:
    t = str(s).replace("\n", " ").replace("|", "\\|").strip()
    if len(t) > max_len:
        t = t[: max_len - 1] + "…"
    return t or "—"


def build_cover_markdown(
    *,
    title: str,
    report_date_line: str,
    report_generated_iso: str,
    source_desc: str,
    n_papers: int,
    n_errors: int,
    year_min: int | None,
    year_max: int | None,
    objective: str = DEFAULT_OBJECTIVE,
) -> str:
    corpus_years = ""
    if year_min is not None and year_max is not None:
        corpus_years = f"Metadata years in corpus span **{year_min}**–**{year_max}** (where year was present).\n\n"

    return f"""## Report information

| Field | Value |
|-------|-------|
| **Report title** | {html.escape(title)} |
| **Report date** | {html.escape(report_date_line)} |
| **Report generated (UTC)** | `{html.escape(report_generated_iso)}` |
| **Primary inputs** | {html.escape(source_desc)} |

### Objective of this report

{objective}

### Introduction

This document synthesizes machine-generated **per-paper** analyses of downloaded PDFs that cite or discuss the seed model. Counts, tables, and ranked quotes are **deterministic** from that JSON unless explicitly labeled as narrative. Figures in the next sections are explained inline with captions.

{corpus_years}### Corpus (papers read)

- **Papers with JSON reports included**: {n_papers}
- **Files that failed to parse**: {n_errors}
- Each table row or figure reflects one paper-level JSON record (titles and heuristics only—internal Semantic Scholar ids are omitted from this document).

---
"""


def rank_impactful_statements(
    rollup: list[dict[str, Any]],
    *,
    max_n: int = 15,
    min_statement_len: int = 28,
) -> list[dict[str, Any]]:
    """Rank by parent-paper confidence then statement length; de-duplicate statement text."""
    scored: list[tuple[float, int, dict[str, Any], str, dict[str, Any]]] = []
    for r in rollup:
        conf = float(r.get("confidence") or 0.0)
        for s in r.get("impactful_statements") or []:
            if not isinstance(s, dict):
                continue
            st = str(s.get("statement") or "").strip()
            if len(st) < min_statement_len:
                continue
            scored.append((conf, len(st), r, st, s))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for conf, _ln, r, st, s in scored:
        key = st[:220].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "paper_id": r.get("paper_id"),
                "title": r.get("title"),
                "confidence": conf,
                "statement": st,
                "evidence_quote": str(s.get("evidence_quote") or "").strip()[:500],
            }
        )
        if len(out) >= max_n:
            break
    return out


def build_impactful_statements_section(ranked: list[dict[str, Any]]) -> str:
    lines = [
        "## Top impactful statements (value-oriented)",
        "",
        "The following **10–15** statements are ranked from per-paper JSON (`impactful_statements`), "
        "ordered by extraction confidence and statement length. **Paper titles** identify the source; "
        "internal ids are omitted.",
        "",
    ]
    for i, item in enumerate(ranked, start=1):
        tit = str(item.get("title") or "Untitled")
        st = str(item.get("statement") or "")
        ev = str(item.get("evidence_quote") or "")
        cf = item.get("confidence")
        cf_s = f"{float(cf):.2f}" if isinstance(cf, (int, float)) else "—"
        lines.append(f"### {i}. (model confidence {cf_s})")
        lines.append("")
        lines.append(f"**Source:** {tit}")
        lines.append("")
        lines.append(f"> {st}")
        lines.append("")
        if ev:
            lines.append(f"*Evidence excerpt:* {ev}")
            lines.append("")
    return "\n".join(lines)


def _usage_counts_display(summary: dict[str, Any]) -> str:
    c = summary.get("usage_type_primary_counts") or {}
    parts = []
    for k, v in sorted(c.items(), key=lambda kv: (-int(kv[1] or 0), str(kv[0]))):
        if str(k).lower() in ("unclear", "unknown"):
            continue
        parts.append(f"{_humanize_token(str(k))}: **{int(v)}**")
    return "; ".join(parts) if parts else "—"


def metrics_summary_to_markdown_table(summary: dict[str, Any]) -> str:
    s = summary
    lines = [
        "## Deterministic metrics (from report_outputs JSON)",
        "",
        f"- **Papers parsed**: {s.get('n_papers', 0)}",
        f"- **Parse errors**: {s.get('n_errors', 0)}",
        f"- **Finetune signal (any)**: {s.get('n_finetune_signal', 0)}",
        f"- **Benchmark signal (any)**: {s.get('n_benchmark_signal', 0)}",
        f"- **Contextual mention only** (mention-only + sections ⊆ abstract/intro/related_work/appendix): {s.get('n_contextual_mention_only', 0)}",
        "",
        "### usage_type_primary counts",
        "",
        "| label | count |",
        "|-------|-------|",
    ]
    for k, v in (s.get("usage_type_primary_counts") or {}).items():
        lines.append(f"| `{html.escape(str(k))}` | {v} |")
    lines.append("")
    return "\n".join(lines)


def build_usage_corpus_figures_section(
    summary: dict[str, Any],
    figure_names: set[str],
) -> str:
    """One subsection per figure that was produced (``figure_names`` = available filenames)."""
    n = int(summary.get("n_papers") or 0)
    nft = int(summary.get("n_finetune_signal") or 0)
    nbm = int(summary.get("n_benchmark_signal") or 0)
    nctx = int(summary.get("n_contextual_mention_only") or 0)

    blocks: list[str] = [
        "## Citation usage and corpus: figures",
        "",
        "This section pairs each chart with a short interpretation. All counts come from the same "
        f"rollup as the summary table (**{n}** papers). Aggregate signals: finetune **{nft}**, "
        f"benchmark **{nbm}**, contextual mention-only **{nctx}**.",
        "",
    ]

    specs: list[tuple[str, str, str, str]] = [
        (
            "usage_type_primary.png",
            "Primary usage label (excluding unclear / unknown)",
            (
                "Each bar counts papers by the automated **primary usage** label. Labels such as "
                "**Mention Only** versus **Fine Tune** or **Benchmark** summarize how the seed model appears "
                "in the extracted text. Heuristic labels can be wrong on individual papers."
            ),
            (
                "*Caption — Primary usage distribution.* Bars show paper counts by label. "
                f"Other labels in the corpus (including omitted unclear/unknown): {_usage_counts_display(summary)}."
            ),
        ),
        (
            "usage_signals.png",
            "Derived non-exclusive usage signals",
            (
                "These three bars are **not mutually exclusive**: a single paper can contribute to more than one. "
                "They summarize finetune-related flags, benchmark-related flags, and the stricter "
                "**contextual mention-only** bucket (mention-only with section tags limited to abstract, "
                "introduction, related work, or appendix)."
            ),
            "*Caption — Finetune signal, benchmark signal, and contextual mention-only counts.*",
        ),
        (
            "where_mentioned.png",
            "Where the seed model is mentioned in the paper (section tags)",
            (
                "When the JSON includes `where_mentioned`, we tally section tags across papers "
                "(a paper can contribute to multiple tags). High counts for **Related Work** often "
                "indicate citation-driven visibility rather than experimental use."
            ),
            "*Caption — Frequency of section tags for mentions (multi-label per paper possible).*",
        ),
        (
            "papers_by_year.png",
            "Corpus coverage by publication year (metadata)",
            (
                "Year is taken from report metadata when present; missing years are omitted from this chart. "
                "Use this as a coarse view of how the downloaded citation set spans time—not as a complete "
                "bibliometric census."
            ),
            "*Caption — Number of papers in the JSON corpus by stated publication year.*",
        ),
    ]

    fig_i = 0
    for fname, subh, writeup, caption in specs:
        if fname not in figure_names:
            continue
        fig_i += 1
        blocks.append(f"### Figure {fig_i}. {subh}")
        blocks.append("")
        blocks.append(writeup)
        blocks.append("")
        blocks.append(f"![{subh}](figures/{fname})")
        blocks.append("")
        blocks.append(caption)
        blocks.append("")
    if fig_i == 0:
        blocks.append("*No usage corpus charts were produced for this corpus.*")
        blocks.append("")
    return "\n".join(blocks)


def build_adaptation_table_md(rollup: list[dict[str, Any]]) -> str:
    rows = [
        r
        for r in rollup
        if r.get("is_finetune") and str(r.get("usage_type_primary") or "").lower() != "benchmark"
    ]
    lines = [
        "## Adaptation to other datasets, resolutions, or tasks",
        "",
        "Papers where the JSON pipeline flagged **fine-tuning / adaptation** (including primary labels "
        "`fine_tune` / `adapted`, structured fine-tuning blocks, or training-interaction flags). "
        "This is **not** a manual audit. **Internal ids are omitted**; use title + year to locate papers.",
        "",
        f"**Rows in table:** {len(rows)}",
        "",
    ]
    if not rows:
        lines.append("*No papers matched adaptation/fine-tuning heuristics in the current rollup.*")
        lines.append("")
        return "\n".join(lines)
    lines.append("| Title | Year | Primary label | Method (if any) | Downstream / dataset | Summary |")
    lines.append("| --- | ---: | --- | --- | --- | --- |")
    for r in sorted(rows, key=lambda x: (str(x.get("title") or "").lower())):
        tit = _md_cell(str(r.get("title") or "—"), max_len=120)
        yr = r.get("year")
        yrc = str(int(yr)) if isinstance(yr, int) else "—"
        pl = _md_cell(_humanize_token(str(r.get("usage_type_primary") or "—")))
        meth = _md_cell(str(r.get("finetuning_method") or "—"), max_len=80)
        ds = r.get("downstream_tasks") or []
        ds_s = _md_cell(", ".join(str(x) for x in ds[:6]) if ds else "—", max_len=120)
        summ = _md_cell(str(r.get("usage_summary") or "—"), max_len=280)
        lines.append(f"| {tit} | {yrc} | {pl} | {meth} | {ds_s} | {summ} |")
    lines.append("")
    return "\n".join(lines)


def build_benchmark_table_md(rollup: list[dict[str, Any]]) -> str:
    rows = [r for r in rollup if r.get("is_benchmark")]
    lines = [
        "## Benchmarks involving the seed model",
        "",
        "Papers where the JSON pipeline flagged **benchmarking** (primary label `benchmark`, structured "
        "benchmark entries, or evaluation metadata). **Internal ids are omitted.**",
        "",
        f"**Rows in table:** {len(rows)}",
        "",
    ]
    if not rows:
        lines.append("*No papers matched benchmark heuristics in the current rollup.*")
        lines.append("")
        return "\n".join(lines)
    lines.append(
        "| Title | Year | Primary label | Benchmark detail (from JSON) | Reported improvement | Summary |"
    )
    lines.append("| --- | ---: | --- | --- | --- | --- |")
    for r in sorted(rows, key=lambda x: (str(x.get("title") or "").lower())):
        tit = _md_cell(str(r.get("title") or "—"), max_len=120)
        yr = r.get("year")
        yrc = str(int(yr)) if isinstance(yr, int) else "—"
        pl = _md_cell(_humanize_token(str(r.get("usage_type_primary") or "—")))
        bsn = r.get("benchmark_snippets") or []
        btxt = _md_cell(" · ".join(str(x) for x in bsn[:3]) if bsn else "—", max_len=220)
        repi = _md_cell(str(r.get("reported_improvement") or "—"), max_len=40)
        summ = _md_cell(str(r.get("usage_summary") or "—"), max_len=220)
        lines.append(f"| {tit} | {yrc} | {pl} | {btxt} | {repi} | {summ} |")
    lines.append("")
    return "\n".join(lines)


def stub_narrative() -> str:
    return """## Executive summary (stub)

The narrative step was not executed. Figures, the metrics table, and impactful-statement excerpts above are **deterministic** from the per-paper JSON corpus.

## Methodology (stub)

Enable the optional narrative step to replace these stubs with generated prose.

## Conclusions (stub)

Use the adaptation and benchmark **tables** after this section until the narrative step is enabled.
"""


def hf_skipped_md() -> str:
    return (
        "## Hugging Face: model download trends (monthly)\n\n"
        "**HF download trends were not included in this run.** Provide HF monthly-download "
        "figures (via the HF trends tool) to populate this section.\n"
    )


def assemble_full_report_markdown(
    *,
    title: str,
    cover_md: str,
    usage_figures_md: str,
    hf_figures_md: str,
    metrics_md: str,
    impactful_md: str,
    narrative_md: str,
    adaptation_md: str,
    benchmark_md: str,
) -> str:
    parts = [
        f"# {title}",
        "",
        cover_md.strip(),
        "",
        usage_figures_md.strip(),
        "",
        hf_figures_md.strip(),
        "",
        "---",
        "",
        metrics_md.strip(),
        "",
        impactful_md.strip(),
        "",
        "## Narrative analysis",
        "",
        narrative_md.strip(),
        "",
        "---",
        "",
        adaptation_md.strip(),
        "",
        benchmark_md.strip(),
        "",
    ]
    return "\n".join(parts)
