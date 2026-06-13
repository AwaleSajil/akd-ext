"""Usage/corpus matplotlib charts rendered to in-memory base64 data URIs.

Ported from the standalone ``plots_usage.py``; instead of writing PNGs to a figures
directory, each chart is encoded as a ``data:image/png;base64,...`` URI and returned in a
``{filename: data_uri}`` dict. The markdown still references ``figures/<name>.png`` and the
renderer swaps those for the data URIs (keeps the section/markdown code identical to source).
"""

from __future__ import annotations

import base64
import io
from collections import Counter
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

_SKIP_USAGE_LABELS = frozenset({"unclear", "unknown"})


def _humanize_axis_label(s: str) -> str:
    s = str(s).strip().lower().replace("-", "_")
    parts = [p for p in s.split("_") if p]
    if not parts:
        return s
    return " ".join(p.capitalize() for p in parts)


def _annotate_vertical_bars(ax, bars, *, fontsize: int = 9) -> None:
    for bar in bars:
        h = float(bar.get_height())
        if h <= 0:
            continue
        x = float(bar.get_x() + bar.get_width() / 2.0)
        ax.text(x, h, f"{int(h)}", ha="center", va="bottom", fontsize=fontsize)


def _annotate_horizontal_bars(ax, bars, *, fontsize: int = 9) -> None:
    xmax = max((float(b.get_width()) for b in bars), default=0.0)
    pad = max(0.02 * xmax, 0.5)
    for bar in bars:
        w = float(bar.get_width())
        if w < 0:
            continue
        y = float(bar.get_y() + bar.get_height() / 2.0)
        ax.text(w + pad, y, f"{int(w)}", ha="left", va="center", fontsize=fontsize)


def _fig_to_data_uri(fig, *, dpi: int = 180) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def generate_usage_figures(summary: dict[str, Any], rollup: list[dict[str, Any]]) -> dict[str, str]:
    """Return {figure_filename: data_uri} for the usage/corpus charts that have data."""
    figs: dict[str, str] = {}

    # 1) usage_type_primary bar (omit unclear / unknown)
    counts = summary.get("usage_type_primary_counts") or {}
    if counts:
        filtered = {
            k: int(v)
            for k, v in counts.items()
            if str(k).strip().lower() not in _SKIP_USAGE_LABELS
        }
        if filtered:
            items = sorted(filtered.items(), key=lambda kv: (-kv[1], kv[0]))
            y_labels = [_humanize_axis_label(str(k)) for k, _ in items]
            vals = [v for _, v in items]
            fig, ax = plt.subplots(figsize=(10, 5))
            bars = ax.barh(y_labels[::-1], vals[::-1], color="steelblue")
            _annotate_horizontal_bars(ax, bars, fontsize=9)
            ax.set_xlabel("Papers")
            ax.set_title("Prithvi usage (primary label)")
            fig.tight_layout()
            figs["usage_type_primary.png"] = _fig_to_data_uri(fig)

    # 2) finetune / benchmark / contextual mention counts
    fig, ax = plt.subplots(figsize=(7, 4))
    cats_raw = ["finetune_signal", "benchmark_signal", "contextual_mention_only"]
    cats_disp = [_humanize_axis_label(c) for c in cats_raw]
    vals2 = [
        int(summary.get("n_finetune_signal", 0) or 0),
        int(summary.get("n_benchmark_signal", 0) or 0),
        int(summary.get("n_contextual_mention_only", 0) or 0),
    ]
    bars2 = ax.bar(cats_disp, vals2, color=["#c44e52", "#8172b3", "#4c72b0"])
    _annotate_vertical_bars(ax, bars2, fontsize=10)
    ax.set_ylabel("Paper count")
    ax.set_title("Derived usage signals (non-exclusive)")
    plt.xticks(rotation=15, ha="right")
    fig.tight_layout()
    figs["usage_signals.png"] = _fig_to_data_uri(fig)

    # 3) where_mentioned multi-label frequency
    sec_counter: Counter[str] = Counter()
    for r in rollup:
        for s in r.get("where_mentioned") or []:
            sec_counter[str(s).lower()] += 1
    if sec_counter:
        items = sec_counter.most_common(15)
        labels3 = [_humanize_axis_label(k) for k, _ in items]
        vals3 = [int(v) for _, v in items]
        fig, ax = plt.subplots(figsize=(9, 4))
        bars3 = ax.bar(labels3, vals3, color="#55a868")
        _annotate_vertical_bars(ax, bars3, fontsize=9)
        ax.set_ylabel("Mentions (papers can have multiple)")
        ax.set_title("Section tags: where Prithvi is mentioned")
        plt.xticks(rotation=35, ha="right")
        fig.tight_layout()
        figs["where_mentioned.png"] = _fig_to_data_uri(fig)

    # 4) year distribution if year present
    years = [r.get("year") for r in rollup if isinstance(r.get("year"), int)]
    if years:
        yc = Counter(years)
        xs = sorted(yc.keys())
        ys = [int(yc[x]) for x in xs]
        fig, ax = plt.subplots(figsize=(8, 4))
        xtick = [str(x) for x in xs]
        bars4 = ax.bar(xtick, ys, color="#ccb974")
        _annotate_vertical_bars(ax, bars4, fontsize=9)
        ax.set_xlabel("Year (meta)")
        ax.set_ylabel("Papers")
        ax.set_title("Papers by publication year (metadata)")
        fig.tight_layout()
        figs["papers_by_year.png"] = _fig_to_data_uri(fig)

    return figs
