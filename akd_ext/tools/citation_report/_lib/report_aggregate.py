"""Flatten master-JSON paper entries into a rollup + summary (NO LLM, in-memory).

Ported from the standalone FM_report_generation_agent ``aggregate.py``. The only change:
instead of reading a directory of per-paper ``*.json`` files, we consume the
``papers[]`` list of the consolidated master JSON produced by ConsolidateReportsTool.
Each master paper entry already carries ``report`` + ``meta`` (+ ``evidence``), which is
exactly the shape the original flattener expected.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

# intro-style contextual mention = abstract | introduction | related_work | appendix only.
EARLY_CONTEXT_SECTIONS = frozenset({"abstract", "introduction", "related_work", "appendix"})


def _truthy(v: Any) -> bool | None:
    if v is True:
        return True
    if v is False:
        return False
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "1"):
            return True
        if s in ("false", "no", "0", ""):
            return False
        if s == "unknown":
            return None
    return None


def _coerce_str(x: Any) -> str:
    if x is None:
        return ""
    return str(x).strip()


def _meta_paper_id(data: dict[str, Any]) -> str:
    m = data.get("meta") or {}
    for k in ("paper_id", "paperId"):
        v = _coerce_str(m.get(k))
        if v:
            return v
    return ""


def flatten_report(data: dict[str, Any]) -> dict[str, Any]:
    """One master ``papers[]`` entry -> one normalized rollup row."""
    report = data.get("report") or {}
    metrics = report.get("metrics") or {}
    ft = report.get("fine_tuning") or {}
    meta = data.get("meta") or {}

    usage_primary = _coerce_str(metrics.get("usage_type_primary") or report.get("usage_type")).lower() or "unclear"
    where = metrics.get("prithvi_presence") or {}
    where_list = [_coerce_str(x).lower() for x in (where.get("where_mentioned") or []) if _coerce_str(x)]

    ti = metrics.get("training_interaction") or {}
    fine_tuned_flag = _truthy(ti.get("fine_tuned"))
    full_ft = _truthy(ti.get("full_finetune"))
    lora = _truthy(ti.get("lora_adapter"))
    finetune_present = bool(ft.get("present")) if isinstance(ft.get("present"), bool) else _truthy(ft.get("present"))

    ev = metrics.get("evaluation") or {}
    benchmarked_vs = _truthy(ev.get("benchmarked_against_prithvi"))
    benches = report.get("benchmarks") if isinstance(report.get("benchmarks"), list) else []

    has_structured_benchmark = False
    for b in benches:
        if not isinstance(b, dict):
            continue
        if any(_coerce_str(b.get(k)) for k in ("task", "dataset", "metric", "result")):
            has_structured_benchmark = True
            break

    is_benchmark_usage = usage_primary == "benchmark" or benchmarked_vs is True or has_structured_benchmark

    benchmark_snippets: list[str] = []
    for b in benches:
        if not isinstance(b, dict):
            continue
        task = _coerce_str(b.get("task"))
        dataset = _coerce_str(b.get("dataset"))
        metric = _coerce_str(b.get("metric"))
        result = _coerce_str(b.get("result"))
        parts = [p for p in (task, dataset, metric, result) if p]
        if parts:
            benchmark_snippets.append(" — ".join(parts)[:400])
        if len(benchmark_snippets) >= 6:
            break

    is_finetune = (
        finetune_present is True
        or fine_tuned_flag is True
        or full_ft is True
        or lora is True
        or usage_primary == "fine_tune"
        or usage_primary == "adapted"
    )

    downstream = []
    dt = _coerce_str(ft.get("downstream_task"))
    if dt:
        downstream.append(dt)
    ds = _coerce_str(ft.get("dataset"))
    if ds:
        downstream.append(f"dataset:{ds}")
    for x in metrics.get("downstream_tasks") or []:
        s = _coerce_str(x)
        if s and s not in downstream:
            downstream.append(s)

    rs = metrics.get("results_signal") or {}
    improvements = ft.get("improvements") if isinstance(ft.get("improvements"), list) else []
    imp_vals = rs.get("improvement_values") if isinstance(rs.get("improvement_values"), list) else []

    contextual_mention = False
    if usage_primary == "mention_only" and not is_finetune and not is_benchmark_usage:
        if not where_list:
            contextual_mention = False  # unknown sections
        else:
            contextual_mention = all(w in EARLY_CONTEXT_SECTIONS for w in where_list)

    row = {
        "paper_id": _meta_paper_id(data),
        "title": _coerce_str(meta.get("title")) or _coerce_str(report.get("title")),
        "year": meta.get("year"),
        "url": _coerce_str(meta.get("url")) or None,
        "usage_type_primary": usage_primary,
        "usage_types_secondary": [
            _coerce_str(x).lower() for x in (metrics.get("usage_types_secondary") or []) if _coerce_str(x)
        ],
        "where_mentioned": where_list,
        "contextual_mention_only": contextual_mention,
        "is_finetune": is_finetune,
        "is_benchmark": is_benchmark_usage,
        "downstream_tasks": downstream,
        "improvement_values": imp_vals,
        "improvements_ft": improvements,
        "reported_improvement": _coerce_str(rs.get("reported_improvement")).lower(),
        "confidence": where.get("mention_count_estimate") if isinstance(where.get("mention_count_estimate"), (int, float)) else None,
        "evidence_quality": metrics.get("evidence_quality") or {},
        "usage_summary": _coerce_str(report.get("usage_summary")),
        "impactful_statements": report.get("impactful_statements") if isinstance(report.get("impactful_statements"), list) else [],
        "benchmark_snippets": benchmark_snippets,
        "finetuning_method": _coerce_str(ft.get("method")),
    }
    eq = row["evidence_quality"]
    if isinstance(eq, dict) and isinstance(eq.get("confidence"), (int, float)):
        row["confidence"] = float(eq["confidence"])
    return row


def build_rollup_and_summary(papers: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """master["papers"] -> (rollup rows, summary dict). Mirrors aggregate_reports()."""
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for i, entry in enumerate(papers or []):
        try:
            row = flatten_report(entry if isinstance(entry, dict) else {})
            if not row["paper_id"]:
                row["paper_id"] = _coerce_str((entry or {}).get("cache_key")) or f"paper_{i}"
            rows.append(row)
        except Exception as e:  # defensive: the agent owns the per-paper shape
            errors.append({"index": str(i), "error": str(e)})

    usage_counts = Counter(r["usage_type_primary"] for r in rows)
    finetune_n = sum(1 for r in rows if r["is_finetune"])
    bench_n = sum(1 for r in rows if r["is_benchmark"])
    ctx_n = sum(1 for r in rows if r["contextual_mention_only"])

    downstream_counter: Counter[str] = Counter()
    for r in rows:
        for d in r["downstream_tasks"]:
            downstream_counter[d] += 1

    years = [int(r["year"]) for r in rows if isinstance(r.get("year"), int)]

    def _brief(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "paper_id": r["paper_id"],
            "title": r["title"],
            "year": r.get("year"),
            "usage_type_primary": r.get("usage_type_primary"),
            "downstream_tasks": r.get("downstream_tasks"),
            "usage_summary": (r.get("usage_summary") or "")[:320],
        }

    summary = {
        "n_papers": len(rows),
        "n_errors": len(errors),
        "usage_type_primary_counts": dict(sorted(usage_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "n_finetune_signal": finetune_n,
        "n_benchmark_signal": bench_n,
        "n_contextual_mention_only": ctx_n,
        "top_downstream_tasks": dict(downstream_counter.most_common(25)),
        "corpus_year_min": min(years) if years else None,
        "corpus_year_max": max(years) if years else None,
        "adaptation_papers": [_brief(r) for r in rows if r["is_finetune"]][:40],
        "benchmark_papers": [_brief(r) for r in rows if r["is_benchmark"]][:40],
        "errors": errors[:50],
    }
    return rows, summary
