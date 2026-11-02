"""
세 서빙 프레임워크(FastAPI / BentoML / Triton 래퍼)의 응답이 일치하는지 검사한다.

CLAUDE.md "검증" 절차를 자동화한 것이다.
  1. /health — 세 곳 모두 정상이고, 같은 데이터셋·window·num_candidates를 보고하는지.
     BentoML만 POST로 호출한다(BentoML 1.3 신규 SDK는 POST 전용).
  2. 같은 log_keys로 POST /predict — 응답 키·타입이 같은지, 같은 체크포인트라면
     top_candidates·is_anomaly가 같고 probabilities가 수치 오차 안에서 같은지.

세 프레임워크 스키마가 어긋나면 벤치마크 CSV 컬럼이 프레임워크마다 달라져 비교 자체가
불가능해진다. 이 검사는 그 규칙을 사람이 아니라 CI가 매번 확인하게 한다.

후보 순서 비교: 확률이 거의 같은 두 후보는 프레임워크 간 아주 작은 수치 차이(PyTorch
eager vs LibTorch)로 순서가 바뀔 수 있다. 그래서 같은 순위의 후보가 다르면, 그 자리의
확률 차이가 허용 오차 안일 때만(즉 동률일 때만) 통과시킨다.

성능을 재지 않는다 — latency_ms는 키·타입만 확인하고 값은 비교하지 않는다.

Usage:
  python ci/check_serving.py --dataset hdfs --test-data <root>/data/hdfs/test.npy
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

ENDPOINTS = {
  "fastapi": "http://localhost:8000",
  "bentoml": "http://localhost:3000",
  "triton":  "http://localhost:8002",
}
HEALTH_POST = {"bentoml"}
EXPECTED_KEYS = {"top_candidates", "probabilities", "is_anomaly", "latency_ms"}
PROB_ATOL = 1e-4
MIN_MEAN_TOP1 = 0.3   # 테스트 모델이 이 정도는 확신해야 후보 순서 비교가 의미 있다


def call(url: str, body: dict | None, method: str, timeout: float = 10.0) -> dict:
  data = None if body is None else json.dumps(body).encode()
  req = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"})
  with urllib.request.urlopen(req, timeout=timeout) as resp:
    return json.loads(resp.read())


def health(fw: str) -> dict:
  if fw in HEALTH_POST:
    return call(f"{ENDPOINTS[fw]}/health", {}, "POST")
  return call(f"{ENDPOINTS[fw]}/health", None, "GET")


def wait_healthy(timeout_s: float) -> dict:
  deadline = time.time() + timeout_s
  pending, result, last_err = set(ENDPOINTS), {}, {}
  while pending and time.time() < deadline:
    for fw in sorted(pending):
      try:
        h = health(fw)
        if h.get("status") == "ok":
          result[fw] = h
          pending.discard(fw)
          print(f"[health] {fw}: {h}")
      except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        last_err[fw] = repr(exc)
    if pending:
      time.sleep(2)
  if pending:
    for fw in sorted(pending):
      print(f"[health] {fw}: {timeout_s:.0f}s 안에 정상 응답 없음 ({last_err.get(fw)})")
    sys.exit(1)
  return result


def type_signature(resp: dict) -> dict:
  def t(v):
    if isinstance(v, list):
      return "list[" + ",".join(sorted({type(x).__name__ for x in v})) + "]"
    return type(v).__name__
  return {k: t(v) for k, v in resp.items()}


def compare(ref_fw: str, ref: dict, fw: str, other: dict) -> list[str]:
  errs = []
  rc, oc = ref["top_candidates"], other["top_candidates"]
  rp, op = ref["probabilities"], other["probabilities"]
  if len(rc) != len(oc):
    return [f"{fw} 후보 수 {len(oc)} != {ref_fw} {len(rc)}"]
  for i, (a, b) in enumerate(zip(rc, oc)):
    if abs(rp[i] - op[i]) > PROB_ATOL:
      errs.append(f"{fw} {i}번째 확률 {op[i]:.6f} != {ref_fw} {rp[i]:.6f}")
    if a != b:
      # 같은 순위 후보가 다르면 동률(바로 옆 순위와 확률이 거의 같음)일 때만 허용
      neighbors = [rp[j] for j in (i - 1, i + 1) if 0 <= j < len(rp)]
      if not any(abs(rp[i] - n) <= PROB_ATOL for n in neighbors):
        errs.append(f"{fw} {i}번째 후보 {b} != {ref_fw} {a} (확률 {rp[i]:.6f}, 동률 아님)")
  if ref["is_anomaly"] != other["is_anomaly"] and set(rc) == set(oc):
    errs.append(f"{fw} is_anomaly {other['is_anomaly']} != {ref_fw} {ref['is_anomaly']}")
  return errs


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--dataset",   required=True)
  parser.add_argument("--test-data", type=Path, required=True)
  parser.add_argument("--n",         type=int, default=50, help="비교할 요청 수")
  parser.add_argument("--timeout",   type=float, default=300)
  parser.add_argument("--frameworks", nargs="+", choices=list(ENDPOINTS),
                      default=list(ENDPOINTS),
                      help="검사할 프레임워크 (기본: 셋 다). 로컬에서 Docker 없이 "
                           "Triton을 뺄 때만 줄인다 — CI는 항상 셋 다 검사한다.")
  args = parser.parse_args()
  for fw in list(ENDPOINTS):
    if fw not in args.frameworks:
      del ENDPOINTS[fw]
  if "fastapi" not in ENDPOINTS:
    parser.error("비교 기준인 fastapi는 빠질 수 없습니다")

  errors = []

  # 1. /health
  hs = wait_healthy(args.timeout)
  for fw, h in hs.items():
    if h.get("dataset") != args.dataset:
      errors.append(f"{fw} /health dataset={h.get('dataset')!r}, 기대값 {args.dataset!r}")
  for key in ("window_size", "num_candidates"):
    vals = {fw: h.get(key) for fw, h in hs.items()}
    if len(set(vals.values())) != 1:
      errors.append(f"/health {key}가 프레임워크마다 다름: {vals}")

  # 2. /predict
  seqs = np.load(args.test_data)
  seqs = seqs[: args.n]
  sigs, top1 = {}, []
  for i, s in enumerate(seqs):
    body = {"log_keys": [int(x) for x in s[:-1]], "next_key": int(s[-1])}
    resps = {fw: call(f"{ENDPOINTS[fw]}/predict", body, "POST") for fw in ENDPOINTS}
    for fw, r in resps.items():
      if set(r) != EXPECTED_KEYS:
        errors.append(f"[{i}] {fw} 응답 키 {sorted(r)} != {sorted(EXPECTED_KEYS)}")
        continue
      sigs.setdefault(fw, type_signature(r))
    if any(set(r) != EXPECTED_KEYS for r in resps.values()):
      continue
    top1.append(resps["fastapi"]["probabilities"][0])
    for fw in [f for f in ENDPOINTS if f != "fastapi"]:
      errors += [f"[{i}] {e}" for e in compare("fastapi", resps["fastapi"], fw, resps[fw])]

  if len({json.dumps(v, sort_keys=True) for v in sigs.values()}) > 1:
    errors.append(f"응답 타입이 프레임워크마다 다름: {sigs}")
  else:
    print(f"[predict] 응답 타입 ({'/'.join(ENDPOINTS)} 공통): {next(iter(sigs.values()), {})}")

  # 검사 자체의 유효성: 모델이 거의 학습되지 않아 후보 확률이 다 비슷하면, 후보 순서
  # 비교가 동률 허용으로 대부분 통과해 버려 아무것도 검사하지 못한다.
  if top1:
    mean_top1 = sum(top1) / len(top1)
    print(f"[predict] fastapi 1순위 후보 평균 확률: {mean_top1:.3f}")
    if mean_top1 < MIN_MEAN_TOP1:
      errors.append(f"테스트 모델이 거의 학습되지 않음(1순위 평균 확률 {mean_top1:.3f} < "
                    f"{MIN_MEAN_TOP1}) — 후보 비교가 의미 없으니 학습 스텝을 늘릴 것")

  if errors:
    print(f"[FAIL] {len(errors)}건")
    for e in errors[:40]:
      print("  - " + e)
    sys.exit(1)
  print(f"[OK] {len(seqs)}개 요청에서 {'/'.join(ENDPOINTS)} 응답 스키마·값 일치 "
        f"(dataset={args.dataset})")


if __name__ == "__main__":
  main()
