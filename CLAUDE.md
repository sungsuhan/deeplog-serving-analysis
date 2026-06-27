# CLAUDE.md — deeplog-serving-analysis

성균관대학교 빅데이터학과 석사 논문 프로젝트.  
DeepLog(LSTM 기반 로그 이상탐지 모델)을 FastAPI, BentoML, Triton 세 가지 ML 서빙 프레임워크에서 서빙할 때의 성능(지연시간, 처리량, 자원 사용률)을 정량적으로 비교한다.

---

## 연구 목적

- **모델 자체는 고정** — DeepLog 구현·성능 개선은 연구 범위 밖. 연구 기여는 서빙 아키텍처 비교에 집중.
- **비교 프레임워크**: FastAPI(베이스라인) / BentoML(범용 서빙) / Triton(고성능 GPU 서빙)
- **데이터셋**: HDFS(11M 로그, 이상 2.93%), BGL(4.7M 로그, 이상 7.34%)
- **측정 지표**: p50·p99 Latency(ms), Throughput(req/s), CPU·메모리 사용률(%), 부하 단계별 곡선

---

## 기술 스택

| 계층 | 기술 |
|---|---|
| 모델 | DeepLog (PyTorch) |
| 스트리밍 | Apache Kafka |
| 서빙 | FastAPI / BentoML / NVIDIA Triton |
| 컨테이너 | Docker / docker-compose |
| 실험 환경 | AWS EC2 g4dn.xlarge (T4 GPU 16GB) |

---

## 디렉토리 구조

```
deeplog-serving-analysis/
├── data/           # 데이터셋 다운로드·전처리 (download.sh, preprocess.py)
├── model/          # DeepLog 모델 정의 및 학습 (deeplog.py, train.py)
├── serving/
│   ├── fastapi/    # FastAPI 서빙 구현
│   ├── bentoml/    # BentoML 서빙 구현
│   └── triton/     # Triton 서빙 구현 (ONNX/TorchScript 모델 포함)
├── kafka/          # Kafka Producer·Consumer (스트리밍 시뮬레이션)
├── experiments/    # 벤치마크 스크립트 및 results/ 디렉토리
├── docker/         # docker-compose.yml
└── .env.example    # 환경변수 예시 (.env는 .gitignore에 포함)
```

---

## 핵심 규칙

- **`.env` 파일을 절대 커밋하지 않는다.** AWS 키 등 민감 정보 포함. `.env.example`만 관리.
- 세 서빙 구현은 **동일한 DeepLog 모델 가중치와 전처리 로직**을 공유해야 한다. 프레임워크별로 모델을 달리 학습시키지 않는다.
- 벤치마크 결과(`experiments/results/`)는 재현 가능해야 한다 — 실험 파라미터(부하 단계, 동시 요청 수 등)를 스크립트에 명시적으로 기록.
- 서빙 코드 변경 시 세 프레임워크 모두에서 동일한 입력/출력 스키마를 유지한다.

---

## 환경 설정

```bash
# 환경변수 복사 후 실제 값 입력
cp .env.example .env

# 전체 스택 실행 (Kafka + 3개 서빙 컨테이너)
docker compose -f docker/docker-compose.yml up

# 벤치마크 실행
bash experiments/run_benchmark.sh
```

---

## 논문 정보

- **제목**: 실시간 로그 이상탐지를 위한 ML 서빙 프레임워크의 성능 비교 연구 — FastAPI, BentoML, Triton을 중심으로
- **소속**: 성균관대학교 특수대학원 빅데이터학과
- **예정 발표**: 2027년 상반기
- **핵심 선행연구**: Du et al. (2017) DeepLog, He et al. (2020) LogHub
