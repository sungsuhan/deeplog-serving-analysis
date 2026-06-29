"""
DeepLog 서빙 프레임워크 HTTP 부하 테스트.

측정 방식 — Closed-loop benchmark:
  동시에 N개의 요청을 항상 in-flight 상태로 유지한다.
  하나가 완료되면 즉시 다음 요청을 보내는 방식.
  → 프레임워크가 처리할 수 있는 최대 처리량을 측정하는 표준 기법.

  (반대 개념인 Open-loop는 정해진 RPS로 요청을 보내지만,
   서버가 따라오지 못하면 큐가 쌓여 지연이 폭발적으로 증가.)

concurrency sweep: 1 → 4 → 8 → 16 → 32 → 64
  낮은 concurrency: 단일 요청 지연시간 (latency-oriented)
  높은 concurrency: 최대 처리량 (throughput-oriented)
  → 논문의 "부하 단계별 성능 곡선" 그래프의 x축이 됨.

출력 CSV 컬럼:
  framework, dataset, concurrency, n_requests, n_errors,
  throughput_rps, p50_ms, p99_ms, mean_ms, min_ms, max_ms

사용법:
  python experiments/benchmark.py \\
      --framework fastapi --endpoint http://localhost:8000 \\
      --dataset hdfs --concurrency 1 4 8 16 32 64 --duration 30
"""

import argparse
import asyncio
import csv
import random
import time
from pathlib import Path

import httpx
import numpy as np

_PROJECT_ROOT = Path(__file__).parent.parent


def _load_payloads(dataset: str, n_samples: int = 2000) -> list[dict]:
    """
    테스트 시퀀스를 무작위로 샘플링해 요청 payload 목록을 만든다.

    전체 데이터셋을 쓰지 않고 2000개만 샘플링하는 이유:
      CPU 메모리를 절약하면서도 충분히 다양한 입력으로 캐시 편향을 방지.
    """
    data_dir = _PROJECT_ROOT / "data" / "processed" / dataset
    seqs     = np.load(data_dir / "test.npy")  # (N, window_size)

    idx      = random.sample(range(len(seqs)), min(n_samples, len(seqs)))
    payloads = []
    for i in idx:
        payloads.append({
            "log_keys": seqs[i, :-1].tolist(),  # 입력 윈도우 (window_size-1개)
            "next_key": int(seqs[i, -1]),        # 실제 다음 키 (이상 판정용)
        })
    return payloads


async def _run_concurrency_level(
    endpoint: str,
    payloads: list[dict],
    concurrency: int,
    duration_s: int,
) -> dict:
    """
    고정 concurrency로 duration_s 초 동안 Closed-loop 부하를 가한다.

    asyncio.Semaphore로 동시 요청 수를 제한:
      Semaphore(N) → 최대 N개의 코루틴이 동시에 await 상태로 대기.
      하나가 완료되면 세마포어가 해제되어 다음 요청이 즉시 시작.
    """
    latencies: list[float] = []
    errors     = 0
    deadline   = time.monotonic() + duration_s
    payload_it = 0
    semaphore  = asyncio.Semaphore(concurrency)

    async def _one_request(client: httpx.AsyncClient, payload: dict) -> None:
        nonlocal errors, payload_it
        async with semaphore:
            t0 = time.perf_counter()
            try:
                resp = await client.post(f"{endpoint}/predict", json=payload)
                resp.raise_for_status()
                latencies.append((time.perf_counter() - t0) * 1000.0)
            except Exception:
                errors += 1

    async with httpx.AsyncClient(
        timeout = httpx.Timeout(10.0),
        limits  = httpx.Limits(max_connections=concurrency + 10),
    ) as client:
        tasks: set[asyncio.Task] = set()

        while time.monotonic() < deadline or tasks:
            # 세마포어 슬롯을 채울 만큼 태스크를 생성
            while len(tasks) < concurrency and time.monotonic() < deadline:
                payload = payloads[payload_it % len(payloads)]
                payload_it += 1
                t = asyncio.create_task(_one_request(client, payload))
                tasks.add(t)

            if tasks:
                # 완료된 태스크를 제거하고 루프 계속
                done, tasks = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )

    n = len(latencies)
    if n == 0:
        return {"n_requests": 0, "n_errors": errors}

    arr = np.array(latencies)
    return {
        "n_requests":     n,
        "n_errors":       errors,
        # throughput = 총 성공 요청 수 / 측정 시간 (RPS)
        "throughput_rps": round(n / duration_s, 2),
        # p50 (중앙값): 전형적인 요청의 지연시간
        "p50_ms":         round(float(np.percentile(arr, 50)), 3),
        # p99: 상위 1% 느린 요청의 지연시간 — SLA 기준으로 사용
        "p99_ms":         round(float(np.percentile(arr, 99)), 3),
        "mean_ms":        round(float(arr.mean()), 3),
        "min_ms":         round(float(arr.min()), 3),
        "max_ms":         round(float(arr.max()), 3),
    }


async def _benchmark(args: argparse.Namespace) -> None:
    payloads = _load_payloads(args.dataset)
    print(f"[Benchmark] framework={args.framework}  endpoint={args.endpoint}  "
          f"dataset={args.dataset}  payloads={len(payloads)}")

    # 워밍업: JIT 컴파일·커넥션 풀 초기화 등을 안정시키기 위해 5초 선행 실행
    print("  워밍업 (concurrency=1, 5s) ...")
    await _run_concurrency_level(args.endpoint, payloads, 1, 5)

    results = []
    for c in args.concurrency:
        print(f"  concurrency={c:3d}  duration={args.duration}s ...", end="", flush=True)
        metrics = await _run_concurrency_level(args.endpoint, payloads, c, args.duration)
        row = {"framework": args.framework, "dataset": args.dataset,
               "concurrency": c, **metrics}
        results.append(row)
        print(f"  rps={metrics.get('throughput_rps')}  "
              f"p50={metrics.get('p50_ms')}ms  p99={metrics.get('p99_ms')}ms")

    # 결과 CSV 저장
    args.output.mkdir(parents=True, exist_ok=True)
    out_csv    = args.output / f"{args.framework}_{args.dataset}.csv"
    fieldnames = [
        "framework", "dataset", "concurrency", "n_requests", "n_errors",
        "throughput_rps", "p50_ms", "p99_ms", "mean_ms", "min_ms", "max_ms",
    ]
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\n[Benchmark] 결과 저장 → {out_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepLog 서빙 프레임워크 부하 테스트")
    parser.add_argument("--framework",   required=True,
                        choices=["fastapi", "bentoml", "triton"])
    parser.add_argument("--endpoint",    required=True,
                        help="기본 URL (예: http://localhost:8000)")
    parser.add_argument("--dataset",     choices=["hdfs", "bgl"], required=True)
    parser.add_argument("--concurrency", type=int, nargs="+",
                        default=[1, 4, 8, 16, 32, 64],
                        help="동시 요청 수 목록 (기본값: 1 4 8 16 32 64)")
    parser.add_argument("--duration",    type=int, default=30,
                        help="concurrency 단계별 측정 시간(초) (기본값: 30)")
    parser.add_argument("--output",      type=Path,
                        default=Path(__file__).parent / "results")
    args = parser.parse_args()

    asyncio.run(_benchmark(args))


if __name__ == "__main__":
    main()
