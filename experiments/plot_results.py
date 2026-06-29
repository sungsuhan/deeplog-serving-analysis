"""
Visualize benchmark results from experiments/results/*.csv.

Generates 4 figures saved to experiments/figures/:
  fig1_p50_latency.png     p50 latency vs concurrency (HDFS / BGL side by side)
  fig2_p99_latency.png     p99 latency vs concurrency
  fig3_throughput.png      throughput (RPS) vs concurrency
  fig4_summary_bar.png     p50 / p99 / RPS bar chart at peak concurrency

Usage:
    python experiments/plot_results.py
    python experiments/plot_results.py --results-dir experiments/results --peak-concurrency 32
"""

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd

# ── Style ─────────────────────────────────────────────────────────────────────
FRAMEWORKS   = ["fastapi", "bentoml", "triton"]
DATASETS     = ["hdfs", "bgl"]
DATASET_LABELS = {"hdfs": "HDFS", "bgl": "BGL"}

COLORS = {
    "fastapi": "#4C72B0",
    "bentoml": "#DD8452",
    "triton":  "#55A868",
}
MARKERS = {
    "fastapi": "o",
    "bentoml": "s",
    "triton":  "^",
}

plt.rcParams.update({
    "font.family":     "DejaVu Sans",
    "font.size":       11,
    "axes.titlesize":  12,
    "axes.labelsize":  11,
    "legend.fontsize": 10,
    "figure.dpi":      150,
    "axes.grid":       True,
    "grid.alpha":      0.3,
    "axes.spines.top":    False,
    "axes.spines.right":  False,
})


# ── Data loading ──────────────────────────────────────────────────────────────
def load_results(results_dir: Path) -> pd.DataFrame:
    frames = []
    for fw in FRAMEWORKS:
        for ds in DATASETS:
            csv_path = results_dir / f"{fw}_{ds}.csv"
            if csv_path.exists():
                df = pd.read_csv(csv_path)
                frames.append(df)
            else:
                print(f"[WARN] Missing: {csv_path}")
    if not frames:
        raise FileNotFoundError(
            f"No result CSVs found in {results_dir}.\n"
            "  Run: bash experiments/run_benchmark.sh"
        )
    return pd.concat(frames, ignore_index=True)


# ── Figure helpers ────────────────────────────────────────────────────────────
def _add_framework_lines(ax, df: pd.DataFrame, y_col: str) -> None:
    for fw in FRAMEWORKS:
        sub = df[df["framework"] == fw].sort_values("concurrency")
        if sub.empty:
            continue
        ax.plot(
            sub["concurrency"], sub[y_col],
            label   = fw.capitalize(),
            color   = COLORS[fw],
            marker  = MARKERS[fw],
            linewidth = 1.8,
            markersize = 5,
        )


def _save(fig: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved -> {path}")


# ── Figure 1 & 2: latency vs concurrency ─────────────────────────────────────
def plot_latency(df: pd.DataFrame, figures_dir: Path, percentile: str) -> None:
    col   = f"p{percentile}_ms"
    label = f"P{percentile} Latency (ms)"

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=False)
    fig.suptitle(f"{label} vs Concurrency", fontweight="bold", y=1.01)

    for ax, ds in zip(axes, DATASETS):
        sub = df[df["dataset"] == ds]
        _add_framework_lines(ax, sub, col)
        ax.set_title(DATASET_LABELS[ds])
        ax.set_xlabel("Concurrent Requests")
        ax.set_ylabel(label)
        ax.set_xscale("log", base=2)
        ax.xaxis.set_major_formatter(ticker.ScalarFormatter())
        ax.legend()

    fig.tight_layout()
    _save(fig, figures_dir / f"fig{'1' if percentile == '50' else '2'}_p{percentile}_latency.png")


# ── Figure 3: throughput vs concurrency ──────────────────────────────────────
def plot_throughput(df: pd.DataFrame, figures_dir: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=False)
    fig.suptitle("Throughput (RPS) vs Concurrency", fontweight="bold", y=1.01)

    for ax, ds in zip(axes, DATASETS):
        sub = df[df["dataset"] == ds]
        _add_framework_lines(ax, sub, "throughput_rps")
        ax.set_title(DATASET_LABELS[ds])
        ax.set_xlabel("Concurrent Requests")
        ax.set_ylabel("Throughput (req/s)")
        ax.set_xscale("log", base=2)
        ax.xaxis.set_major_formatter(ticker.ScalarFormatter())
        ax.legend()

    fig.tight_layout()
    _save(fig, figures_dir / "fig3_throughput.png")


# ── Figure 4: summary bar chart at peak concurrency ──────────────────────────
def plot_summary_bar(df: pd.DataFrame, figures_dir: Path,
                     peak_concurrency: int) -> None:
    peak = df[df["concurrency"] == peak_concurrency]
    if peak.empty:
        print(f"[WARN] No data for concurrency={peak_concurrency}, skipping fig4.")
        return

    metrics = ["p50_ms", "p99_ms", "throughput_rps"]
    metric_labels = ["P50 Latency (ms)", "P99 Latency (ms)", "Throughput (RPS)"]

    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    fig.suptitle(
        f"Performance Summary at Concurrency={peak_concurrency}",
        fontweight="bold", y=1.01,
    )

    n_datasets = len(DATASETS)
    x          = np.arange(n_datasets)
    width      = 0.22

    for ax, metric, mlabel in zip(axes, metrics, metric_labels):
        for i, fw in enumerate(FRAMEWORKS):
            vals = [
                peak[(peak["framework"] == fw) & (peak["dataset"] == ds)][metric].values
                for ds in DATASETS
            ]
            vals = [v[0] if len(v) > 0 else 0 for v in vals]
            offset = (i - 1) * width
            bars = ax.bar(
                x + offset, vals, width,
                label  = fw.capitalize(),
                color  = COLORS[fw],
                alpha  = 0.85,
                edgecolor = "white",
            )
            # Value labels on bars
            for bar, val in zip(bars, vals):
                if val > 0:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() * 1.02,
                        f"{val:.1f}",
                        ha="center", va="bottom", fontsize=8,
                    )

        ax.set_title(mlabel)
        ax.set_xticks(x)
        ax.set_xticklabels([DATASET_LABELS[d] for d in DATASETS])
        ax.set_ylabel(mlabel)
        ax.legend()

    fig.tight_layout()
    _save(fig, figures_dir / "fig4_summary_bar.png")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir",     type=Path,
                        default=Path(__file__).parent / "results")
    parser.add_argument("--figures-dir",     type=Path,
                        default=Path(__file__).parent / "figures")
    parser.add_argument("--peak-concurrency", type=int, default=32,
                        help="Concurrency level used for the summary bar chart")
    args = parser.parse_args()

    print(f"[Plot] loading results from {args.results_dir} ...")
    df = load_results(args.results_dir)
    print(f"  {len(df)} rows loaded  frameworks={df['framework'].unique().tolist()}")

    print("[Plot] generating figures ...")
    plot_latency(df, args.figures_dir, "50")
    plot_latency(df, args.figures_dir, "99")
    plot_throughput(df, args.figures_dir)
    plot_summary_bar(df, args.figures_dir, args.peak_concurrency)

    print(f"\n[Plot] all figures saved to {args.figures_dir}/")


if __name__ == "__main__":
    main()
