#!/usr/bin/env bash
# 소형 합성 데이터로 학습 → 서빙 패키징 → 세 프레임워크 기동 → 응답 일치 검사.
#
# GitHub Actions(.github/workflows/ci.yml)와 로컬에서 같은 스크립트를 쓴다.
# 모든 산출물은 CI_ROOT 아래에만 쓴다 — 저장소의 data/processed/, model/checkpoints/,
# serving/triton/models/, experiments/results/는 건드리지 않는다.
#
# 성능 측정이 아니다. GPU 없는 환경을 전제로 전부 CPU로 돌리며, Triton도
# --instance-kind KIND_CPU로 띄운다(실험 설정의 기본값 KIND_GPU는 그대로).
#
# 필요: python(ci/requirements.txt 설치됨), docker(Triton 서버 이미지 실행용)
#
# Usage:
#   CI_ROOT=/tmp/ci bash ci/smoke.sh
#
# 환경변수: CI_ROOT(필수), PYTHON(기본 python), TRITON_IMAGE, EPOCHS(기본 2),
#           KEEP_RUNNING=1(끝나도 서버를 내리지 않음 — 디버깅용)
#           SKIP_TRITON=1(Docker 없는 로컬 개발용 — Triton을 빼고 FastAPI·BentoML만
#                         비교. CI는 이 값을 쓰지 않고 항상 세 프레임워크를 검사한다)
set -euo pipefail

: "${CI_ROOT:?CI_ROOT를 지정하세요 (산출물을 쓸 임시 디렉토리)}"
PYTHON="${PYTHON:-python}"
TRITON_IMAGE="${TRITON_IMAGE:-nvcr.io/nvidia/tritonserver:24.08-py3}"
EPOCHS="${EPOCHS:-2}"
DS=hdfs
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

R="$CI_ROOT"
mkdir -p "$R/logs"
export PYTHONPATH="$REPO_ROOT"
export BENTOML_HOME="$R/bentoml"
export CUDA_VISIBLE_DEVICES=-1        # 로컬 GPU가 있어도 CPU만 쓴다
# --batch-size 128: 기본값(2048)이면 합성 데이터 2만 개로 에폭당 9스텝뿐이라 모델이
# 거의 배우지 못한다. 후보 확률이 서로 비슷해지면 프레임워크 간 미세한 수치 차이로
# 순서가 뒤바뀌어 응답 비교가 불안정해지므로, 스텝을 늘려 뚜렷한 1순위를 학습시킨다.
# (학습 설정은 이 검사 전용이며 실제 실험 기본값과 무관하다.)
TRAIN=("$PYTHON" model/train.py --dataset "$DS" --data-dir "$R/data"
       --ckpt-dir "$R/ckpt" --epochs "$EPOCHS" --batch-size 128)
PIDS=()
TRITON_CONTAINER="deeplog-ci-triton-$$"

step() { echo; echo "── $* ──"; }

cleanup() {
  local rc=$?
  if [[ "${KEEP_RUNNING:-0}" != "1" ]]; then
    for p in "${PIDS[@]:-}"; do [[ -n "$p" ]] && kill "$p" 2>/dev/null || true; done
    docker rm -f "$TRITON_CONTAINER" >/dev/null 2>&1 || true
  fi
  if (( rc != 0 )); then
    echo; echo "[smoke] 실패 (exit $rc) — 서버 로그 마지막 부분:"
    for f in "$R"/logs/*.log; do [[ -f "$f" ]] && { echo "=== $f"; tail -n 40 "$f"; }; done
    docker logs "$TRITON_CONTAINER" 2>&1 | tail -n 40 || true
  fi
}
trap cleanup EXIT

step "1. 합성 데이터"
"$PYTHON" ci/make_fixture.py --root "$R" --dataset "$DS"

step "2. 학습 (${EPOCHS}에폭, CPU)"
"${TRAIN[@]}"
for f in deeplog_best.pt deeplog.ts config.json train_state.pt; do
  [[ -f "$R/ckpt/$DS/$f" ]] || { echo "[FAIL] 학습 산출물 없음: $f"; exit 1; }
done

step "3. 이어 학습 안전장치"
# 진행 상태가 있는데 --resume 없이 실행하면 덮어쓰지 않고 거부해야 한다
if "${TRAIN[@]}" > "$R/logs/guard.log" 2>&1; then
  echo "[FAIL] --resume 없이 재실행했는데 거부되지 않음"; exit 1
fi
grep -q "train_state.pt" "$R/logs/guard.log" || { echo "[FAIL] 거부 사유가 예상과 다름"; cat "$R/logs/guard.log"; exit 1; }
echo "  --resume 없는 재실행: 거부됨 (정상)"
# 설정이 다르면 재개를 거부해야 한다
if "${TRAIN[@]}" --resume --lr 0.01 > "$R/logs/mismatch.log" 2>&1; then
  echo "[FAIL] 설정이 다른데 재개가 거부되지 않음"; exit 1
fi
echo "  설정이 다른 --resume: 거부됨 (정상)"
# 이미 끝난 학습을 --resume하면 산출물만 다시 만들고 끝나야 한다
"${TRAIN[@]}" --resume > "$R/logs/resume.log" 2>&1
grep -q "DONE" "$R/logs/resume.log" || { echo "[FAIL] 완료된 학습의 --resume이 끝나지 않음"; exit 1; }
echo "  완료된 학습의 --resume: 산출물 재생성 후 종료 (정상)"

step "4. 서빙 패키징"
"$PYTHON" serving/triton/setup_model.py --dataset "$DS" --ckpt-dir "$R/ckpt/$DS" \
  --model-repo "$R/triton/$DS" --instance-kind KIND_CPU
"$PYTHON" serving/bentoml/save_model.py --dataset "$DS" --ckpt-dir "$R/ckpt/$DS"

step "5. 서버 기동"
SKIP_TRITON="${SKIP_TRITON:-0}"
FRAMEWORKS=(fastapi bentoml triton)

DATASET="$DS" CKPT_DIR="$R/ckpt/$DS" \
  "$PYTHON" -m uvicorn serving.fastapi.app:app --host 127.0.0.1 --port 8000 \
  > "$R/logs/fastapi.log" 2>&1 &
PIDS+=($!)
BENTOML_MODEL_NAME="deeplog-$DS" BENTOML_MODEL_TAG=latest \
  "$PYTHON" -m bentoml serve serving.bentoml.service:DeepLogService --host 127.0.0.1 --port 3000 \
  > "$R/logs/bentoml.log" 2>&1 &
PIDS+=($!)

if [[ "$SKIP_TRITON" == "1" ]]; then
  echo "  [주의] SKIP_TRITON=1 — Triton 없이 FastAPI·BentoML만 비교한다(로컬 전용)"
  FRAMEWORKS=(fastapi bentoml)
else
  # Docker Desktop(Windows)에서도 마운트되도록 경로를 docker가 읽는 형식으로 바꾼다
  TRITON_REPO="$R/triton/$DS"
  if command -v cygpath >/dev/null 2>&1; then TRITON_REPO="$(cygpath -m "$TRITON_REPO")"; fi
  docker run -d --rm --name "$TRITON_CONTAINER" -p 8001:8001 \
    -v "$TRITON_REPO:/models" "$TRITON_IMAGE" \
    tritonserver --model-repository=/models \
    --http-port=8001 --grpc-port=8003 --metrics-port=8004 > /dev/null
  echo "  triton 서버 컨테이너: $TRITON_CONTAINER"

  waited=0
  until curl -sf --max-time 5 http://localhost:8001/v2/health/ready > /dev/null 2>&1; do
    sleep 2; waited=$((waited + 2))
    (( waited < 300 )) || { echo "[FAIL] triton 서버가 300s 안에 ready가 되지 않음"; exit 1; }
  done
  echo "  triton 서버 ready (${waited}s)"
  TRITON_URL=localhost:8001 MODEL_NAME=deeplog MODEL_REPO="$R/triton/$DS" \
    "$PYTHON" -m uvicorn serving.triton.app:app --host 127.0.0.1 --port 8002 \
    > "$R/logs/triton-wrapper.log" 2>&1 &
  PIDS+=($!)
fi

step "6. 응답 비교 (${FRAMEWORKS[*]})"
"$PYTHON" ci/check_serving.py --dataset "$DS" --test-data "$R/data/$DS/test.npy" \
  --frameworks "${FRAMEWORKS[@]}"

echo; echo "[smoke] 전부 통과"
