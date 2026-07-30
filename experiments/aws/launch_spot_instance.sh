#!/usr/bin/env bash
# g4dn.xlarge 스팟 인스턴스 요청 스크립트.
#
# setup_instance.sh(NVIDIA 드라이버 + CUDA + Docker + docker compose 플러그인)를
# user-data로 넘겨 부팅과 동시에 자동 프로비저닝한다.
#
# 이 스크립트는 실행하지 않으면 아무 것도 기동하지 않는다 — 실행 전 아래 필수
# 환경변수를 반드시 채우고, 계정 청구(스팟 요금)가 발생한다는 점을 인지할 것.
#
# 필수 환경변수 (계정별 리소스라 하드코딩하지 않음):
#   EC2_SUBNET_ID          예: subnet-0123abcd
#   EC2_SECURITY_GROUP_ID  예: sg-0123abcd — 인바운드 22(SSH), 8000/3000/8001/8002(서빙), 9092(Kafka) 필요
#   EC2_KEY_NAME           기존 EC2 키페어 이름
#
# 선택 환경변수:
#   AWS_REGION             기본값: ap-northeast-2 (.env.example과 동일)
#   EC2_INSTANCE_TYPE       기본값: g4dn.xlarge
#   EC2_AMI_ID              기본값: 비워두면 최신 Ubuntu 22.04 AMI 자동 조회
#   EC2_SPOT_MAX_PRICE      기본값: 0.30 (USD/h) — 온디맨드 $0.647/h(EXPERIMENT.md) 대비 상한
#   EC2_VOLUME_SIZE_GB      기본값: 100
#
# 사용법:
#   export EC2_SUBNET_ID=subnet-xxxx EC2_SECURITY_GROUP_ID=sg-xxxx EC2_KEY_NAME=my-key
#   bash experiments/aws/launch_spot_instance.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REGION="${AWS_REGION:-ap-northeast-2}"
INSTANCE_TYPE="${EC2_INSTANCE_TYPE:-g4dn.xlarge}"
AMI_ID="${EC2_AMI_ID:-}"
SPOT_MAX_PRICE="${EC2_SPOT_MAX_PRICE:-0.30}"
VOLUME_SIZE_GB="${EC2_VOLUME_SIZE_GB:-100}"

: "${EC2_SUBNET_ID:?EC2_SUBNET_ID 환경변수를 설정하세요 (예: subnet-0123abcd)}"
: "${EC2_SECURITY_GROUP_ID:?EC2_SECURITY_GROUP_ID 환경변수를 설정하세요 (22/8000/3000/8001/8002/9092 인바운드 필요)}"
: "${EC2_KEY_NAME:?EC2_KEY_NAME 환경변수를 설정하세요 (기존 EC2 키페어 이름)}"

command -v aws >/dev/null || { echo "[FAIL] aws CLI가 설치돼 있지 않습니다."; exit 1; }

if [[ -z "$AMI_ID" ]]; then
  echo "[launch] 최신 Ubuntu 22.04 AMI 조회 중 (region=$REGION) ..."
  AMI_ID=$(aws ec2 describe-images \
    --region "$REGION" \
    --owners 099720109477 \
    --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
              "Name=state,Values=available" \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
    --output text)
  echo "[launch] AMI: $AMI_ID"
fi

LAUNCH_SPEC=$(cat <<JSON
{
  "ImageId": "$AMI_ID",
  "InstanceType": "$INSTANCE_TYPE",
  "KeyName": "$EC2_KEY_NAME",
  "SecurityGroupIds": ["$EC2_SECURITY_GROUP_ID"],
  "SubnetId": "$EC2_SUBNET_ID",
  "BlockDeviceMappings": [
    {"DeviceName": "/dev/sda1", "Ebs": {"VolumeSize": $VOLUME_SIZE_GB, "VolumeType": "gp3", "DeleteOnTermination": true}}
  ],
  "UserData": "$(base64 -w0 "$SCRIPT_DIR/setup_instance.sh")"
}
JSON
)

echo "[launch] 스팟 인스턴스 요청 중 (type=$INSTANCE_TYPE, max_price=\$$SPOT_MAX_PRICE/h) ..."
REQUEST_ID=$(aws ec2 request-spot-instances \
  --region "$REGION" \
  --instance-count 1 \
  --type "one-time" \
  --spot-price "$SPOT_MAX_PRICE" \
  --launch-specification "$LAUNCH_SPEC" \
  --query 'SpotInstanceRequests[0].SpotInstanceRequestId' \
  --output text)

echo "[launch] 요청 ID: $REQUEST_ID — 인스턴스 배정 대기 중 ..."
aws ec2 wait spot-instance-request-fulfilled --region "$REGION" --spot-instance-request-ids "$REQUEST_ID"

INSTANCE_ID=$(aws ec2 describe-spot-instance-requests \
  --region "$REGION" \
  --spot-instance-request-ids "$REQUEST_ID" \
  --query 'SpotInstanceRequests[0].InstanceId' \
  --output text)

echo "[launch] 인스턴스 ID: $INSTANCE_ID — running 대기 중 ..."
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

PUBLIC_IP=$(aws ec2 describe-instances \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)

cat <<SUMMARY

════════════════════════════════════════
  인스턴스 ID : $INSTANCE_ID
  Public IP   : $PUBLIC_IP
  SSH         : ssh -i <key.pem> ubuntu@$PUBLIC_IP
════════════════════════════════════════

setup_instance.sh는 user-data로 자동 실행되며 완료까지 약 5~10분 소요(드라이버
설치 시 1회 재부팅 포함). 진행 확인:
  ssh -i <key.pem> ubuntu@$PUBLIC_IP 'tail -f /var/log/cloud-init-output.log'

완료 후 검증:
  ssh -i <key.pem> ubuntu@$PUBLIC_IP 'nvidia-smi && docker compose version'

다음 단계 (SSH로 접속해서):
  1. (로컬에서 먼저) bash experiments/aws/sync_to_s3.sh — 데이터/체크포인트를 S3에 올려둔다
  2. export DEEPLOG_S3_BUCKET=<bucket> && aws configure
  3. curl -fsSL https://raw.githubusercontent.com/sungsuhan/deeplog-serving-analysis/main/experiments/aws/bootstrap_ec2.sh | bash
     (또는 git clone 후 bash experiments/aws/bootstrap_ec2.sh)
  4. nohup bash experiments/aws/spot_backup_watcher.sh > /tmp/spot_watcher.log 2>&1 &
  5. bash experiments/run_benchmark.sh hdfs

스팟 인스턴스는 가격 급등 시 회수될 수 있습니다 — 3~4단계로 체크포인트/결과가
주기적으로 S3에 백업되도록 반드시 워처를 띄워두세요.

종료(과금 중단):
  aws ec2 terminate-instances --region $REGION --instance-ids $INSTANCE_ID
SUMMARY
