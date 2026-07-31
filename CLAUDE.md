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

## 현재 스코프 — 2026-08-11 기말 발표

**이 섹션은 위 §연구 목적(졸업논문 전체 스코프)과 구분되는, 이번 라운드에만
적용되는 제약이다.** 발표일이 8/11로 확정되며 실질 가용 기간이 약 2주로 줄어,
세 프레임워크 비교라는 연구 novelty의 핵심은 그대로 지키고 데이터셋 범위만 좁혔다.

- **데이터셋: HDFS만.** BGL은 이번 라운드에서 제외 — 학습·벤치마크는 물론 EDA도
  하지 않는다. BGL은 후속 연구/졸업논문 스코프에서 다룬다(전처리 코드와
  `data/processed/bgl/`은 이미 존재하지만 이번 라운드 결과물에는 포함하지 않음).
- **프레임워크: FastAPI / BentoML / Triton 3종 그대로.** 축소하지 않는다 — 스코프를
  좁힐 때 가장 마지막에 건드릴 항목.
- **부하 단계: 저·중·고 3단계 그대로.**
- **반복 측정: 3사이클.** 원래 계획은 5사이클이었으나 시간 제약으로 축소
  (`experiments/run_benchmark.sh`의 `CYCLES` 기본값과 일치).
- **자원 사용률(CPU/GPU) 로깅: 포함.** `experiments/benchmark.py`의
  `--container`/`--monitor-gpu` 옵션으로 측정.

축소 배경과 결정 과정은 EXPERIMENT.md "스코프 결정 이력"을 참고.

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
- **로컬(랩탑)은 개발·디버깅 전용.** 공식 벤치마크 결과는 **EC2 g4dn.xlarge에서만 측정**하며, 로컬에서 돌린 결과는 `experiments/results/`에 저장하지 않는다.
- 주요 마일스톤(각 프레임워크 서빙 완성, 벤치마크 파이프라인 완성 등) 시점에 `EXPERIMENT.md`를 현재 코드 기준으로 재생성한다. 구체적 설정값은 문서에 하드코딩하지 않고 실제 config 파일 참조로 표기.

---

## 벤치마크 측정 규칙

- 세 프레임워크를 **동시에 실행하지 않는다.** 컨테이너를 순차적으로 기동 → 측정 → 종료.
- 순서 효과(order effect)를 방지하기 위해 측정 사이클마다 프레임워크 실행 순서를 바꿔가며 반복한다.
- 핫패스(Kafka → 추론 서버 → 응답)에 **pandas를 사용하지 않는다.** pandas는 전처리와 사후 분석 전용.
- 결과 비교 시 통계 검정은 **Mann-Whitney U**를 사용한다.

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

## 검증

이 프로젝트에는 자동화된 테스트 스위트(pytest 등)가 없다. 대신 코드 변경 종류별로
다음을 직접 실행해 확인한다.

- **서빙 코드(`serving/*`) 변경 후**: 세 프레임워크 모두 기동한 상태에서
  1. `/health` — 세 곳 모두 정상 응답하는지. **BentoML만 GET이 아니라 POST**로
     호출해야 한다(BentoML 1.3 신규 SDK의 `@bentoml.api`는 POST 전용 — 로컬
     검증에서 실측 확인됨, 2026-07-29). FastAPI·Triton 래퍼는 GET.
  2. 동일한 `log_keys` 입력으로 `POST /predict`를 세 프레임워크에 각각 호출 —
     `top_candidates`/`probabilities`/`is_anomaly`/`latency_ms` **응답 스키마가
     동일한 키·타입**인지, 같은 체크포인트를 로드했다면 `top_candidates`가
     실질적으로 같은 값을 내는지 확인
  - 세 프레임워크 스키마가 어긋나면 벤치마크 CSV 컬럼이 프레임워크마다 달라져
    비교 자체가 불가능해지므로, "동일 입출력 스키마 유지" 규칙은 이 절차로만
    실제로 검증된다.
- **전처리(`data/preprocess.py`) 변경 후**: 처리 결과 배열 shape과 이상 비율이
  §연구 목적에 명시된 값(HDFS 2.93%, BGL 7.34%)과 크게 벗어나지 않는지 확인.
- **벤치마크 스크립트(`experiments/benchmark.py`) 변경 후**: `--concurrency 1
  --duration 5` 같은 짧은 설정으로 로컬에서 1회 드라이런 — 단, 이 결과는 위 규칙대로
  `experiments/results/`에 저장하지 않는다.
