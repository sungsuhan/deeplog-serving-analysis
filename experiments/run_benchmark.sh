#!/usr/bin/env bash
# Full benchmark pipeline — 세 서빙 프레임워크에 대해 순차 기동 → 측정 → 종료를
# 반복하며 부하 테스트를 수행한다.
#
# CLAUDE.md 벤치마크 측정 규칙 준수:
#   - 세 프레임워크를 동시에 실행하지 않는다 — 컨테이너를 프레임워크 1개씩
#     기동 → 측정 → 종료한 뒤 다음 프레임워크로 넘어간다.
#   - 순서 효과(order effect) 방지를 위해 측정 사이클마다 프레임워크 실행
#     순서를 랜덤화한다.
#   - 5회 반복 측정 (CYCLES=5) — n=3에서는 Mann-Whitney U(양측)의 최소 도달
#     가능 p-value가 약 0.1이라 유의수준 0.05에 도달할 수 없다(stat_test.py 참고).
#
# Prerequisites (측정할 데이터셋마다):
#   1. python model/train.py --dataset <ds>
#   2. python serving/bentoml/save_model.py --dataset <ds>   → BentoML 모델 deeplog-<ds>
#   3. python serving/triton/setup_model.py --dataset <ds>   → serving/triton/models/<ds>/
#
# 데이터셋 선택: 이 스크립트가 데이터셋마다 DEEPLOG_DATASET을 export해
# docker-compose가 세 프레임워크 모두 그 데이터셋 모델을 띄우게 한다. 그리고
# 측정 직전에 각 서버 /health의 dataset 필드가 기대값과 같은지 확인해, 다르면
# 측정 없이 즉시 중단한다 — 다른 모델을 서빙하는 상태로 측정이 조용히 진행되는
# 것을 막기 위함(2026-10-31 추가).
#   4. docker compose -f docker/docker-compose.yml build
#      (컨테이너는 이 스크립트가 프레임워크별로 개별 기동/종료하므로 미리
#       `docker compose up`으로 전체를 띄워두지 않는다 — 동시 실행 규칙 위반)
#
# Usage:
#   bash experiments/run_benchmark.sh [hdfs|bgl|all]
#
# 기존 사이클에 이어서 추가로 돌리고 싶으면(예: 3사이클 결과에 4~5회차를
# 덧붙임) CYCLE_START를 같이 지정한다 — cycle{1..N} 디렉토리가 프레임워크별
# CSV 파일 단위라 겹치지 않는 한 기존 사이클을 덮어쓰지 않는다.
#   CYCLE_START=4 CYCLES=5 bash experiments/run_benchmark.sh hdfs
#
# 환경변수로 조정 가능: DURATION, CONCURRENCY, CYCLES, CYCLE_START, RESULTS_DIR
#
# RESULTS_DIR는 로컬 드라이런 전용이다 — 로컬 결과는 experiments/results/에 저장하지
# 않는다는 규칙(CLAUDE.md)을 지키려고 출력 위치만 바꾼다. 공식 측정(EC2)은 지정하지
# 않고 기본값 그대로 실행한다.
#   RESULTS_DIR=/tmp/dryrun DURATION=5 CONCURRENCY=1 CYCLES=1 bash experiments/run_benchmark.sh bgl
set -euo pipefail

DATASET="${1:-all}"
DURATION="${DURATION:-30}"
CONCURRENCY="${CONCURRENCY:-1 4 8 16 32 64}"
CYCLES="${CYCLES:-5}"
CYCLE_START="${CYCLE_START:-1}"
RESULTS_DIR="${RESULTS_DIR:-experiments/results}"
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
  # --max-time 필수: 서버 프로세스가 크래시/행 상태여도 TCP 연결 자체는
  # accept될 수 있어(docker-proxy가 포트를 열어둔 채로) curl이 응답을
  # 무한정 기다리는 사고가 실제로 있었다(2026-08-05, bentoml 모델 미등록으로
  # 앱이 죽어있는데 curl만 20분 넘게 걸려있었음) — 타임아웃 없으면 이 루프
  # 자체가 절대 안 끝난다.
  local curl_opts=(-sf --max-time 5)
  # BentoML 1.3 신규 SDK의 /health는 GET을 거부하고 POST만 받는다
  # (CLAUDE.md 검증 절차 참고) — GET으로 두면 이 프레임워크만 항상 타임아웃난다.
  if [[ "$label" == "bentoml" ]]; then
    curl_opts+=(-X POST -d '{}')
  fi
  until curl "${curl_opts[@]}" "$url/health" > /dev/null 2>&1; do
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
  until curl -sf --max-time 5 "http://localhost:8001/v2/health/ready" > /dev/null 2>&1; do
    sleep 2
    waited=$((waited + 2))
    if (( waited >= HEALTH_TIMEOUT_S )); then
      echo "[FAIL] triton 서버 — ${HEALTH_TIMEOUT_S}s 내 ready 상태가 되지 않음"
      return 1
    fi
  done
  echo "  triton 서버 ready (${waited}s)"
}

# 서버가 실제로 서빙 중인 데이터셋을 /health 응답에서 읽는다(세 프레임워크 공통
# 필드). BentoML은 wait_for_health와 같은 이유로 POST.
served_dataset() {
  local url="$1" label="$2"
  local curl_opts=(-sf --max-time 5)
  if [[ "$label" == "bentoml" ]]; then
    curl_opts+=(-X POST -d '{}')
  fi
  local body
  body="$(curl "${curl_opts[@]}" "$url/health")"
  $PYTHON -c 'import json,sys; print(json.loads(sys.argv[1]).get("dataset"))' "$body"
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

  local served
  served="$(served_dataset "$endpoint" "$framework" || true)"
  if [[ "$served" != "$dataset" ]]; then
    echo "[ABORT] $framework 서버가 '$served' 모델을 서빙 중 (기대값: $dataset)."
    echo "        save_model.py / setup_model.py를 --dataset $dataset로 실행했는지 확인하세요."
    compose_down "${services[@]}"
    exit 1
  fi
  echo "  $framework 서빙 데이터셋 확인: $served"
  echo "cycle=$cycle framework=$framework served_dataset=$served" >> "$MANIFEST"

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
  export DEEPLOG_DATASET="$dataset"
  for cycle in $(seq "$CYCLE_START" "$CYCLES"); do
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
echo "  Benchmark complete — cycles $CYCLE_START..$CYCLES"
echo "  Results in : $RESULTS_DIR/cycle{$CYCLE_START..$CYCLES}/"
echo "  Manifest   : $MANIFEST"
echo "════════════════════════════════════"
ls -lh "$RESULTS_DIR"
