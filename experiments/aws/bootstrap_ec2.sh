#!/usr/bin/env bash
# EC2 인스턴스에서 SSH로 접속한 뒤 수동 실행 — 코드/데이터/체크포인트를 준비하고
# 세 서빙 스택을 벤치마크 직전 상태까지 만든다.
#
# 전제: setup_instance.sh(user-data)가 이미 끝난 상태
#       (nvidia-smi, docker compose version, aws --version이 모두 동작해야 함)
#
# 호스트 Python에는 torch/bentoml을 설치하지 않는다 — 학습은 이미 로컬에서
# 끝났고(체크포인트를 S3에서 받아옴), BentoML 모델 등록도 컨테이너 안의
# Python으로 실행한다(`docker compose run`). 호스트에는 benchmark.py/
# plot_results.py를 돌릴 만큼만(httpx/numpy/pandas/matplotlib/psutil) 설치한다.
#
# 필수 환경변수:
#   DEEPLOG_S3_BUCKET   S3 버킷 이름 (sync_to_s3.sh와 동일 값)
#
# 선택 환경변수:
#   GITHUB_REPO   기본값: https://github.com/sungsuhan/deeplog-serving-analysis.git
#   GIT_REF       기본값: main
#   DATASETS      기본값: "hdfs bgl" (S3에 없는 데이터셋은 자동 건너뜀)
#
# 사용법 (EC2 위에서):
#   export DEEPLOG_S3_BUCKET=my-deeplog-thesis-bucket
#   aws configure   # 최초 1회 — S3 접근용 자격 증명
#   bash bootstrap_ec2.sh
set -euo pipefail

: "${DEEPLOG_S3_BUCKET:?DEEPLOG_S3_BUCKET 환경변수를 설정하세요}"
GITHUB_REPO="${GITHUB_REPO:-https://github.com/sungsuhan/deeplog-serving-analysis.git}"
GIT_REF="${GIT_REF:-main}"
DATASETS="${DATASETS:-hdfs bgl}"
S3_PREFIX="s3://${DEEPLOG_S3_BUCKET}/deeplog"
REPO_DIR="$HOME/deeplog-serving-analysis"

log() { echo "[bootstrap] $(date -u +%H:%M:%S)  $*"; }

# ── 1. 코드 ────────────────────────────────────────────────────────────────
if [[ -d "$REPO_DIR/.git" ]]; then
  log "레포 이미 존재 — pull"
  git -C "$REPO_DIR" fetch origin
  git -C "$REPO_DIR" checkout "$GIT_REF"
  git -C "$REPO_DIR" pull origin "$GIT_REF"
else
  log "레포 clone: $GITHUB_REPO ($GIT_REF)"
  git clone --branch "$GIT_REF" "$GITHUB_REPO" "$REPO_DIR"
fi
cd "$REPO_DIR"

# ── 2. .env ───────────────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  log ".env 없음 — .env.example 복사 (필요시 직접 값 채울 것)"
  cp .env.example .env
fi

# ── 3. 호스트 Python venv (벤치마크 클라이언트용) ─────────────────────────────
if [[ ! -d .venv ]]; then
  log "venv 생성 + experiments/requirements.txt 설치"
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip -q
  ./.venv/bin/pip install -r experiments/requirements.txt -q
else
  log "venv 이미 존재 — 건너뜀"
fi

# ── 4. 데이터·체크포인트를 S3에서 내려받기 ────────────────────────────────────
for ds in $DATASETS; do
  if aws s3 ls "$S3_PREFIX/data/processed/$ds/" >/dev/null 2>&1; then
    log "data/processed/$ds 다운로드"
    mkdir -p "data/processed/$ds"
    aws s3 sync "$S3_PREFIX/data/processed/$ds/" "data/processed/$ds/" --only-show-errors
  else
    log "S3에 data/processed/$ds 없음 — 건너뜀"
  fi

  if aws s3 ls "$S3_PREFIX/model/checkpoints/$ds/" >/dev/null 2>&1; then
    log "model/checkpoints/$ds 다운로드"
    mkdir -p "model/checkpoints/$ds"
    aws s3 sync "$S3_PREFIX/model/checkpoints/$ds/" "model/checkpoints/$ds/" --only-show-errors
  else
    log "S3에 model/checkpoints/$ds 없음 — 건너뜀 (해당 데이터셋 학습 미완료)"
  fi
done

# ── 5. Triton 모델 레포지토리 생성 (체크포인트가 있는 데이터셋만) ────────────────
for ds in $DATASETS; do
  if [[ -f "model/checkpoints/$ds/deeplog.ts" ]]; then
    log "Triton 모델 레포지토리 생성: $ds"
    python3 serving/triton/setup_model.py --dataset "$ds"
  fi
done

# ── 6. Docker 이미지 빌드 ─────────────────────────────────────────────────────
log "Docker 이미지 빌드 (fastapi, bentoml, triton-wrapper)"
docker compose -f docker/docker-compose.yml build fastapi bentoml triton-wrapper

# ── 7. BentoML 모델 등록 (컨테이너 안 Python 사용 — 호스트에 bentoml 불필요) ────
for ds in $DATASETS; do
  if [[ -f "model/checkpoints/$ds/deeplog_best.pt" ]]; then
    log "BentoML 모델 등록: $ds"
    docker compose -f docker/docker-compose.yml run --rm bentoml \
      python serving/bentoml/save_model.py --dataset "$ds"
  fi
done

log "완료."
log ""
log "다음 단계:"
log "  1. 스팟 백업 워처 기동:"
log "     DEEPLOG_S3_BUCKET=$DEEPLOG_S3_BUCKET nohup bash experiments/aws/spot_backup_watcher.sh > /tmp/spot_watcher.log 2>&1 &"
log "  2. 벤치마크 실행:"
log "     source .venv/bin/activate && bash experiments/run_benchmark.sh hdfs"
