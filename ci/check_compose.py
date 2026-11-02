"""
docker-compose.yml이 DEEPLOG_DATASET 하나로 세 프레임워크의 모델을 함께 바꾸는지 검사한다.

2026-10-31 이전에는 FastAPI 데이터셋이 hdfs로 하드코딩되어 있었고, BentoML·Triton은
마지막에 등록한 모델을 띄웠다. 그 상태로 BGL을 측정하면 프레임워크마다 다른 모델을
비교하게 된다(EXPERIMENT.md 스코프 이력 2026-10-31). 이 검사는 그 실수가 되돌아오는
것을 막는다. 컨테이너를 띄우지 않고 `docker compose config`의 해석 결과만 본다.

Usage:
  python ci/check_compose.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

COMPOSE = Path(__file__).resolve().parent.parent / "docker" / "docker-compose.yml"


def resolved(dataset: str | None) -> dict:
  env = {k: v for k, v in os.environ.items() if k != "DEEPLOG_DATASET"}
  if dataset is not None:
    env["DEEPLOG_DATASET"] = dataset
  out = subprocess.run(
    ["docker", "compose", "-f", str(COMPOSE), "config", "--format", "json"],
    env=env, check=True, capture_output=True, text=True,
  ).stdout
  return json.loads(out)["services"]


def env_of(service: dict) -> dict:
  e = service.get("environment") or {}
  if isinstance(e, list):
    e = dict(item.split("=", 1) for item in e)
  return e


def volume_sources(service: dict) -> list[str]:
  return [v.get("source", "").replace("\\", "/") for v in service.get("volumes", [])]


def check(dataset: str | None) -> list[str]:
  ds = dataset or "hdfs"
  svc = resolved(dataset)
  errors = []

  fa = env_of(svc["fastapi"])
  if fa.get("DATASET") != ds:
    errors.append(f"fastapi DATASET={fa.get('DATASET')!r}, 기대값 {ds!r}")
  if not str(fa.get("CKPT_DIR", "")).endswith(f"/checkpoints/{ds}"):
    errors.append(f"fastapi CKPT_DIR={fa.get('CKPT_DIR')!r}가 {ds} 체크포인트가 아님")

  name = env_of(svc["bentoml"]).get("BENTOML_MODEL_NAME")
  if name != f"deeplog-{ds}":
    errors.append(f"bentoml BENTOML_MODEL_NAME={name!r}, 기대값 'deeplog-{ds}'")

  for s in ("triton", "triton-wrapper"):
    repos = [v for v in volume_sources(svc[s]) if "/serving/triton/models" in v]
    if len(repos) != 1 or not repos[0].endswith(f"/serving/triton/models/{ds}"):
      errors.append(f"{s} 모델 레포 마운트={repos}, 기대값 .../serving/triton/models/{ds}")

  label = dataset if dataset is not None else "<unset>"
  print(f"[compose] DEEPLOG_DATASET={label}: " + ("OK" if not errors else "FAIL"))
  return errors


def main() -> None:
  errors = []
  for ds in (None, "hdfs", "bgl"):
    errors += check(ds)
  if errors:
    print("\n".join(f"  - {e}" for e in errors))
    sys.exit(1)


if __name__ == "__main__":
  main()
