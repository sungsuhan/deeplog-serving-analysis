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

> 추후 업데이트 예정

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
