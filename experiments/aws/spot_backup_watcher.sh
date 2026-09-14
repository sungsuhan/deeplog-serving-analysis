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
# 선택 환경변수:
#   DEEPLOG_PROJECT_ROOT  백업 대상 레포 경로를 명시적으로 지정. 생략하면 아래
#                         _resolve_project_root()가 자동 탐색한다.
#
# 사용법 (EC2 위, 백그라운드로):
#   DEEPLOG_S3_BUCKET=my-bucket nohup bash spot_backup_watcher.sh > /tmp/spot_watcher.log 2>&1 &
#
# 이 스크립트는 레포 밖(예: /tmp)으로 scp해서 실행하는 것을 정상 사용법으로
# 지원한다 — one_shot_launch.sh가 실제로 그렇게 띄운다.
set -uo pipefail   # -e는 의도적으로 뺌: curl 실패(통지 없음)가 정상 상태이므로

: "${DEEPLOG_S3_BUCKET:?DEEPLOG_S3_BUCKET 환경변수를 설정하세요}"
POLL_INTERVAL_S="${POLL_INTERVAL_S:-10}"     # 인터럽션 메타데이터 확인 주기
BACKUP_EVERY_S="${BACKUP_EVERY_S:-300}"      # 정기 백업 주기 (기본 5분)
S3_PREFIX="s3://${DEEPLOG_S3_BUCKET}/deeplog"
META_URL="http://169.254.169.254/latest/meta-data/spot/instance-action"

# ── 프로젝트 루트 탐색 ────────────────────────────────────────────────────────
# 예전에는 `${BASH_SOURCE[0]}/../..`만 썼는데, 이 스크립트를 /tmp로 scp해서
# 실행하면(one_shot_launch.sh의 실제 동작) 루트가 `/`로 계산돼 아래 backup()의
# 디렉토리 존재 검사가 전부 실패, **백업이 조용히 아무것도 안 하는** 버그가
# 있었다(2026-08-05 실측). 이제 후보를 순서대로 검증하고, 못 찾으면 조용히
# 넘어가지 않고 즉시 실패한다.
_looks_like_project_root() {
  [[ -n "$1" && -f "$1/experiments/run_benchmark.sh" && -d "$1/model" ]]
}

_resolve_project_root() {
  local candidate
  # 1) 명시적 지정  2) 실행 시점 CWD(one_shot_launch.sh가 레포로 cd 후 기동)
  # 3) 스크립트 위치 기준(레포 안에서 직접 실행)  4) bootstrap_ec2.sh의 clone 경로
  local -a candidates=(
    "${DEEPLOG_PROJECT_ROOT:-}"
    "$PWD"
    "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)"
    "$HOME/deeplog-serving-analysis"
  )
  for candidate in "${candidates[@]}"; do
    if _looks_like_project_root "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if ! _PROJECT_ROOT="$(_resolve_project_root)"; then
  echo "[watcher][FATAL] 프로젝트 루트를 찾지 못했습니다 — 백업 대상이 없어 종료합니다." >&2
  echo "                 DEEPLOG_PROJECT_ROOT=/path/to/deeplog-serving-analysis 로 직접 지정하세요." >&2
  exit 1
fi
cd "$_PROJECT_ROOT"

log() { echo "[watcher] $(date -u +%H:%M:%S)  $*"; }

backup() {
  local target found=0 failed=0
  log "백업 시작 -> $S3_PREFIX"
  for target in model/checkpoints experiments/results; do
    [[ -d "$target" ]] || continue
    found=$((found + 1))
    aws s3 sync "$target/" "$S3_PREFIX/$target/" --only-show-errors || failed=$((failed + 1))
  done
  # 조용한 실패를 막는다 — 이 프로젝트는 경로 버그(2026-08-05)와 EC2 자격증명
  # 누락(EXPERIMENT.md §스코프 이력 7번) 양쪽 모두로 '백업이 안 됐는데 로그상
  # 멀쩡해 보이는' 사고를 겪었다. 두 원인을 구분해서 남긴다.
  if (( found == 0 )); then
    log "[WARN] 백업할 디렉토리가 없습니다 (root=$_PROJECT_ROOT) — 아직 산출물이 없거나 경로가 잘못됐습니다."
  elif (( failed > 0 )); then
    log "[WARN] ${failed}/${found}개 디렉토리 백업 실패 — aws 자격증명·버킷($DEEPLOG_S3_BUCKET)을 확인하세요."
  else
    log "백업 완료 (${found}개 디렉토리)"
  fi
}
log "시작 (root=$_PROJECT_ROOT, poll=${POLL_INTERVAL_S}s, 정기백업=${BACKUP_EVERY_S}s)"
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
