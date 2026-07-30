#!/usr/bin/env bash
# 보안그룹 인바운드 규칙을 지금 이 컴퓨터의 공인 IP로 갱신한다.
# 네트워크를 옮길 때마다(집/카페/다른 랩탑 등) 이것 하나만 실행하면 됨.
#
# 로컬에 "이전 IP"를 따로 저장해두지 않는다 — 매번 보안그룹 자체에 현재
# 등록된 CIDR을 조회해서 그걸 기준으로 지우고 새로 연다. 그래서 다른 컴퓨터
# 에서 실행해도, 혹은 콘솔에서 수동으로 건드린 뒤에 실행해도 항상 안전하게
# "지금 내 IP만 허용" 상태로 수렴한다.
#
# 선택 환경변수:
#   SECURITY_GROUP_ID  기본값: sg-063774d1ab4874831 (deeplog-serving-sg)
#   AWS_REGION         기본값: ap-northeast-2
#   PORTS              기본값: "22 8000 3000 8001 8002 9092"
#
# 사용법:
#   bash experiments/aws/update_sg_ip.sh
set -euo pipefail

SECURITY_GROUP_ID="${SECURITY_GROUP_ID:-sg-063774d1ab4874831}"
REGION="${AWS_REGION:-ap-northeast-2}"
PORTS="${PORTS:-22 8000 3000 8001 8002 9092}"

command -v aws >/dev/null || { echo "[FAIL] aws CLI가 설치돼 있지 않습니다."; exit 1; }

# ── 1. 현재 공인 IP 조회 ──────────────────────────────────────────────────────
MY_IP="$(curl -fsS --max-time 5 https://checkip.amazonaws.com | tr -d '[:space:]')"
if [[ -z "$MY_IP" ]]; then
  echo "[FAIL] 공인 IP 조회 실패 (checkip.amazonaws.com 응답 없음)"
  exit 1
fi
NEW_CIDR="${MY_IP}/32"

echo "[update-sg] 감지된 공인 IP : $MY_IP"
echo "[update-sg] 대상 보안그룹  : $SECURITY_GROUP_ID ($REGION)"
echo ""

# ── 2. 포트별로 기존 CIDR을 전부 걷어내고 새 IP만 남긴다 ──────────────────────
for port in $PORTS; do
  existing_cidrs="$(aws ec2 describe-security-groups \
    --group-ids "$SECURITY_GROUP_ID" --region "$REGION" \
    --query "SecurityGroups[0].IpPermissions[?FromPort==\`$port\` && ToPort==\`$port\` && IpProtocol=='tcp'].IpRanges[].CidrIp" \
    --output text)"

  already_present=false
  for cidr in $existing_cidrs; do
    if [[ "$cidr" == "$NEW_CIDR" ]]; then
      already_present=true
      continue
    fi
    aws ec2 revoke-security-group-ingress \
      --group-id "$SECURITY_GROUP_ID" --protocol tcp --port "$port" --cidr "$cidr" \
      --region "$REGION" >/dev/null
    echo "  tcp/$port : $cidr 제거"
  done

  if [[ "$already_present" == true ]]; then
    echo "  tcp/$port : 이미 $NEW_CIDR 허용됨 — 변경 없음"
  else
    aws ec2 authorize-security-group-ingress \
      --group-id "$SECURITY_GROUP_ID" --protocol tcp --port "$port" --cidr "$NEW_CIDR" \
      --region "$REGION" >/dev/null
    echo "  tcp/$port : $NEW_CIDR 허용"
  fi
done

echo ""
echo "현재 허용된 IP: $MY_IP"
