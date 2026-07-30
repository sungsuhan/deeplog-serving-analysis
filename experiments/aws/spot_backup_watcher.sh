#!/usr/bin/env bash
# 스팟 인터럽션 감지 + 주기적 백업 워처.
#
# EC2 스팟 인스턴스는 회수 2분 전에 인스턴스 메타데이터에 인터럽션 통지를
# 띄운다(http://169.254.169.254/latest/meta-data/spot/instance-action).
# 이 스크립트는 그 엔드포인트를 짧은 주기로 폴링하다가 통지가 뜨는 즉시
# model/checkpoints·experiments/results를 S3로 백업한다. 통지가 없어도
# BACKUP_EVERY_S마다 정기 백업을 겸해 하드웨어 장애 등 통지 없는 종료에도 대비한다.
#
# S3가 아니라 EBS를 쓰지 않은 이유: launch_spot_instance.sh의 루트 볼륨은
# DeleteOnTermination=true라 인스턴스가 죽으면 볼륨도 같이 사라진다. 별도
# EBS를 붙이는 방법도 있지만 볼륨 프로비저닝·마운트가 늘어나는 데 비해
# 이 프로젝트의 백업 대상(체크포인트 수백 KB~결과 CSV)은 S3 sync로 충분하다.
#
# 필수 환경변수:
#   DEEPLOG_S3_BUCKET
#
# 사용법 (EC2 위, 백그라운드로):
#   DEEPLOG_S3_BUCKET=my-bucket nohup bash spot_backup_watcher.sh > /tmp/spot_watcher.log 2>&1 &
set -uo pipefail   # -e는 의도적으로 뺌: curl 실패(통지 없음)가 정상 상태이므로

: "${DEEPLOG_S3_BUCKET:?DEEPLOG_S3_BUCKET 환경변수를 설정하세요}"
POLL_INTERVAL_S="${POLL_INTERVAL_S:-10}"     # 인터럽션 메타데이터 확인 주기
BACKUP_EVERY_S="${BACKUP_EVERY_S:-300}"      # 정기 백업 주기 (기본 5분)
S3_PREFIX="s3://${DEEPLOG_S3_BUCKET}/deeplog"
META_URL="http://169.254.169.254/latest/meta-data/spot/instance-action"

_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$_PROJECT_ROOT"

log() { echo "[watcher] $(date -u +%H:%M:%S)  $*"; }

backup() {
  log "백업 시작 -> $S3_PREFIX"
  [[ -d model/checkpoints ]] && aws s3 sync model/checkpoints/ "$S3_PREFIX/model/checkpoints/" --only-show-errors
  [[ -d experiments/results ]] && aws s3 sync experiments/results/ "$S3_PREFIX/experiments/results/" --only-show-errors
  log "백업 완료"
}

log "시작 (poll=${POLL_INTERVAL_S}s, 정기백업=${BACKUP_EVERY_S}s)"
elapsed_since_backup=0

while true; do
  if curl -s -f -o /dev/null --max-time 3 "$META_URL"; then
    log "!!! 스팟 인터럽션 통지 감지 — 즉시 백업 후 종료 !!!"
    backup
    exit 0
  fi

  sleep "$POLL_INTERVAL_S"
  elapsed_since_backup=$((elapsed_since_backup + POLL_INTERVAL_S))

  if (( elapsed_since_backup >= BACKUP_EVERY_S )); then
    backup
    elapsed_since_backup=0
  fi
done
