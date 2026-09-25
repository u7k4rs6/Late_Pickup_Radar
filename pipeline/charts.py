"""PNG charts with a fixed style and size, so screenshots match between takes (PRD 15.4).

Colours are the validated reference palette: sequential blue ramp for magnitude, categorical
slots 1-2 (blue, orange) for the two-series rain/dry bar, neutral ink for text and guides.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

SIZE = (9, 4.5)
DPI = 120
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#e6e5e1"
SERIES_1 = "#2a78d6"  # categorical slot 1, blue
SERIES_2 = "#eb6834"  # categorical slot 2, orange
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
EMPTY_CELL = "#f0efec"

plt.rcParams.update({"font.size": 9, "axes.titlesize": 11, "svg.hashsalt": "late-pickup-radar"})


def _frame(title: str):
    fig, ax = plt.subplots(figsize=SIZE, dpi=DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, loc="left")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=INK_MUTED)
    return fig, ax


def _save(fig, path: Path) -> Path:
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURFACE, metadata={"Software": None})
    plt.close(fig)
    return path


def wait_histogram(
    hist: pd.DataFrame,
    path: Path,
    thresholds: list[int],
    title: str,
    cap: float | None = None,
) -> Path:
    """Log-x / log-y histogram from 0.05-decade bins (columns bin_k, n); empty bins drawn empty."""
    counts = dict(zip(hist["bin_k"].astype(int), hist["n"].astype(int), strict=True))
    ks = range(min(counts), max(counts) + 1)
    edges = [10 ** (k / 20) for k in ks] + [10 ** ((max(counts) + 1) / 20)]
    values = [counts.get(k, 0) for k in ks]

    fig, ax = _frame(title)
    ax.stairs(values, edges, fill=True, color=SERIES_1, linewidth=0)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylim(bottom=0.8)
    top = ax.get_ylim()[1]
    thresholds = sorted(thresholds)
    for m in thresholds:
        ax.axvline(m, color=INK_MUTED, linewidth=1, linestyle=":")
    ax.text(
        thresholds[0] / 1.05,  # left of the first line, so it never meets the cap label
        top,
        "late thresholds " + " / ".join(map(str, thresholds)) + " min",
        color=INK_MUTED,
        fontsize=8,
        va="top",
        ha="right",
    )
    if cap is not None:
        ax.axvline(cap, color=INK_MUTED, linewidth=1, linestyle="--")
        ax.text(cap * 1.08, top, f"R03 cap {cap:g} min", color=INK_MUTED, fontsize=8, va="top")
    ax.set_xlabel("wait = pickup - request (minutes, log scale)", color=INK)
    ax.set_ylabel("trips per 0.05-decade bin (log scale)", color=INK)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    return _save(fig, path)


def late_rate_heatmap(
    df: pd.DataFrame, path: Path, title: str, min_n: int, boroughs: list[str]
) -> Path:
    """Late rate by borough (rows) x hour of day (cols); cells with n < min_n drawn empty."""
    grid = np.full((len(boroughs), 24), np.nan)
    for r in df.itertuples(index=False):
        if r.borough in boroughs and r.n >= min_n:
            grid[boroughs.index(r.borough), int(r.hour)] = 100 * r.value
    cmap = LinearSegmentedColormap.from_list("seq_blue", SEQUENTIAL).with_extremes(bad=EMPTY_CELL)
    fig, ax = _frame(title)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(False)
    im = ax.imshow(np.ma.masked_invalid(grid), aspect="auto", cmap=cmap, interpolation="nearest")
    ax.set_yticks(range(len(boroughs)), boroughs)
    ax.set_xticks(range(24), [f"{h:02d}" for h in range(24)])
    ax.set_xlabel("request hour of day", color=INK)
    ax.tick_params(length=0)
    # 2px surface gap between cells
    ax.set_xticks(np.arange(-0.5, 24, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(boroughs), 1), minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2)
    ax.tick_params(which="minor", length=0)
    bar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    bar.set_label("late-pickup rate, %", color=INK)
    bar.outline.set_visible(False)
    bar.ax.tick_params(colors=INK_MUTED)
    fig.text(0.01, 0.01, f"grey: fewer than {min_n:,} trips", color=INK_MUTED, fontsize=8)
    return _save(fig, path)


def rain_vs_dry(df: pd.DataFrame, path: Path, title: str, subtitle: str) -> Path:
    """Grouped bars: late rate in rainy vs dry request hours per scope (cols scope, rainy, dry)."""
    fig, ax = _frame(title)
    x = np.arange(len(df))
    w = 0.38
    bars_d = ax.bar(x - w / 2, 100 * df["dry"], w, color=SERIES_2, label="dry hours")
    bars_r = ax.bar(x + w / 2, 100 * df["rainy"], w, color=SERIES_1, label="rainy hours")
    for bars in (bars_d, bars_r):
        for b in bars:
            ax.text(
                b.get_x() + b.get_width() / 2,
                b.get_height(),
                f"{b.get_height():.1f}",
                ha="center",
                va="bottom",
                color=INK,
                fontsize=8,
            )
    ax.set_xticks(x, df["scope"])
    ax.set_ylabel("late-pickup rate, %", color=INK)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="upper left", labelcolor=INK)
    ax.text(0.0, -0.16, subtitle, transform=ax.transAxes, color=INK_MUTED, fontsize=8)
    return _save(fig, path)
