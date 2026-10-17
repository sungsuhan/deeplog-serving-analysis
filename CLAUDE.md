# CLAUDE.md — deeplog-serving-analysis

성균관대학교 빅데이터학과 석사 논문 프로젝트.  
DeepLog(LSTM 기반 로그 이상탐지 모델)을 FastAPI, BentoML, Triton 세 가지 ML 서빙 프레임워크에서 서빙할 때의 성능(지연시간, 처리량, 자원 사용률)을 정량적으로 비교한다.

---

## 연구 목적

- **모델 자체는 고정** — DeepLog 구현·성능 개선은 연구 범위 밖. 연구 기여는 서빙 아키텍처 비교에 집중. 아키텍처는 데이터셋과 무관하게 동일하고 vocab 크기만 데이터셋을 따른다(§현재 스코프).
- **비교 프레임워크**: FastAPI(베이스라인) / BentoML(범용 서빙) / Triton(고성능 GPU 서빙)
- **데이터셋**: HDFS(11M 로그, 이상 2.93%), BGL(4.7M 로그, 이상 7.34%)
- **측정 지표**: p50·p99 Latency(ms), Throughput(req/s), CPU·메모리 사용률(%), 부하 단계별 곡선

---

## 현재 스코프 — 졸업논문 (BGL 라운드)

**이 섹션은 지금 라운드에 적용되는 제약이다.** 2026-08-11 기말 발표 라운드는
HDFS만으로 종료됐고(결과는 EXPERIMENT.md §8), 이제 §연구 목적의 졸업논문 전체
스코프로 돌아가 BGL을 추가한다.

- **데이터셋: HDFS + BGL.** HDFS는 기말 라운드 측정분(`experiments/results/`)을
  그대로 쓰고 재측정하지 않는다. 이번 라운드에서 새로 하는 일은 BGL의 학습·
  서빙·벤치마크다.
- **모델: 아키텍처 고정, vocab 크기만 데이터셋을 따른다.** HDFS와 BGL 모두
  `model/deeplog.py`의 같은 구조(one-hot 입력, 2층 LSTM)를 쓰고, `input_size`/
  `num_classes`만 각 데이터셋 vocab에서 결정된다. BGL은 vocab이 훨씬 커서
  one-hot 입력이 넓어지지만 **임베딩 레이어 추가 등 구조 변경은 하지 않는다** —
  두 데이터셋 결과를 같은 표에서 비교하려면 아키텍처가 같아야 한다. 하이퍼
  파라미터(hidden size, 층 수, window)도 HDFS와 동일하게 유지한다.
- **이상 판정 기준(`num_candidates`, top-g): 두 데이터셋 모두 9.** `model/train.py`
  기본값 그대로이므로 BGL 학습 시 옵션을 줄 필요 없다. 이 값은 서빙 응답의
  `top_candidates`/`probabilities` 배열 길이를 결정해 응답 크기와 지연시간에
  영향을 준다 — 데이터셋 간 측정 조건을 같게 하려면 값이 같아야 한다. 대신
  BGL은 vocab이 커서 정상 시퀀스도 이상으로 판정되는 비율이 높을 수 있으므로,
  **탐지 성능 수치는 논문의 비교 대상으로 쓰지 않는다**(근거: EXPERIMENT.md
  스코프 이력 2026-10-13).
- **측정 조건: HDFS 라운드와 동일.** 프레임워크 3종, concurrency sweep
  1/4/8/16/32/64(저=4·중=16·고=64), 단계별 측정 시간, 반복 5사이클 모두 그대로 —
  `experiments/run_benchmark.sh` 기본값과 일치하므로 옵션 없이 실행한다. 조건이
  달라지면 두 데이터셋을 비교할 수 없다.
- **반복 측정: 5사이클.** n=3에서는 Mann-Whitney U(양측)가 도달 가능한 최소
  p-value가 약 0.1이라 유의수준 0.05에 원천적으로 도달할 수 없다(2026-08-06
  결정). 기존 사이클에 이어 붙일 때는 `CYCLE_START`로 시작 번호를 지정한다.
- **자원 사용률(CPU/GPU) 로깅: 포함.** `experiments/benchmark.py`의
  `--container`/`--monitor-gpu` 옵션으로 측정.
- **Kafka: 시스템 아키텍처로 포함, 성능 비교 측정 경로에서는 제외.**
  `kafka/producer.py` → 토픽 → `kafka/consumer.py` → 서빙 `POST /predict`
  경로는 실시간 스트리밍 워크로드를 보여주는 구현으로 논문 시스템 구성에
  들어간다. 다만 프레임워크 간 수치 비교는 부하를 통제할 수 있는
  `experiments/benchmark.py`의 직접 호출로만 한다 — Kafka를 거치면 프로듀서·
  컨슈머 속도가 변수로 섞인다(2026-07-31 결정, EXPERIMENT.md §4.2). 두 경로의
  수치를 같은 표·그래프에 섞지 않는다.
- **범위 밖(별도 확장 챕터 또는 향후 연구)**: K8s 확장, CI/CD 구축, Kafka
  경로 end-to-end 지연 측정. 시간이 남으면 본 실험과 분리해서만 다룬다.

기말 라운드의 축소 배경과 결정 과정은 EXPERIMENT.md "스코프 결정 이력"을 참고.

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
