# deeplog-serving-analysis

> 실시간 로그 이상탐지를 위한 ML 서빙 프레임워크의 성능 비교 연구  
> **A Performance Comparison of ML Serving Frameworks for Real-Time Log Anomaly Detection**

성균관대학교 특수대학원 빅데이터학과 석사 논문 프로젝트

---

## 📌 연구 개요

본 연구는 **DeepLog**(LSTM 기반 로그 이상탐지 모델)을 세 가지 ML 서빙 프레임워크에서 서빙할 때 발생하는 성능 차이를 정량적으로 비교하고, 부하 조건에 따른 트레이드오프를 분석합니다.

### 연구 질문
> 실시간 로그 이상탐지 워크로드에서 ML 서빙 프레임워크(FastAPI, BentoML, Triton)의 선택이 추론 지연시간 및 처리량에 미치는 영향은 무엇이며, 부하 조건에 따라 어떤 트레이드오프가 존재하는가?

---

## 🏗️ 전체 파이프라인

```
[HDFS / BGL 로그 데이터셋]
          ↓
[Kafka Producer - 실시간 스트리밍 시뮬레이션]
          ↓
┌─────────────┬─────────────┬─────────────┐
│   FastAPI   │   BentoML   │   Triton    │
│  (Docker)   │  (Docker)   │  (Docker)   │
└─────────────┴─────────────┴─────────────┘
          ↓ DeepLog 추론
[성능 측정: Latency / Throughput / 자원 사용률]
```

---

## 🔬 실험 설계

### 비교 대상 프레임워크

| 프레임워크 | 역할 | 특징 |
|---|---|---|
| FastAPI | 베이스라인 | Python 기반 경량 REST API, 직접 구현 |
| BentoML | 범용 서빙 | 개발자 친화적, 다양한 ML 프레임워크 지원 |
| Triton | 고성능 서빙 | NVIDIA 개발, GPU 최적화, 엔터프라이즈 표준 |

### 모델
- **DeepLog** (Du et al., 2017) — LSTM 기반 로그 시퀀스 이상탐지 모델
- 모델 자체는 고정(연구 기여는 서빙 아키텍처 비교에 집중)

### 데이터셋

| 데이터셋 | 로그 수 | 이상 비율 | 출처 |
|---|---|---|---|
| HDFS | 11,175,629건 | 2.93% | Amazon EC2 200개 노드 |
| BGL | 4,747,963건 | 7.34% | BlueGene/L 슈퍼컴퓨터 (LLNL) |

### 측정 지표
- p50 / p99 Latency (ms)
- Throughput (req/s)
- CPU · 메모리 사용률 (%)
- 부하 단계별 성능 곡선 (저 · 중 · 고)

---

## 🛠️ 기술 스택

- **모델**: DeepLog (PyTorch)
- **스트리밍**: Apache Kafka
- **서빙**: FastAPI / BentoML / NVIDIA Triton
- **컨테이너**: Docker
- **실험 환경**: AWS EC2 g4dn.xlarge (T4 GPU 16GB)
- **데이터셋**: LogHub (HDFS, BGL)

---

## 📁 프로젝트 구조

```
deeplog-serving-analysis/
├── README.md
├── data/                   # 데이터셋 전처리 코드
│   ├── download.sh
│   └── preprocess.py
├── model/                  # DeepLog 모델
│   ├── deeplog.py
│   └── train.py
├── serving/                # 서빙 프레임워크 구현
│   ├── fastapi/
│   ├── bentoml/
│   └── triton/
├── kafka/                  # Kafka 스트리밍 구성
│   ├── producer.py
│   └── consumer.py
├── experiments/            # 실험 스크립트 및 결과
│   ├── run_benchmark.sh
│   └── results/
├── docker/                 # Docker 환경 구성
│   └── docker-compose.yml
└── .env.example            # 환경변수 예시 (.env는 .gitignore)
```

---

## 🚀 실행 방법

아래 명령은 모두 저장소 루트에서 실행합니다. `<ds>` 자리에는 `hdfs` 또는 `bgl`을 넣습니다.
공식 벤치마크 수치는 AWS EC2 g4dn.xlarge(T4)에서만 측정하며, 로컬은 개발·검증용입니다.

### 0. 준비

- Python 3.11, Docker(+ compose 플러그인), NVIDIA GPU와 드라이버(NVIDIA Container Toolkit)
- 환경변수 파일 복사 후 값 입력 (`.env`는 커밋하지 않습니다)

```bash
cp .env.example .env
python -m venv .venv && source .venv/bin/activate      # Windows Git Bash: source .venv/Scripts/activate
pip install -r data/requirements.txt -r model/requirements.txt \
            -r experiments/requirements.txt -r kafka/requirements.txt
```

### 1. 데이터 다운로드·전처리

```bash
bash data/download.sh                          # LogHub(Zenodo)에서 HDFS_1, BGL 원본 → data/raw/
python data/preprocess.py --dataset <ds>       # → data/processed/<ds>/ (기본값 all = 둘 다)
```

BGL은 원본 로그를 Drain으로 직접 파싱하므로 처음 한 번은 수 분 이상 걸립니다(결과는 캐시됨).

### 2. DeepLog 학습

```bash
python model/train.py --dataset <ds>           # → model/checkpoints/<ds>/
```

하이퍼파라미터는 두 데이터셋 모두 기본값을 그대로 씁니다. 결과물은 PyTorch 가중치,
TorchScript 모델, 그리고 세 서빙 스택이 함께 읽는 `config.json`입니다.

학습은 에폭마다 진행 상태를 저장합니다. 중간에 컴퓨터를 꺼도 다시 켠 뒤 아래처럼
이어서 진행할 수 있고, 정식 결과물은 마지막 에폭이 끝난 뒤에만 만들어집니다.

```bash
python model/train.py --dataset <ds> --resume
```

### 3. 서빙 모델 패키징

```bash
python serving/triton/setup_model.py --dataset <ds>   # → serving/triton/models/<ds>/
docker compose -f docker/docker-compose.yml build fastapi bentoml triton-wrapper
docker compose -f docker/docker-compose.yml run --rm bentoml \
  python serving/bentoml/save_model.py --dataset <ds>  # BentoML 모델 deeplog-<ds> 등록
```

### 4. 서빙 실행·확인

세 프레임워크가 띄울 데이터셋은 `DEEPLOG_DATASET` 하나로 함께 정해집니다(기본 `hdfs`).

```bash
export DEEPLOG_DATASET=<ds>
docker compose -f docker/docker-compose.yml up -d fastapi          # :8000
curl http://localhost:8000/health
curl -X POST http://localhost:8000/predict -H 'Content-Type: application/json' \
     -d '{"log_keys": [0,1,2,3,4,5,6,7,8], "next_key": 9}'
docker compose -f docker/docker-compose.yml down
```

| 프레임워크 | 기동할 서비스 | 엔드포인트 | `/health` |
|---|---|---|---|
| FastAPI | `fastapi` | `http://localhost:8000` | GET |
| BentoML | `bentoml` | `http://localhost:3000` | **POST** |
| Triton | `triton` → `triton-wrapper` | `http://localhost:8002` | GET |

세 프레임워크는 같은 요청·응답 스키마(`top_candidates`, `probabilities`, `is_anomaly`,
`latency_ms`)를 씁니다.

### 5. 벤치마크

```bash
bash experiments/run_benchmark.sh <ds>
```

- 프레임워크를 하나씩 기동 → 측정 → 종료하며, 사이클마다 실행 순서를 무작위로 바꿉니다.
  세 프레임워크를 동시에 띄우지 않습니다.
- 기본 조건: concurrency 1/4/8/16/32/64, 단계별 30초, 5사이클. 저·중·고 부하 대표값은 4·16·64입니다.
- 측정 직전 각 서버의 `/health`가 보고하는 데이터셋이 `<ds>`와 다르면 측정 없이 중단합니다.
- 결과는 `experiments/results/cycle<N>/`, 실행 조건과 순서는 `experiments/results/run_manifest_*.txt`에 남습니다.
- 기존 사이클 뒤에 이어 붙이려면 `CYCLE_START=6 CYCLES=8 bash experiments/run_benchmark.sh <ds>`처럼 지정합니다.

### 6. 시각화·통계 검정

```bash
python experiments/plot_results.py                 # → experiments/figures/fig1~6
python experiments/stat_test.py --dataset <ds>     # Mann-Whitney U, 저·중·고 부하 기준
```

### 7. Kafka 스트리밍 경로 (선택)

Kafka는 실시간 스트리밍 아키텍처를 보여주기 위한 구성이며, 프레임워크 성능 비교 수치는
5단계의 직접 호출 벤치마크로만 냅니다. 두 경로의 수치를 섞지 않으려면 컨슈머 출력은
`experiments/results/` 밖에 둡니다.

```bash
docker compose -f docker/docker-compose.yml up -d kafka fastapi
python kafka/producer.py --dataset <ds> --rps 100
python kafka/consumer.py --topic deeplog-<ds> --endpoint http://localhost:8000 \
  --framework fastapi --output kafka_runs/kafka_fastapi_<ds>.csv
```

### 8. EC2에서 공식 측정

```bash
bash experiments/aws/update_sg_ip.sh                 # 접속 IP가 바뀌었을 때
EC2_LAUNCH_MODE=ondemand bash experiments/aws/one_shot_launch.sh
# 인스턴스 접속 후
source .venv/bin/activate && bash experiments/run_benchmark.sh <ds>
```

`one_shot_launch.sh`는 데이터·체크포인트 S3 업로드부터 인스턴스 기동, 이미지 빌드,
모델 등록까지 처리하고 벤치마크 실행 직전에 멈춥니다. 필요한 환경변수는 스크립트 상단
주석을 참고하세요. 측정이 끝나면 결과를 회수하고 인스턴스를 반드시 terminate합니다.

---

## 📄 논문 정보

- **제목**: 실시간 로그 이상탐지를 위한 ML 서빙 프레임워크의 성능 비교 연구 — FastAPI, BentoML, Triton을 중심으로
- **소속**: 성균관대학교 특수대학원 빅데이터학과
- **예정 발표**: 2027년 상반기

---

## 📚 주요 참고문헌

- Du, M., et al. (2017). DeepLog: Anomaly Detection and Diagnosis from System Logs through Deep Learning. *ACM CCS 2017*.
- He, S., et al. (2020). Loghub: A large collection of system log datasets towards automated log analytics. *arXiv:2008.06448*.
- Ali, R. (2025). Benchmarking Note: Comparing FastAPI and Triton Inference Server for ML Model Deployment. *Zenodo*.
- Gopalan, D. (2025). Scalable and Secure AI Inference in Healthcare. *arXiv:2602.00053*.

---

## ⚠️ 주의사항

`.env` 파일에 AWS 키 등 민감 정보를 저장하며, 해당 파일은 `.gitignore`에 포함되어 있습니다. `.env.example`을 참고하여 로컬 환경을 구성하세요.
