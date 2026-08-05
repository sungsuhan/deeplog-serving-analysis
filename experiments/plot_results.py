"""
Visualize benchmark results from experiments/results/cycle*/*.csv.

Generates figures saved to experiments/figures/:
  fig1_p50_latency.png        p50 latency vs concurrency, 전체 sweep(1~64), 사이클 평균±표준편차
  fig2_p99_latency.png        p99 latency vs concurrency
  fig3_throughput.png         throughput (RPS) vs concurrency
  fig4_summary_bar_{ds}.png   저(4)/중(16)/고(64) 3단계 대표값 비교 (데이터셋별 1장)
  fig5_cpu_usage.png          CPU 사용률(%) vs concurrency (docker stats 기반, cpu_pct_mean)
  fig6_gpu_usage.png          GPU 사용률(%) vs concurrency (nvidia-smi 기반, gpu_util_pct_mean)

run_benchmark.sh는 사이클(순서 랜덤화 반복 측정)마다 결과를
experiments/results/cycle{1,2,3}/{framework}_{dataset}.csv로 나누어 저장한다 —
이 스크립트는 그 cycle*/ 하위 전체를 읽어 사이클 간 평균을 낸다.

Usage:
  python experiments/plot_results.py
  python experiments/plot_results.py --results-dir experiments/results
"""

import argparse
from pathlib import Path

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd


def _set_korean_font() -> None:
  """저/중/고 등 한글 라벨이 기본 폰트(DejaVu Sans)엔 없어 네모(tofu)로 깨진다.
  설치된 폰트 중 한글을 지원하는 걸 우선순위대로 찾아 적용한다. Windows는
  보통 Malgun Gothic이 기본 내장이고, Linux(EC2)는 별도 설치가 필요할 수 있다
  (예: apt install fonts-nanum) — 못 찾으면 경고만 띄우고 넘어간다."""
  candidates = ["Malgun Gothic", "AppleGothic", "NanumGothic",
                "Noto Sans CJK KR", "Noto Sans KR"]
  available = {f.name for f in fm.fontManager.ttflist}
  for name in candidates:
    if name in available:
      plt.rcParams["font.family"] = name
      plt.rcParams["axes.unicode_minus"] = False  # 한글 폰트 전환 시 '-' 깨짐 방지
      return
  print("[WARN] 한글 지원 폰트를 찾지 못했습니다 - 그래프의 한글 라벨(저/중/고 등)이 "
        "깨질 수 있습니다. Linux라면 `apt install fonts-nanum` 등으로 설치하세요.")


# ── Style ─────────────────────────────────────────────────────────────────────
FRAMEWORKS   = ["fastapi", "bentoml", "triton"]
DATASETS     = ["hdfs", "bgl"]
DATASET_LABELS = {"hdfs": "HDFS", "bgl": "BGL"}

# 저/중/고 부하 대표값 — EXPERIMENT.md "concurrency sweep → 저/중/고 매핑" 결정
# (2026-07-31)과 동기화. concurrency sweep 전체(1/4/8/16/32/64)는 fig1~3의
# 곡선으로 보여주고, 발표·논문 비교표용 요약(fig4)은 이 3개 값만 쓴다.
LOAD_LEVELS = {"저": 4, "중": 16, "고": 64}

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
_set_korean_font()  # font.family은 위 update 이후에 정해야 덮어써지지 않는다


# ── Data loading ──────────────────────────────────────────────────────────────
def load_results(results_dir: Path) -> pd.DataFrame:
  """results_dir/cycle{N}/{framework}_{dataset}.csv를 전부 읽어 합친다.

  평평한 results_dir/{framework}_{dataset}.csv 구조가 아니라 cycle*/ 하위를
  찾는 이유: run_benchmark.sh가 순서 랜덤화 반복 측정(3사이클)마다 결과를
  별도 디렉토리에 쓰기 때문 — 이 함수가 사이클 번호를 "cycle" 컬럼으로
  붙여두면 이후 플로팅 단계에서 사이클 간 평균/표준편차를 낼 수 있다.
  """
  cycle_dirs = sorted(results_dir.glob("cycle*"))
  if not cycle_dirs:
    raise FileNotFoundError(
      f"{results_dir} 아래에 cycle*/ 디렉토리가 없습니다.\n"
      "  Run: bash experiments/run_benchmark.sh"
    )

  frames = []
  for cycle_dir in cycle_dirs:
    cycle_num = int(cycle_dir.name.removeprefix("cycle") or -1)
    for fw in FRAMEWORKS:
      for ds in DATASETS:
        csv_path = cycle_dir / f"{fw}_{ds}.csv"
        if csv_path.exists():
          df = pd.read_csv(csv_path)
          df["cycle"] = cycle_num
          frames.append(df)
        else:
          print(f"[WARN] Missing: {csv_path}")
  if not frames:
    raise FileNotFoundError(
      f"No result CSVs found under {results_dir}/cycle*/.\n"
      "  Run: bash experiments/run_benchmark.sh"
    )
  return pd.concat(frames, ignore_index=True)


# ── Figure helpers ────────────────────────────────────────────────────────────
def _add_framework_lines(ax, df: pd.DataFrame, y_col: str) -> None:
  """concurrency별 사이클 평균을 선으로, 사이클 간 표준편차를 에러바로 그린다."""
  for fw in FRAMEWORKS:
    sub = df[df["framework"] == fw]
    if sub.empty:
      continue
    agg = (
      sub.groupby("concurrency")[y_col]
      .agg(["mean", "std"])
      .reset_index()
      .sort_values("concurrency")
    )
    ax.errorbar(
      agg["concurrency"], agg["mean"], yerr=agg["std"].fillna(0),
      label      = fw.capitalize(),
      color      = COLORS[fw],
      marker     = MARKERS[fw],
      linewidth  = 1.8,
      markersize = 5,
      capsize    = 3,
    )


def _save(fig: plt.Figure, path: Path) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fig.savefig(path, bbox_inches="tight")
  plt.close(fig)
  print(f"  saved -> {path}")


# ── 공용: 지표 하나를 concurrency에 대해 그리는 함수 ───────────────────────────
# latency(fig1~2)·throughput(fig3)·자원 사용률(fig5~6)이 전부 "concurrency별
# 사이클 평균±표준편차를 프레임워크별 선으로, 데이터셋별로 나눠 그린다"는
# 구조가 동일해 하나로 합쳤다.
def _plot_metric_vs_concurrency(df: pd.DataFrame, figures_dir: Path,
                                y_col: str, title: str, ylabel: str,
                                filename: str) -> None:
  fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=False)
  fig.suptitle(title, fontweight="bold", y=1.01)

  for ax, ds in zip(axes, DATASETS):
    sub = df[df["dataset"] == ds]
    _add_framework_lines(ax, sub, y_col)
    ax.set_title(DATASET_LABELS[ds])
    ax.set_xlabel("Concurrent Requests")
    ax.set_ylabel(ylabel)
    ax.set_xscale("log", base=2)
    ax.xaxis.set_major_formatter(ticker.ScalarFormatter())
    ax.legend()

  fig.tight_layout()
  _save(fig, figures_dir / filename)


# ── Figure 1 & 2: latency vs concurrency ─────────────────────────────────────
def plot_latency(df: pd.DataFrame, figures_dir: Path, percentile: str) -> None:
  col   = f"p{percentile}_ms"
  label = f"P{percentile} Latency (ms)"
  fname = f"fig{'1' if percentile == '50' else '2'}_p{percentile}_latency.png"
  _plot_metric_vs_concurrency(
    df, figures_dir, col, f"{label} vs Concurrency", label, fname
  )


# ── Figure 3: throughput vs concurrency ──────────────────────────────────────
def plot_throughput(df: pd.DataFrame, figures_dir: Path) -> None:
  _plot_metric_vs_concurrency(
    df, figures_dir, "throughput_rps", "Throughput (RPS) vs Concurrency",
    "Throughput (req/s)", "fig3_throughput.png",
  )


# ── Figure 5 & 6: CPU/GPU 사용률 vs concurrency ───────────────────────────────
def plot_resource_usage(df: pd.DataFrame, figures_dir: Path) -> None:
  """benchmark.py --container/--monitor-gpu가 기록한 cpu_pct_mean/
  gpu_util_pct_mean(concurrency 구간별 docker stats·nvidia-smi 평균)을 그린다.
  GPU 수정(2026-07-31, FastAPI/BentoML도 Triton과 동일하게 GPU 사용) 이후에는
  세 프레임워크 전부 gpu_util_pct_mean이 채워진다 — 수정 전이었다면
  FastAPI/BentoML 라인이 0 근처에 붙어 있는 것으로 confound가 바로 드러났을 것."""
  if "cpu_pct_mean" in df.columns:
    _plot_metric_vs_concurrency(
      df, figures_dir, "cpu_pct_mean", "CPU 사용률 (%) vs Concurrency",
      "CPU Usage (%)", "fig5_cpu_usage.png",
    )
  else:
    print("[WARN] cpu_pct_mean 컬럼 없음 - fig5 건너뜀 "
          "(benchmark.py를 --container 없이 돌렸을 가능성)")

  if "gpu_util_pct_mean" in df.columns:
    _plot_metric_vs_concurrency(
      df, figures_dir, "gpu_util_pct_mean", "GPU 사용률 (%) vs Concurrency",
      "GPU Usage (%)", "fig6_gpu_usage.png",
    )
  else:
    print("[WARN] gpu_util_pct_mean 컬럼 없음 - fig6 건너뜀 "
          "(benchmark.py를 --monitor-gpu 없이 돌렸을 가능성)")


# ── Figure 4: 저/중/고 부하 대표값 비교 (데이터셋별 1장) ──────────────────────────
def plot_summary_bar(df: pd.DataFrame, figures_dir: Path) -> None:
  """LOAD_LEVELS(저=4/중=16/고=64)에서의 p50/p99/throughput을 프레임워크별로
  비교한다. 사이클이 여러 개면(3사이클) 평균을 낸다. 6개 sweep 값 전체는
  fig1~3의 곡선으로 이미 보여주므로, 이 차트는 발표·논문 비교표에 바로 쓸 수
  있게 3단계로 압축한 요약이다."""
  metrics       = ["p50_ms", "p99_ms", "throughput_rps"]
  metric_labels = ["P50 Latency (ms)", "P99 Latency (ms)", "Throughput (RPS)"]
  load_labels   = list(LOAD_LEVELS.keys())
  load_concurrencies = list(LOAD_LEVELS.values())

  for ds in DATASETS:
    sub_ds = df[(df["dataset"] == ds) & (df["concurrency"].isin(load_concurrencies))]
    if sub_ds.empty:
      print(f"[WARN] {DATASET_LABELS[ds]}: 저/중/고 concurrency 데이터 없음 - fig4 건너뜀")
      continue

    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    fig.suptitle(
      f"{DATASET_LABELS[ds]} — 저/중/고 부하 비교 (concurrency={load_concurrencies})",
      fontweight="bold", y=1.01,
    )

    x     = np.arange(len(load_labels))
    width = 0.22

    for ax, metric, mlabel in zip(axes, metrics, metric_labels):
      for i, fw in enumerate(FRAMEWORKS):
        fw_df = sub_ds[sub_ds["framework"] == fw]
        vals = [
          fw_df.loc[fw_df["concurrency"] == c, metric].mean()  # 사이클 평균
          for c in load_concurrencies
        ]
        vals = [0.0 if pd.isna(v) else v for v in vals]
        offset = (i - 1) * width
        bars = ax.bar(
          x + offset, vals, width,
          label  = fw.capitalize(),
          color  = COLORS[fw],
          alpha  = 0.85,
          edgecolor = "white",
        )
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
      ax.set_xticklabels([f"{lbl}\n(c={c})" for lbl, c in zip(load_labels, load_concurrencies)])
      ax.set_ylabel(mlabel)
      ax.legend()

    fig.tight_layout()
    _save(fig, figures_dir / f"fig4_summary_bar_{ds}.png")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--results-dir",     type=Path,
                      default=Path(__file__).parent / "results")
  parser.add_argument("--figures-dir",     type=Path,
                      default=Path(__file__).parent / "figures")
  args = parser.parse_args()

  print(f"[Plot] loading results from {args.results_dir} ...")
  df = load_results(args.results_dir)
  n_cycles = df["cycle"].nunique()
  print(f"  {len(df)} rows loaded  cycles={n_cycles}  "
        f"frameworks={df['framework'].unique().tolist()}")

  print("[Plot] generating figures ...")
  plot_latency(df, args.figures_dir, "50")
  plot_latency(df, args.figures_dir, "99")
  plot_throughput(df, args.figures_dir)
  plot_summary_bar(df, args.figures_dir)
  plot_resource_usage(df, args.figures_dir)

  print(f"\n[Plot] all figures saved to {args.figures_dir}/")


if __name__ == "__main__":
  main()
