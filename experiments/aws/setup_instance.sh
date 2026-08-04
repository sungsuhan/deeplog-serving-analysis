#!/usr/bin/env bash
# EC2 g4dn.xlarge (Ubuntu 22.04 base AMI) 프로비저닝 스크립트.
#
# 설치 항목:
#   1. NVIDIA GPU 드라이버
#   2. CUDA Toolkit
#   3. Docker Engine + docker compose 플러그인 (docker-compose.yml은 `docker compose` v2 문법 사용)
#   4. NVIDIA Container Toolkit (컨테이너 안에서 GPU 인식 — Triton의 KIND_GPU에 필수)
#   5. git + AWS CLI v2 (bootstrap_ec2.sh가 레포 clone·S3 동기화에 사용)
#
# 이 스크립트가 끝나면 OS 레벨 준비는 끝난 상태 — 코드/데이터를 올리고
# 서빙을 준비하는 건 bootstrap_ec2.sh(SSH로 접속해 별도 실행)가 담당한다.
#
# 사용법:
#   - launch_spot_instance.sh가 user-data로 이 파일을 그대로 넘겨 부팅 시 자동 실행.
#   - 이미 떠 있는 인스턴스에서 수동 실행: sudo bash setup_instance.sh
#
# 전제: Ubuntu 22.04 LTS 베이스 AMI (Deep Learning AMI 아님 — 처음부터 직접 구성).
#       Deep Learning AMI를 쓰면 1~4가 대부분 이미 설치돼 있어 이 스크립트가 필요 없음.
set -euo pipefail

log() { echo "[setup] $(date -u +%H:%M:%S)  $*"; }

export DEBIAN_FRONTEND=noninteractive
DRIVER_FRESH_INSTALL=0

log "apt update & 기본 패키지 설치"
apt-get update -y
apt-get install -y --no-install-recommends \
  build-essential ca-certificates curl gnupg lsb-release software-properties-common \
  python3-venv "linux-headers-$(uname -r)"

# ── 1. NVIDIA GPU 드라이버 ──────────────────────────────────────────────────
if ! command -v nvidia-smi &>/dev/null; then
  log "NVIDIA 드라이버 설치 (ubuntu-drivers 사용)"
  apt-get install -y --no-install-recommends ubuntu-drivers-common
  ubuntu-drivers install
  DRIVER_FRESH_INSTALL=1
else
  log "NVIDIA 드라이버 이미 설치됨 — 건너뜀 ($(nvidia-smi --query-gpu=driver_version --format=csv,noheader))"
fi

# ── 2. CUDA Toolkit ──────────────────────────────────────────────────────────
if ! command -v nvcc &>/dev/null; then
  log "CUDA Toolkit 설치 (NVIDIA apt repo)"
  UBUNTU_VER="$(lsb_release -rs | tr -d '.')"
  curl -fsSL "https://developer.download.nvidia.com/compute/cuda/repos/ubuntu${UBUNTU_VER}/x86_64/cuda-keyring_1.1-1_all.deb" \
    -o /tmp/cuda-keyring.deb
  dpkg -i /tmp/cuda-keyring.deb
  apt-get update -y
  apt-get install -y --no-install-recommends cuda-toolkit-12-4
  echo 'export PATH=/usr/local/cuda/bin:$PATH' > /etc/profile.d/cuda.sh
else
  log "CUDA Toolkit 이미 설치됨 — 건너뜀"
fi

# ── 3. Docker Engine + docker compose 플러그인 ───────────────────────────────
if ! command -v docker &>/dev/null; then
  log "Docker 설치"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  usermod -aG docker "${SUDO_USER:-ubuntu}"
else
  log "Docker 이미 설치됨 — 건너뜀 ($(docker --version))"
fi

# ── 4. NVIDIA Container Toolkit ──────────────────────────────────────────────
if ! dpkg -l 2>/dev/null | grep -q nvidia-container-toolkit; then
  log "nvidia-container-toolkit 설치"
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update -y
  apt-get install -y --no-install-recommends nvidia-container-toolkit
  nvidia-ctk runtime configure --runtime=docker
  systemctl restart docker
else
  log "nvidia-container-toolkit 이미 설치됨 — 건너뜀"
fi

# ── 5. git + AWS CLI v2 ────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
  log "git 설치"
  apt-get install -y --no-install-recommends git
else
  log "git 이미 설치됨 — 건너뜀"
fi

if ! command -v aws &>/dev/null; then
  log "AWS CLI v2 설치"
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
  apt-get install -y --no-install-recommends unzip
  unzip -q /tmp/awscliv2.zip -d /tmp
  /tmp/aws/install
  rm -rf /tmp/awscliv2.zip /tmp/aws
else
  log "AWS CLI 이미 설치됨 — 건너뜀 ($(aws --version))"
fi

log "설치 완료."
log "검증 명령 (재부팅 후 SSH로 접속해 직접 실행):"
log "  nvidia-smi"
log "  docker compose version"
log "  docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi"
log "  aws sts get-caller-identity   # IAM 자격 확인 (인스턴스 프로파일 또는 aws configure 필요)"
log ""
log "다음 단계: bootstrap_ec2.sh로 코드/데이터/체크포인트를 가져오세요."

# /tmp가 아니라 /var/lib에 마커를 남긴다 — /tmp는 보통 tmpfs라 재부팅하면
# 지워지는데, 드라이버 설치 시 바로 아래에서 재부팅을 트리거하므로 /tmp에
# 남기면 재부팅 후 "완료 안 됨"으로 잘못 보인다(2026-08-04 실제로 겪은 버그 —
# 스크립트는 520초 만에 정상 완료했는데 재부팅 후 마커가 사라져 one_shot_launch.sh가
# 계속 대기하다 타임아웃났다).
touch /var/lib/setup_instance_done

if [[ "$DRIVER_FRESH_INSTALL" == "1" ]]; then
  log "새 NVIDIA 드라이버가 설치되어 커널 모듈 로드를 위해 1분 후 재부팅합니다."
  shutdown -r +1 "GPU driver install - rebooting"
fi
