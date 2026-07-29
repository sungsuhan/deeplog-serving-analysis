#!/usr/bin/env bash
# Full benchmark pipeline — 세 서빙 프레임워크에 대해 순차 기동 → 측정 → 종료를
# 반복하며 부하 테스트를 수행한다.
#
# CLAUDE.md 벤치마크 측정 규칙 준수:
#   - 세 프레임워크를 동시에 실행하지 않는다 — 컨테이너를 프레임워크 1개씩
#     기동 → 측정 → 종료한 뒤 다음 프레임워크로 넘어간다.
#   - 순서 효과(order effect) 방지를 위해 측정 사이클마다 프레임워크 실행
#     순서를 랜덤화한다.
#   - 3회 반복 측정 (CYCLES=3).
#
# Prerequisites:
#   1. python model/train.py --dataset hdfs   (BGL도 비교하려면 --dataset bgl 추가)
#   2. python serving/bentoml/save_model.py --dataset hdfs
#   3. python serving/triton/setup_model.py --dataset hdfs
#   4. docker compose -f docker/docker-compose.yml build
#      (컨테이너는 이 스크립트가 프레임워크별로 개별 기동/종료하므로 미리
#       `docker compose up`으로 전체를 띄워두지 않는다 — 동시 실행 규칙 위반)
#
# Usage:
#   bash experiments/run_benchmark.sh [hdfs|bgl|all]
#
# 환경변수로 조정 가능: DURATION, CONCURRENCY, CYCLES
set -euo pipefail

DATASET="${1:-all}"
DURATION="${DURATION:-30}"
CONCURRENCY="${CONCURRENCY:-1 4 8 16 32 64}"
CYCLES="${CYCLES:-3}"
RESULTS_DIR="experiments/results"
PYTHON="python"
COMPOSE_FILE="docker/docker-compose.yml"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-120}"

FRAMEWORKS=("fastapi" "bentoml" "triton")

declare -A ENDPOINT=(
  [fastapi]="http://localhost:8000"
  [bentoml]="http://localhost:3000"
  [triton]="http://localhost:8002"
)

mkdir -p "$RESULTS_DIR"
MANIFEST="$RESULTS_DIR/run_manifest_$(date +%Y%m%d_%H%M%S).txt"
{
  echo "dataset=$DATASET duration=${DURATION}s concurrency=[$CONCURRENCY] cycles=$CYCLES"
  echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} | tee "$MANIFEST"

# ── 컨테이너 기동/종료 헬퍼 ────────────────────────────────────────────────────
compose_up() {
  docker compose -f "$COMPOSE_FILE" up -d "$@"
}

compose_down() {
  docker compose -f "$COMPOSE_FILE" stop "$@" >/dev/null 2>&1 || true
  docker compose -f "$COMPOSE_FILE" rm -f "$@" >/dev/null 2>&1 || true
}

container_id() {
  docker compose -f "$COMPOSE_FILE" ps -q "$1"
}

wait_for_health() {
  local url="$1" label="$2" waited=0
  until curl -sf "$url/health" > /dev/null 2>&1; do
    sleep 2
    waited=$((waited + 2))
    if (( waited >= HEALTH_TIMEOUT_S )); then
      echo "[FAIL] $label — ${HEALTH_TIMEOUT_S}s 내 healthy 상태가 되지 않음"
      return 1
    fi
  done
  echo "  $label healthy (${waited}s)"
}

wait_for_triton_ready() {
  local waited=0
  until curl -sf "http://localhost:8001/v2/health/ready" > /dev/null 2>&1; do
    sleep 2
    waited=$((waited + 2))
    if (( waited >= HEALTH_TIMEOUT_S )); then
      echo "[FAIL] triton 서버 — ${HEALTH_TIMEOUT_S}s 내 ready 상태가 되지 않음"
      return 1
    fi
  done
  echo "  triton 서버 ready (${waited}s)"
}

# ── 프레임워크 1개 기동 → 측정 → 종료 ────────────────────────────────────────
run_framework() {
  local framework="$1" dataset="$2" cycle="$3"
  local endpoint="${ENDPOINT[$framework]}"
  local out_dir="$RESULTS_DIR/cycle${cycle}"
  mkdir -p "$out_dir"

  echo ""
  echo "═══════════════════════════════════════════════"
  echo "  cycle     : $cycle/$CYCLES"
  echo "  framework : $framework"
  echo "  dataset   : $dataset"
  echo "═══════════════════════════════════════════════"

  local services monitor_container
  if [[ "$framework" == "triton" ]]; then
    services=(triton triton-wrapper)
    echo "  [up] triton (모델 로드 대기)"
    compose_up triton
    if ! wait_for_triton_ready; then
      compose_down "${services[@]}"
      return 0
    fi
    echo "  [up] triton-wrapper"
    compose_up triton-wrapper
    monitor_container="$(container_id triton)"
  else
    services=("$framework")
    compose_up "$framework"
    monitor_container="$(container_id "$framework")"
  fi

  if ! wait_for_health "$endpoint" "$framework"; then
    compose_down "${services[@]}"
    return 0
  fi

  $PYTHON experiments/benchmark.py \
    --framework   "$framework" \
    --endpoint    "$endpoint" \
    --dataset     "$dataset" \
    --concurrency $CONCURRENCY \
    --duration    "$DURATION" \
    --output      "$out_dir" \
    --container   "$monitor_container" \
    --monitor-gpu

  echo "  [down] $framework"
  compose_down "${services[@]}"
}

# ── 순서 랜덤화 + 사이클 반복 ─────────────────────────────────────────────────
run_dataset() {
  local dataset="$1"
  for cycle in $(seq 1 "$CYCLES"); do
    local order
    order=($(shuf -e "${FRAMEWORKS[@]}"))
    echo ""
    echo "▶ Dataset: $dataset — Cycle $cycle/$CYCLES — order: ${order[*]}"
    echo "cycle=$cycle dataset=$dataset order=${order[*]}" >> "$MANIFEST"
    for framework in "${order[@]}"; do
      run_framework "$framework" "$dataset" "$cycle"
    done
  done
}

if [[ "$DATASET" == "all" ]]; then
  run_dataset "hdfs"
  run_dataset "bgl"
else
  run_dataset "$DATASET"
fi

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$MANIFEST"

echo ""
echo "════════════════════════════════════"
echo "  Benchmark complete — $CYCLES cycles"
echo "  Results in : $RESULTS_DIR/cycle{1..$CYCLES}/"
echo "  Manifest   : $MANIFEST"
echo "════════════════════════════════════"
ls -lh "$RESULTS_DIR"
