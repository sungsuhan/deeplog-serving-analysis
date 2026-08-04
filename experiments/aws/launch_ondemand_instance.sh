#!/usr/bin/env bash
# g4dn.xlarge 온디맨드 인스턴스 기동 스크립트 — 스팟 용량 부족/인터럽션 시 대안.
#
# setup_instance.sh를 user-data로 넘겨 부팅과 동시에 자동 프로비저닝하는 건
# launch_spot_instance.sh와 동일 — 차이는 가격 입찰·체결 대기 없이 즉시
# 기동한다는 점뿐. 온디맨드 요금($0.647/h 수준, EXPERIMENT.md)이 붙지만
# 스팟 인터럽션(용량 부족으로 인스턴스가 통째로 회수되는 것) 위험이 없다.
# 2026-08-04: 스팟이 "no Spot capacity available"로 두 번째 시도에서 회수된 뒤
# 마감이 임박해 신뢰성을 우선시하기로 하고 이 스크립트를 추가함.
#
# 필수/선택 환경변수는 launch_spot_instance.sh와 동일(EC2_SPOT_MAX_PRICE만 여기선 무관).
#
# 사용법:
#   export EC2_SUBNET_ID=subnet-xxxx EC2_SECURITY_GROUP_ID=sg-xxxx EC2_KEY_NAME=my-key
#   bash experiments/aws/launch_ondemand_instance.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REGION="${AWS_REGION:-ap-northeast-2}"
INSTANCE_TYPE="${EC2_INSTANCE_TYPE:-g4dn.xlarge}"
AMI_ID="${EC2_AMI_ID:-}"
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

echo "[launch] 온디맨드 인스턴스 기동 중 (type=$INSTANCE_TYPE, ~\$0.647/h) ..."
INSTANCE_ID=$(aws ec2 run-instances \
  --region "$REGION" \
  --image-id "$AMI_ID" \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$EC2_KEY_NAME" \
  --security-group-ids "$EC2_SECURITY_GROUP_ID" \
  --subnet-id "$EC2_SUBNET_ID" \
  --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":$VOLUME_SIZE_GB,\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]" \
  --user-data "$(cat "$SCRIPT_DIR/setup_instance.sh")" \
  --count 1 \
  --query 'Instances[0].InstanceId' \
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

setup_instance.sh는 user-data로 자동 실행되며 완료까지 약 10~20분 소요(드라이버
설치 시 1회 재부팅 포함 — CUDA 툴킷 설치가 예상보다 오래 걸릴 수 있음, 2026-08-04
실측 기준 CUDA 설치만 8분 내외). 진행 확인:
  ssh -i <key.pem> ubuntu@$PUBLIC_IP 'tail -f /var/log/cloud-init-output.log'

완료 후 검증:
  ssh -i <key.pem> ubuntu@$PUBLIC_IP 'nvidia-smi && docker compose version'

온디맨드 요금이 인스턴스를 켜둔 시간만큼 계속 청구됩니다 — 작업 끝나면 바로 종료:
  aws ec2 terminate-instances --region $REGION --instance-ids $INSTANCE_ID
SUMMARY
