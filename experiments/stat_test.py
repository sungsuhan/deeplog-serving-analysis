"""
프레임워크 간 지연시간·처리량 분포 차이를 Mann-Whitney U 검정으로 비교한다.
CLAUDE.md "벤치마크 측정 규칙" — 결과 비교 시 통계 검정은 Mann-Whitney U 사용.

검정의 표본 단위: benchmark.py는 concurrency 레벨마다 p50/p99/throughput 등
"요약 통계"만 CSV에 저장하고 개별 요청의 원본 지연시간 배열은 저장하지 않는다.
대신 run_benchmark.sh가 순서 랜덤화 반복 측정을 여러 사이클(기본 3회) 도므로,
이 스크립트는 "사이클"을 검정의 표본 단위로 삼는다 — 예를 들어 concurrency=16
에서 FastAPI의 사이클별 p50 값 3개 vs Triton의 사이클별 p50 값 3개를 비교.

사이클 수가 적으면(n=3) Mann-Whitney U(양측)가 도달 가능한 최소 p-value가
제한된다(n1=n2=3이면 약 0.1) — 즉 사이클을 늘리지 않는 한 전통적 유의수준
(0.05)에 원천적으로 도달할 수 없는 경우가 생긴다. 이 스크립트는 그 한계를
결과와 함께 출력한다.

Usage:
  python experiments/stat_test.py --dataset hdfs
  python experiments/stat_test.py --dataset hdfs --concurrency 4 16 64
"""

import argparse
import itertools
from math import comb
from pathlib import Path

import pandas as pd
from scipy import stats

FRAMEWORKS = ["fastapi", "bentoml", "triton"]
DATASETS   = ["hdfs", "bgl"]
METRICS    = ["p50_ms", "p99_ms", "throughput_rps"]


def load_results(results_dir: Path) -> pd.DataFrame:
  """results_dir/cycle{N}/{framework}_{dataset}.csv를 전부 읽어 합친다.
  plot_results.py의 load_results와 로직이 같다 — 두 스크립트를 독립적으로
  실행할 수 있게 각자 자기완결적으로 유지했다."""
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
  if not frames:
    raise FileNotFoundError(f"No result CSVs found under {results_dir}/cycle*/.")
  return pd.concat(frames, ignore_index=True)


def _min_reachable_p(n1: int, n2: int) -> float:
  """이 표본 크기에서 Mann-Whitney U(양측)가 도달 가능한 최소 p-value.
  가능한 순위 배열 C(n1+n2, n1)개 중 가장 극단적인 배열(양쪽 끝 각 1가지)의
  확률이라 최소 p ≈ 2/C(n1+n2, n1)."""
  return 2 / comb(n1 + n2, n1)


def run_tests(df: pd.DataFrame, dataset: str, concurrencies: list[int]) -> pd.DataFrame:
  """(concurrency, metric, 프레임워크 쌍)마다 Mann-Whitney U를 돌려 표로 만든다."""
  rows = []
  sub = df[(df["dataset"] == dataset) & (df["concurrency"].isin(concurrencies))]

  for concurrency in concurrencies:
    level_df = sub[sub["concurrency"] == concurrency]
    for metric in METRICS:
      for fw_a, fw_b in itertools.combinations(FRAMEWORKS, 2):
        a = level_df.loc[level_df["framework"] == fw_a, metric].dropna().to_numpy()
        b = level_df.loc[level_df["framework"] == fw_b, metric].dropna().to_numpy()
        if len(a) < 2 or len(b) < 2:
          continue  # U 검정 자체가 성립하려면 그룹당 최소 2개 표본 필요

        u_stat, p_value = stats.mannwhitneyu(a, b, alternative="two-sided")
        rows.append({
          "dataset":            dataset,
          "concurrency":        concurrency,
          "metric":             metric,
          "framework_a":        fw_a,
          "framework_b":        fw_b,
          "n_a":                len(a),
          "n_b":                len(b),
          "median_a":           round(float(pd.Series(a).median()), 3),
          "median_b":           round(float(pd.Series(b).median()), 3),
          "u_statistic":        round(float(u_stat), 3),
          "p_value":            round(float(p_value), 4),
          "min_reachable_p":    round(_min_reachable_p(len(a), len(b)), 4),
          "significant_at_0.05": bool(p_value < 0.05),
        })

  return pd.DataFrame(rows)


def main() -> None:
  parser = argparse.ArgumentParser(
    description="프레임워크 간 지연시간/처리량 분포 비교 (Mann-Whitney U)"
  )
  parser.add_argument("--dataset", choices=DATASETS, required=True)
  parser.add_argument("--results-dir", type=Path,
                      default=Path(__file__).parent / "results")
  parser.add_argument("--concurrency", type=int, nargs="+", default=[4, 16, 64],
                      help="검정할 concurrency 값 (기본값: 저/중/고 대표값 4 16 64 "
                           "- plot_results.py의 LOAD_LEVELS와 동기화)")
  parser.add_argument("--output", type=Path, default=None,
                      help="결과 CSV 저장 경로 (기본값: {results-dir}/stat_test_{dataset}.csv)")
  args = parser.parse_args()

  df = load_results(args.results_dir)
  n_cycles = df["cycle"].nunique()
  print(f"[stat_test] dataset={args.dataset}  cycles={n_cycles}  "
        f"concurrency={args.concurrency}")
  if n_cycles < 3:
    print(f"[WARN] 사이클 수가 {n_cycles}개뿐입니다 - CLAUDE.md 권장(3사이클)보다 "
          "적으면 통계적 검정력이 더 낮습니다.")

  result_df = run_tests(df, args.dataset, args.concurrency)
  if result_df.empty:
    print("[stat_test] 비교 가능한 데이터가 없습니다 (표본 부족 또는 결과 없음).")
    return

  out_path = args.output or (args.results_dir / f"stat_test_{args.dataset}.csv")
  result_df.to_csv(out_path, index=False)
  print(f"\n[stat_test] {len(result_df)}건 검정 완료 -> {out_path}")

  n1 = n2 = n_cycles
  print(
    f"\n[주의] 사이클 수(n={n_cycles})가 곧 검정의 표본 크기입니다. "
    f"n={n1} vs n={n2}에서 Mann-Whitney U(양측)가 도달 가능한 최소 p-value는 "
    f"약 {_min_reachable_p(n1, n2):.3f}입니다 - 이보다 작은 값이 안 나온다고 "
    "차이가 없다는 뜻은 아니며, 사이클 수를 늘리지 않는 한 통계적 유의성 판정 "
    "자체가 제한적입니다. median_a/median_b의 차이 크기(effect size)를 "
    "p-value와 함께 보고하는 걸 권장합니다."
  )

  sig = result_df[result_df["significant_at_0.05"]]
  if not sig.empty:
    print(f"\np<0.05인 비교 {len(sig)}건:")
    print(sig[["concurrency", "metric", "framework_a", "framework_b", "p_value"]]
          .to_string(index=False))


if __name__ == "__main__":
  main()
