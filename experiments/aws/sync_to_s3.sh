#!/usr/bin/env bash
# 로컬(랩탑)에서 검증 완료한 전처리 데이터 + 체크포인트를 S3에 업로드한다.
# EC2가 뜨면 bootstrap_ec2.sh가 이 S3 경로에서 그대로 내려받는다.
#
# 코드 자체는 S3를 거치지 않는다 — GitHub(origin)에 push해두면 EC2에서
# git clone으로 받는 게 더 간단하고 버전이 명확하다 (bootstrap_ec2.sh 참고).
#
# 필수 환경변수:
#   DEEPLOG_S3_BUCKET   S3 버킷 이름 (예: my-deeplog-thesis-bucket)
#
# 선택 환경변수:
#   DATASETS            동기화할 데이터셋 목록 (기본값: "hdfs bgl")
#
# 사용법:
#   export DEEPLOG_S3_BUCKET=my-deeplog-thesis-bucket
#   bash experiments/aws/sync_to_s3.sh
set -euo pipefail

: "${DEEPLOG_S3_BUCKET:?DEEPLOG_S3_BUCKET 환경변수를 설정하세요 (예: my-deeplog-thesis-bucket)}"
DATASETS="${DATASETS:-hdfs bgl}"
S3_PREFIX="s3://${DEEPLOG_S3_BUCKET}/deeplog"

command -v aws >/dev/null || { echo "[FAIL] aws CLI가 설치돼 있지 않습니다."; exit 1; }

_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$_PROJECT_ROOT"

for ds in $DATASETS; do
  if [[ -d "data/processed/$ds" ]]; then
    echo "[sync] data/processed/$ds -> $S3_PREFIX/data/processed/$ds/"
    aws s3 sync "data/processed/$ds/" "$S3_PREFIX/data/processed/$ds/" --only-show-errors
  else
    echo "[skip] data/processed/$ds 없음 (전처리 안 됨)"
  fi

  if [[ -d "model/checkpoints/$ds" ]]; then
    echo "[sync] model/checkpoints/$ds -> $S3_PREFIX/model/checkpoints/$ds/"
    aws s3 sync "model/checkpoints/$ds/" "$S3_PREFIX/model/checkpoints/$ds/" --only-show-errors
  else
    echo "[skip] model/checkpoints/$ds 없음 (학습 안 됨)"
  fi
done

echo ""
echo "완료. EC2에서 받으려면:"
echo "  export DEEPLOG_S3_BUCKET=$DEEPLOG_S3_BUCKET"
echo "  bash experiments/aws/bootstrap_ec2.sh"
