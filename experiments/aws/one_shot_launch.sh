#!/usr/bin/env bash
# quota 승인 후 이것 하나만 실행 — 로컬 데이터 업로드부터 EC2에서 벤치마크
# 직전 상태(이미지 빌드 + 모델 등록 + 백업 워처 기동)까지 전부 자동으로 간다.
#
# 의도적으로 자동 실행하지 않는 것: experiments/run_benchmark.sh 자체.
# 결과를 만들어내는 마지막 단계는 사람이 직접 트리거하는 게 맞다고 판단했다
# (컨테이너 상태·데이터셋 선택을 마지막으로 눈으로 확인할 기회를 남겨둠).
#
# 필수 환경변수 — launch_spot_instance.sh와 동일 + 아래 추가분:
#   EC2_SUBNET_ID, EC2_SECURITY_GROUP_ID, EC2_KEY_NAME   (계정별 리소스)
#   EC2_SSH_KEY_PATH   EC2_KEY_NAME과 짝이 맞는 .pem 파일의 로컬 경로
#   DEEPLOG_S3_BUCKET  S3 버킷 이름 (미리 생성해둘 것: aws s3 mb s3://<bucket>)
#
# 선택 환경변수: AWS_REGION, EC2_INSTANCE_TYPE, EC2_SPOT_MAX_PRICE,
#               EC2_VOLUME_SIZE_GB, GITHUB_REPO, GIT_REF, DATASETS
#               (각 하위 스크립트의 기본값을 그대로 물려받음)
#   EC2_LAUNCH_MODE     spot(기본값) | ondemand — spot은 저렴하지만 용량 부족
#                       시 인스턴스가 회수될 수 있음(2026-08-04 실제로 겪음).
#                       마감이 임박해 신뢰성이 더 중요하면 ondemand로.
#
# 사용법:
#   aws configure                     # 자격증명 최초 1회
#   aws s3 mb s3://my-deeplog-bucket   # 버킷 최초 1회
#   export EC2_SUBNET_ID=subnet-xxxx EC2_SECURITY_GROUP_ID=sg-xxxx \
#          EC2_KEY_NAME=my-key EC2_SSH_KEY_PATH=~/.ssh/my-key.pem \
#          DEEPLOG_S3_BUCKET=my-deeplog-bucket
#   bash experiments/aws/one_shot_launch.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${EC2_SUBNET_ID:?EC2_SUBNET_ID 환경변수를 설정하세요}"
: "${EC2_SECURITY_GROUP_ID:?EC2_SECURITY_GROUP_ID 환경변수를 설정하세요}"
: "${EC2_KEY_NAME:?EC2_KEY_NAME 환경변수를 설정하세요}"
: "${EC2_SSH_KEY_PATH:?EC2_SSH_KEY_PATH 환경변수를 설정하세요 (.pem 경로)}"
: "${DEEPLOG_S3_BUCKET:?DEEPLOG_S3_BUCKET 환경변수를 설정하세요}"

REGION="${AWS_REGION:-ap-northeast-2}"
GITHUB_REPO="${GITHUB_REPO:-https://github.com/sungsuhan/deeplog-serving-analysis.git}"
GIT_REF="${GIT_REF:-main}"
# 2026-08-04 실측: NVIDIA 드라이버+CUDA 툴킷 설치만 8분 내외 소요, 재부팅까지
# 포함하면 10분(600s) 기본값으로는 부족했다 — 25분으로 여유를 둠.
SSH_READY_TIMEOUT_S="${SSH_READY_TIMEOUT_S:-1500}"
BOOTSTRAP_TIMEOUT_S="${BOOTSTRAP_TIMEOUT_S:-1800}"
EC2_LAUNCH_MODE="${EC2_LAUNCH_MODE:-spot}"

step() { echo ""; echo "════════ $* ════════"; }

command -v aws >/dev/null || { echo "[FAIL] aws CLI가 로컬에 설치돼 있지 않습니다."; exit 1; }
aws sts get-caller-identity >/dev/null 2>&1 || { echo "[FAIL] aws configure로 자격증명을 먼저 설정하세요."; exit 1; }
[[ -f "$EC2_SSH_KEY_PATH" ]] || { echo "[FAIL] SSH 키 파일이 없습니다: $EC2_SSH_KEY_PATH"; exit 1; }

# ── 1/5. 로컬 데이터·체크포인트 S3 업로드 ─────────────────────────────────────
step "1/5  데이터·체크포인트 S3 업로드"
DEEPLOG_S3_BUCKET="$DEEPLOG_S3_BUCKET" bash "$SCRIPT_DIR/sync_to_s3.sh"

# ── 2/5. 인스턴스 기동 (spot 또는 ondemand) ───────────────────────────────────
if [[ "$EC2_LAUNCH_MODE" == "ondemand" ]]; then
  step "2/5  온디맨드 인스턴스 기동"
  LAUNCHER="launch_ondemand_instance.sh"
else
  step "2/5  스팟 인스턴스 기동"
  LAUNCHER="launch_spot_instance.sh"
fi
LAUNCH_LOG="$(mktemp)"
AWS_REGION="$REGION" EC2_SUBNET_ID="$EC2_SUBNET_ID" \
  EC2_SECURITY_GROUP_ID="$EC2_SECURITY_GROUP_ID" EC2_KEY_NAME="$EC2_KEY_NAME" \
  bash "$SCRIPT_DIR/$LAUNCHER" | tee "$LAUNCH_LOG"

PUBLIC_IP="$(grep -oE 'Public IP\s*:\s*[0-9.]+' "$LAUNCH_LOG" | grep -oE '[0-9.]+$' | tail -1)"
INSTANCE_ID="$(grep -oE '인스턴스 ID\s*:\s*i-[a-z0-9]+' "$LAUNCH_LOG" | awk '{print $NF}' | tail -1)"
[[ -n "$PUBLIC_IP" && -n "$INSTANCE_ID" ]] || {
  echo "[FAIL] Public IP/인스턴스 ID를 로그에서 못 찾았습니다 — $LAUNCH_LOG 확인"
  exit 1
}
echo "[one-shot] instance=$INSTANCE_ID  ip=$PUBLIC_IP"

SSH_OPTS=(-i "$EC2_SSH_KEY_PATH" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5)
ssh_run() { ssh "${SSH_OPTS[@]}" "ubuntu@$PUBLIC_IP" "$@"; }

# ── 3/5. SSH 접속 + user-data(setup_instance.sh) 완료 대기 ────────────────────
step "3/5  프로비저닝 대기 (드라이버 설치 시 재부팅 포함, 통상 5~10분)"
waited=0
until ssh_run "test -f /var/lib/setup_instance_done" 2>/dev/null; do
  sleep 15
  waited=$((waited + 15))
  echo "  대기 중... ${waited}s"
  if (( waited >= SSH_READY_TIMEOUT_S )); then
    echo "[FAIL] ${SSH_READY_TIMEOUT_S}s 내 프로비저닝이 끝나지 않았습니다."
    echo "  수동 확인: ssh -i $EC2_SSH_KEY_PATH ubuntu@$PUBLIC_IP 'tail -f /var/log/cloud-init-output.log'"
    exit 1
  fi
done
echo "[one-shot] setup_instance.sh 완료 신호 확인됨"

# 드라이버 설치 재부팅으로 SSH가 일시적으로 끊길 수 있어 nvidia-smi로 재확인
waited=0
until ssh_run "nvidia-smi >/dev/null 2>&1" 2>/dev/null; do
  sleep 10
  waited=$((waited + 10))
  if (( waited >= SSH_READY_TIMEOUT_S )); then
    echo "[FAIL] nvidia-smi가 응답하지 않습니다 — 드라이버 설치 상태를 수동 확인하세요."
    exit 1
  fi
done
echo "[one-shot] nvidia-smi 확인 완료"

# ── 4/5. bootstrap_ec2.sh 원격 실행 ───────────────────────────────────────────
step "4/5  bootstrap_ec2.sh 원격 실행 (레포 clone + S3 다운로드 + 이미지 빌드 + 모델 등록)"
scp "${SSH_OPTS[@]}" "$SCRIPT_DIR/bootstrap_ec2.sh" "ubuntu@$PUBLIC_IP:/tmp/bootstrap_ec2.sh"
ssh_run "DEEPLOG_S3_BUCKET='$DEEPLOG_S3_BUCKET' GITHUB_REPO='$GITHUB_REPO' GIT_REF='$GIT_REF' timeout $BOOTSTRAP_TIMEOUT_S bash /tmp/bootstrap_ec2.sh"

# ── 5/5. 스팟 백업 워처 기동 ───────────────────────────────────────────────────
step "5/5  스팟 백업 워처 기동"
scp "${SSH_OPTS[@]}" "$SCRIPT_DIR/spot_backup_watcher.sh" "ubuntu@$PUBLIC_IP:/tmp/spot_backup_watcher.sh"
ssh_run "cd ~/deeplog-serving-analysis && DEEPLOG_S3_BUCKET='$DEEPLOG_S3_BUCKET' nohup bash /tmp/spot_backup_watcher.sh > /tmp/spot_watcher.log 2>&1 & disown"

cat <<SUMMARY

════════════════════════════════════════
  준비 완료 — 벤치마크 직전 상태
  Instance : $INSTANCE_ID
  IP       : $PUBLIC_IP
  SSH      : ssh -i $EC2_SSH_KEY_PATH ubuntu@$PUBLIC_IP
════════════════════════════════════════

experiments/run_benchmark.sh는 자동 실행하지 않았습니다 — 직접 확인 후 실행하세요:
  ssh -i $EC2_SSH_KEY_PATH ubuntu@$PUBLIC_IP
  cd deeplog-serving-analysis && source .venv/bin/activate
  bash experiments/run_benchmark.sh hdfs

백업 워처 로그 확인:
  ssh -i $EC2_SSH_KEY_PATH ubuntu@$PUBLIC_IP 'tail -f /tmp/spot_watcher.log'

종료(과금 중단):
  aws ec2 terminate-instances --region $REGION --instance-ids $INSTANCE_ID
SUMMARY
