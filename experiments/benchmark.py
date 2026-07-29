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
  {framework}_{dataset}.csv
    framework, dataset, concurrency, n_requests, n_errors,
    throughput_rps, p50_ms, p99_ms, mean_ms, min_ms, max_ms,
    cpu_pct_mean, cpu_pct_max, mem_used_mb_mean,
    gpu_util_pct_mean, gpu_util_pct_max, gpu_mem_used_mb_mean
  {framework}_{dataset}_resources.csv  (--container/--monitor-gpu 지정 시)
    timestamp_unix, datetime_utc, framework, dataset, concurrency,
    cpu_pct, mem_used_mb, gpu_util_pct, gpu_mem_used_mb
    — latency 측정과 동일한 time.time() 기준 타임스탬프로 기록되어 사후에
      "이 시점에 concurrency=N이었다"를 대조할 수 있다.

사용법:
  python experiments/benchmark.py \\
    --framework fastapi --endpoint http://localhost:8000 \\
    --dataset hdfs --concurrency 1 4 8 16 32 64 --duration 30 \\
    --container <docker-container-id-or-name> --monitor-gpu
"""

import argparse
import asyncio
import csv
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import psutil

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


# ── 자원 사용률(CPU/메모리/GPU) 샘플링 ─────────────────────────────────────────
_MEM_UNIT_TO_MB = {"B": 1e-6, "KB": 1e-3, "MB": 1.0, "GB": 1000.0,
                   "KiB": 1 / 1024, "MiB": 1.0, "GiB": 1024.0}


def _parse_docker_mem(mem_usage: str) -> float | None:
  """docker stats의 '123.4MiB' 형태 문자열에서 MB 단위 수치만 추출한다."""
  m = re.match(r"([\d.]+)\s*([A-Za-z]+)", mem_usage.strip())
  if not m:
    return None
  value, unit = float(m.group(1)), m.group(2)
  return value * _MEM_UNIT_TO_MB.get(unit, 1.0)


async def _run_cmd(*args: str) -> str | None:
  """서브프로세스를 비동기로 실행해 stdout을 반환한다. 실패 시 None."""
  try:
    proc = await asyncio.create_subprocess_exec(
      *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    if proc.returncode != 0:
      return None
    return stdout.decode().strip()
  except Exception:
    return None


async def _sample_once(container: str | None, monitor_gpu: bool) -> dict:
  """CPU·메모리·GPU 사용률을 한 시점에 대해 샘플링한다."""
  sample = {
    "timestamp_unix":  time.time(),
    "cpu_pct":         None,
    "mem_used_mb":     None,
    "gpu_util_pct":    None,
    "gpu_mem_used_mb": None,
  }

  if container:
    out = await _run_cmd(
      "docker", "stats", "--no-stream", "--no-trunc",
      "--format", "{{.CPUPerc}}|{{.MemUsage}}", container,
    )
    if out:
      cpu_str, _, mem_str = out.partition("|")
      try:
        sample["cpu_pct"] = float(cpu_str.strip().rstrip("%"))
      except ValueError:
        pass
      mem_used, _, _ = mem_str.partition("/")
      sample["mem_used_mb"] = _parse_docker_mem(mem_used)
  else:
    # 특정 컨테이너를 지정하지 않으면 host 전체 CPU/메모리를 psutil로 대체 측정
    sample["cpu_pct"]     = psutil.cpu_percent(interval=None)
    sample["mem_used_mb"] = psutil.virtual_memory().used / 1e6

  if monitor_gpu:
    out = await _run_cmd(
      "nvidia-smi",
      "--query-gpu=utilization.gpu,memory.used",
      "--format=csv,noheader,nounits",
    )
    if out:
      first_line = out.splitlines()[0]  # 다중 GPU면 GPU 0만 사용
      try:
        util_str, mem_str = [p.strip() for p in first_line.split(",")]
        sample["gpu_util_pct"]    = float(util_str)
        sample["gpu_mem_used_mb"] = float(mem_str)
      except ValueError:
        pass

  return sample


async def _resource_sampler(
  container: str | None,
  monitor_gpu: bool,
  interval_s: float,
  level_state: dict,
  samples: list[dict],
  stop_event: asyncio.Event,
) -> None:
  """
  벤치마크가 도는 동안 백그라운드에서 주기적으로 자원 사용률을 기록한다.

  latency 측정과 동일한 time.time() 기준 타임스탬프를 남겨, 사후에 특정
  concurrency 구간과 자원 사용률을 시간으로 대조할 수 있게 한다.
  """
  while not stop_event.is_set():
    sample = await _sample_once(container, monitor_gpu)
    sample["concurrency"] = level_state.get("concurrency")
    samples.append(sample)
    try:
      await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
    except asyncio.TimeoutError:
      pass


def _summarize_resources(samples: list[dict], t_start: float, t_end: float) -> dict:
  """[t_start, t_end] 구간에 속하는 샘플만 모아 평균/최댓값을 계산한다."""
  window = [s for s in samples if t_start <= s["timestamp_unix"] <= t_end]

  def _agg(key: str, fn):
    vals = [s[key] for s in window if s.get(key) is not None]
    return round(fn(vals), 2) if vals else None

  return {
    "cpu_pct_mean":         _agg("cpu_pct", lambda v: sum(v) / len(v)),
    "cpu_pct_max":          _agg("cpu_pct", max),
    "mem_used_mb_mean":     _agg("mem_used_mb", lambda v: sum(v) / len(v)),
    "gpu_util_pct_mean":    _agg("gpu_util_pct", lambda v: sum(v) / len(v)),
    "gpu_util_pct_max":     _agg("gpu_util_pct", max),
    "gpu_mem_used_mb_mean": _agg("gpu_mem_used_mb", lambda v: sum(v) / len(v)),
  }


async def _run_concurrency_level(
  endpoint: str,
  payloads: list[dict],
  concurrency: int,
  duration_s: int,
  level_state: dict | None = None,
) -> dict:
  """
  고정 concurrency로 duration_s 초 동안 Closed-loop 부하를 가한다.

  asyncio.Semaphore로 동시 요청 수를 제한:
    Semaphore(N) → 최대 N개의 코루틴이 동시에 await 상태로 대기.
    하나가 완료되면 세마포어가 해제되어 다음 요청이 즉시 시작.
  """
  if level_state is not None:
    level_state["concurrency"] = concurrency
  t_level_start = time.time()  # 자원 사용률 샘플과 대조할 wall-clock 시작 시각

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

  t_level_end = time.time()
  n = len(latencies)
  if n == 0:
    return {"n_requests": 0, "n_errors": errors,
            "t_start": t_level_start, "t_end": t_level_end}

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
    "t_start":        t_level_start,
    "t_end":          t_level_end,
  }


async def _benchmark(args: argparse.Namespace) -> None:
  payloads = _load_payloads(args.dataset)
  print(f"[Benchmark] framework={args.framework}  endpoint={args.endpoint}  "
        f"dataset={args.dataset}  payloads={len(payloads)}")

  # 워밍업: JIT 컴파일·커넥션 풀 초기화 등을 안정시키기 위해 5초 선행 실행
  print("  워밍업 (concurrency=1, 5s) ...")
  await _run_concurrency_level(args.endpoint, payloads, 1, 5)

  # 자원 사용률 백그라운드 샘플러 — concurrency 레벨 루프와 동시에 계속 돈다
  resource_samples: list[dict] = []
  level_state: dict = {"concurrency": None}
  stop_event = asyncio.Event()
  sampler_task = None
  if args.container or args.monitor_gpu:
    sampler_task = asyncio.create_task(_resource_sampler(
      args.container, args.monitor_gpu, args.sample_interval,
      level_state, resource_samples, stop_event,
    ))
  else:
    print("  [자원 모니터링 비활성] --container 또는 --monitor-gpu 미지정")

  results = []
  for c in args.concurrency:
    print(f"  concurrency={c:3d}  duration={args.duration}s ...", end="", flush=True)
    metrics = await _run_concurrency_level(
      args.endpoint, payloads, c, args.duration, level_state=level_state,
    )
    resource_agg = _summarize_resources(
      resource_samples, metrics["t_start"], metrics["t_end"]
    )
    row = {
      "framework": args.framework, "dataset": args.dataset, "concurrency": c,
      **{k: v for k, v in metrics.items() if k not in ("t_start", "t_end")},
      **resource_agg,
    }
    results.append(row)
    print(f"  rps={metrics.get('throughput_rps')}  "
          f"p50={metrics.get('p50_ms')}ms  p99={metrics.get('p99_ms')}ms  "
          f"cpu={resource_agg['cpu_pct_mean']}%  gpu={resource_agg['gpu_util_pct_mean']}%")

  if sampler_task is not None:
    stop_event.set()
    await sampler_task

  # 결과 CSV 저장
  args.output.mkdir(parents=True, exist_ok=True)
  out_csv    = args.output / f"{args.framework}_{args.dataset}.csv"
  fieldnames = [
    "framework", "dataset", "concurrency", "n_requests", "n_errors",
    "throughput_rps", "p50_ms", "p99_ms", "mean_ms", "min_ms", "max_ms",
    "cpu_pct_mean", "cpu_pct_max", "mem_used_mb_mean",
    "gpu_util_pct_mean", "gpu_util_pct_max", "gpu_mem_used_mb_mean",
  ]
  with open(out_csv, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)

  print(f"\n[Benchmark] 결과 저장 → {out_csv}")

  # 자원 사용률 시계열 원본 — latency 측정과 동일한 timestamp_unix 기준
  if resource_samples:
    res_csv = args.output / f"{args.framework}_{args.dataset}_resources.csv"
    res_fieldnames = [
      "timestamp_unix", "datetime_utc", "framework", "dataset", "concurrency",
      "cpu_pct", "mem_used_mb", "gpu_util_pct", "gpu_mem_used_mb",
    ]
    with open(res_csv, "w", newline="") as f:
      writer = csv.DictWriter(f, fieldnames=res_fieldnames)
      writer.writeheader()
      for s in resource_samples:
        writer.writerow({
          "timestamp_unix":  round(s["timestamp_unix"], 3),
          "datetime_utc":    datetime.fromtimestamp(
            s["timestamp_unix"], tz=timezone.utc
          ).isoformat(),
          "framework":       args.framework,
          "dataset":         args.dataset,
          "concurrency":     s.get("concurrency"),
          "cpu_pct":         s.get("cpu_pct"),
          "mem_used_mb":     s.get("mem_used_mb"),
          "gpu_util_pct":    s.get("gpu_util_pct"),
          "gpu_mem_used_mb": s.get("gpu_mem_used_mb"),
        })
    print(f"[Benchmark] 자원 사용률 로그 → {res_csv}")


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
  parser.add_argument("--container",   default=None,
                      help="자원 사용률을 측정할 Docker 컨테이너 이름/ID "
                           "(미지정 시 host 전체를 psutil로 대체 측정)")
  parser.add_argument("--monitor-gpu", action="store_true",
                      help="nvidia-smi로 GPU 사용률·메모리도 함께 기록")
  parser.add_argument("--sample-interval", type=float, default=1.0,
                      help="자원 사용률 샘플링 주기(초) (기본값: 1.0)")
  args = parser.parse_args()

  asyncio.run(_benchmark(args))


if __name__ == "__main__":
  main()
