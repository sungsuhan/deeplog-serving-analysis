# EXPERIMENT.md — deeplog-serving-analysis 실험 문서

> 이 문서는 **코드 없이 논문을 쓰는 사람(채팅 Claude/ChatGPT 등)을 위한 스냅샷**입니다.
> 소스는 항상 코드이고, 이 문서는 특정 시점의 재생성 가능한 요약본입니다.
> **마지막 갱신: 2026-07-15, 데이터 파이프라인 완료 + 벤치마크 측정 규칙 확정 시점.**
> 코드가 바뀌면 이 문서도 다시 생성해야 합니다 — 특히 하이퍼파라미터·설정값은
> 아래 표만 믿지 말고 실제 config 파일과 대조하세요.

---

## 0. 진행 상황

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. 환경 설정 (.env) | ✅ 완료 | 코드가 실제로 읽는 값은 DATASET/CKPT_DIR/BENTOML_*/TRITON_* 뿐. AWS 키·Kafka bootstrap은 어디서도 안 읽음(문서용 placeholder) |
| 2. 데이터 다운로드·전처리 | ✅ 완료 | 아래 §2 참조 |
| 3. DeepLog 학습 | ⬜ 미실행 | 코드(`model/train.py`)는 존재, 체크포인트 없음 |
| 4. 모델 변환/패키징 (Triton ONNX·TorchScript, BentoML) | ⬜ 미실행 | 코드는 존재 |
| 5. Docker 스택 통합·스모크 테스트 | ⬜ 미실행 | |
| 6. 벤치마크 실행 | ⬜ 미실행 | 코드(`experiments/benchmark.py`)는 존재·완성 |
| 7. 결과 시각화 | ⬜ 미실행 | |

**즉 지금 이 시점에서 논문에 "확정"으로 쓸 수 있는 건 데이터셋 통계뿐이고,
모델 성능·서빙 벤치마크 수치는 전부 아직 존재하지 않습니다.** 방법론(설계) 서술은
가능하지만 결과(수치) 서술은 6~7단계 완료 후에나 가능합니다.

---

## 1. 연구 개요

- **연구 질문**: 실시간 로그 이상탐지 워크로드에서 ML 서빙 프레임워크(FastAPI,
  BentoML, Triton)의 선택이 추론 지연시간·처리량에 미치는 영향과, 부하 조건별
  트레이드오프.
- **모델은 고정**: DeepLog(Du et al., 2017) 구현·개선은 연구 범위 밖. 세 서빙
  구현은 **동일 가중치·동일 전처리**를 공유해야 함 (`model/train.py`가 한 번
  학습한 체크포인트를 FastAPI/BentoML/Triton이 그대로 로드).
- **비교 대상**: FastAPI(베이스라인, 순수 PyTorch) / BentoML(범용 ML 서빙) /
  Triton(GPU 고성능 서빙, dynamic batching)
- **실험 환경**: AWS EC2 g4dn.xlarge (T4 GPU 16GB) — 아직 인스턴스 미기동.

---

## 2. 데이터 파이프라인 (완료)

### 2.1 출처

- **LogHub** (He et al., 2020) — Zenodo record **8196385** (표시 버전 v8, 최신),
  DOI `10.5281/zenodo.8196385`, 게시일 2023-07-31.
- 다운로드: `data/download.sh` → `https://zenodo.org/record/8196385/files/{HDFS_v1.zip, BGL.zip}`
- **검증**: 이전 버전(v7, record 3227177, 2021-09-01)의 원본 로그와 MD5 체크섬을
  대조한 결과 `HDFS.log`/`BGL.log`/`anomaly_label.csv`가 **바이트 단위로 동일**함을
  확인. 즉 v7→v8 버전업은 Android·HDFS_v2/v3 등 다른 데이터셋 추가일 뿐, HDFS_1/BGL
  자체는 여러 버전에 걸쳐 변경되지 않음.

### 2.2 HDFS_1

- LogHub Zenodo 패키지에는 **라인 단위 구조화 CSV(`HDFS.log_structured.csv`)가
  없음** — 대신 `preprocessed/Event_traces.csv`에 **BlockId(세션)별로 이미 파싱된
  EventId 시퀀스**가 들어있어, 별도 로그 파서 없이 이걸 그대로 사용.
- 세션 = 하나의 BlockId에 속한 모든 로그 이벤트. `WINDOW_SIZE=10`짜리 슬라이딩
  윈도우로 시퀀스화, 세션 끝에서 짧아지면 0-padding.
- 라벨: `preprocessed/anomaly_label.csv` (BlockId → Normal/Anomaly).
- **최종 통계**:

  | 항목 | 값 |
  |---|---|
  | 원본 로그 라인 | 11,175,629 |
  | 세션(BlockId) 수 | 575,061 |
  | 세션 단위 이상 비율 | 2.93% (16,838 / 575,061) |
  | vocab 크기(이벤트 템플릿 수) | 29 |
  | train_normal.npy | (5,863,372, 10) |
  | test.npy / test_labels.npy | (6,043,092, 10) |
  | 윈도우 단위 이상 비율 | 2.97% |

### 2.3 BGL

- LogHub Zenodo 패키지에는 **raw 로그(`BGL.log`)만 존재**, 템플릿·구조화 파일이
  전혀 없음 → **Drain3 기반 로그 파서를 신규 구현** (`data/preprocess.py`의
  `_parse_bgl_structured`).
  - 파라미터: `sim_th=0.5`, `depth=4` (Zhu et al., ICSE-SEIP 2019 벤치마크의 BGL
    튜닝값을 따름)
  - 마스킹: hex 주소(`0x...`), IP, 순수 숫자를 `<*>`로 치환 후 클러스터링
  - 라인 포맷: `<Label> <Timestamp> <Date> <Node> <Time> <NodeRepeat> <Type> <Component> <Level> <Content>`
    — 첫 필드가 `-`면 정상, 아니면 알람 타입(라벨은 원본에 이미 존재, 파서는
    EventId만 새로 부여)
  - 파싱 결과는 `data/raw/BGL/BGL.log_structured.csv`로 캐싱 (재실행 시 재파싱 안 함)
- 세션 개념이 없어 **전역 슬라이딩 윈도우**(`WINDOW_SIZE=10`, step=1) 사용. 윈도우
  내 라인 중 하나라도 이상이면 그 윈도우는 이상으로 라벨링.
- **최종 통계**:

  | 항목 | 값 |
  |---|---|
  | 원본 로그 라인 | 4,713,493 |
  | 라인 단위 이상 비율 | **7.39%** (CLAUDE.md 명시값 7.34%와 근접 — 파서 정확도 검증됨) |
  | Drain 추출 템플릿(vocab) 수 | 991 |
  | train_normal.npy | (4,323,231, 10) |
  | test.npy / test_labels.npy | (4,713,484, 10) |
  | 윈도우 단위 이상 비율 | 8.28% (윈도우 내 이상 라인 1개만 있어도 전체 윈도우가 이상 처리되므로 라인 단위보다 높음) |

### 2.4 재현 환경

- Python 3.11.9 venv (`.venv/`, 프로젝트 로컬 global Python은 3.13이라 torch/numpy
  구버전과 비호환 — 3.11로 별도 설치)
- `data/requirements.txt`: `numpy==1.26.4`, `pandas==2.2.3`, `drain3==0.9.11`
- 데이터·전처리 산출물은 `.gitignore`로 제외 (`data/raw/`, `data/processed/`) —
  용량 문제(raw만 2.5GB) 때문에 저장소에는 스크립트만 커밋, 산출물은 각자 재생성.

---

## 3. 모델 — DeepLog (`model/deeplog.py`, `model/train.py`)

- **구조**: 2-layer LSTM → Linear. 입력은 로그 키 시퀀스의 one-hot 인코딩
  `(batch, window_size-1, num_classes)`, 출력은 다음 로그 키에 대한 클래스 로짓
  `(batch, num_classes)`. 임베딩 레이어 없음(원 논문 방식, vocab이 작아 one-hot으로 충분).
- **학습**: 정상 세션/윈도우만으로 비지도 학습(다음 로그 키 예측, CrossEntropyLoss).
  val_loss 기준 best checkpoint 저장.
- **이상 판정**: 추론 시 실제 다음 키가 top-g(`num_candidates`) 예측 안에 없으면
  anomaly.
- **기본 하이퍼파라미터** (논문 Du et al. 2017 기준값, `train.py` 상단 상수):

  | 파라미터 | 기본값 |
  |---|---|
  | hidden_size | 64 |
  | num_layers | 2 |
  | epochs | 30 |
  | batch_size | 2048 |
  | learning rate | 1e-3 |
  | val_split | 0.1 |
  | num_candidates (top-g) | 9 |

- **산출물** (`model/checkpoints/{dataset}/`, 아직 생성 안 됨):
  - `deeplog_best.pt` — FastAPI/BentoML이 로드
  - `deeplog.ts` — TorchScript(trace 변환), Triton `pytorch_libtorch` 백엔드용
  - `config.json` — 세 서빙 스택이 공유하는 하이퍼파라미터 (dataset, input_size,
    hidden_size, num_layers, num_classes, window_size, num_candidates)

---

## 4. 서빙 아키텍처 (코드 구현 완료, 배포·통합 테스트 미실행)

세 프레임워크 모두 **동일한 요청/응답 스키마**를 노출 (공정 비교 목적):

```
POST /predict
  요청: { "log_keys": [int, ...] (길이 = window_size-1), "next_key": int? }
  응답: { "top_candidates": [int,...], "probabilities": [float,...],
          "is_anomaly": bool?, "latency_ms": float }
GET  /health
```

| 항목 | FastAPI (port 8000) | BentoML (port 3000) | Triton (port 8001 서버 / 8002 래퍼) |
|---|---|---|---|
| 역할 | 베이스라인 — 순수 PyTorch, 프레임워크 오버헤드 없음 | 범용 ML 서빙 — 모델 스토어, 자동 스키마 문서화, 리소스/타임아웃 선언 | GPU 고성능 서빙 — C++ 런타임, dynamic batching |
| 모델 로드 | `torch.load(deeplog_best.pt)`, lifespan에서 1회 | BentoML model store에서 `bentoml.pytorch.load_model()` | `deeplog.ts`(TorchScript)를 Triton 서버가 로드, 래퍼는 HTTP 클라이언트로 위임 |
| 전처리(one-hot) | 서버 프로세스 내(PyTorch) | 서버 프로세스 내(PyTorch) | **래퍼**가 numpy로 수행 후 Triton에 전달 (Triton 네이티브 API와 포맷이 달라 래퍼 필요) |
| 동시성 제어 | 없음(uvicorn 기본) | `traffic.timeout=10s` 선언 | `max_batch_size=64`, `dynamic_batching`(preferred batch 8/16/32/64, max_queue_delay=100us) |
| 리소스 선언 | 없음 | `resources.memory=1Gi` | `instance_group: KIND_GPU, count=1` |

**Triton 구조가 2단**이라는 점이 논문에서 특히 설명이 필요한 부분: 클라이언트 →
래퍼(8002, FastAPI+tritonclient) → Triton 서버(8001, C++ 런타임) 순으로 홉이 하나
더 있음. 벤치마크 시 이 홉의 오버헤드도 결과 해석에 포함해야 함.

---

## 5. 벤치마크 설계 (`experiments/benchmark.py`, 코드 완성·미실행)

- **방식**: Closed-loop — `asyncio.Semaphore(concurrency)`로 항상 N개 요청을
  in-flight 상태로 유지, 하나 끝나면 즉시 다음 요청 전송. (Open-loop 대비 최대
  처리량 측정에 적합한 표준 기법.)
- **Concurrency sweep**: 1 → 4 → 8 → 16 → 32 → 64 (기본값, CLI로 변경 가능)
- **단계별 측정 시간**: 30초 (기본값), 워밍업 5초(concurrency=1) 선행
- **요청 payload**: 각 데이터셋 test.npy에서 2,000개 무작위 샘플링 (캐시 편향 방지)
- **측정 지표**: `throughput_rps`, `p50_ms`, `p99_ms`, `mean_ms`, `min_ms`,
  `max_ms`, `n_requests`, `n_errors`
- **출력**: `experiments/results/{framework}_{dataset}.csv` (framework × dataset
  조합마다 별도 파일 — 3 프레임워크 × 2 데이터셋 = 6개 CSV 예상)
- Kafka 스트리밍 경로(`kafka/producer.py`, `kafka/consumer.py`)는 별도로 존재 —
  실시간 스트리밍 시뮬레이션용이며, `benchmark.py`의 HTTP 직접 부하 테스트와는
  다른 실험 축(처리량 vs. 실시간 파이프라인 지연)임.

### 5.1 측정 운영 규칙 (CLAUDE.md와 동기화, 방법론 챕터용)

이 항목들은 스크립트 로직이 아니라 **측정을 어떻게 운영하는가**에 대한 규칙이라
`benchmark.py` 코드만 봐서는 드러나지 않음 — 방법론 서술 시 반드시 포함해야 함.

- **세 프레임워크를 동시에 실행하지 않는다.** 컨테이너를 하나씩 기동 → 측정 →
  종료하는 순차 방식. (동시 실행 시 CPU/GPU/네트워크 자원 경합으로 세 프레임워크
  결과가 서로 오염될 수 있기 때문.)
- **순서 효과(order effect) 통제**: 측정 사이클마다 프레임워크 실행 순서를
  바꿔가며 반복 — 항상 같은 순서로 측정하면 캐시 예열·시스템 온도 등 시간에 따른
  변화가 특정 프레임워크에만 유리/불리하게 작용할 수 있음.
- **공식 측정은 EC2 g4dn.xlarge에서만** — 로컬(개발 랩탑)에서 돌린 벤치마크
  결과는 `experiments/results/`에 저장하지 않음. 논문에 들어가는 모든 수치는
  동일한 하드웨어(EC2)에서 나온 것이어야 비교 타당성이 성립.
- **통계 검정: Mann-Whitney U** — 프레임워크 간 지연시간 분포 비교 시 사용
  (지연시간은 정규분포를 가정하기 어려운 right-skewed 분포이므로 비모수 검정 채택).
- **핫패스에 pandas 미사용** — Kafka→추론 서버→응답 경로(`kafka/*.py`,
  `serving/*/app.py`, `service.py`, `experiments/benchmark.py`)는 실제로 pandas를
  import하지 않음(2026-07-15 확인). pandas는 `data/preprocess.py`(전처리)와
  `experiments/plot_results.py`(사후 분석)에서만 사용 — 측정값 자체에 프레임워크
  오버헤드가 섞이지 않도록 하기 위함.

---

## 6. 아직 결정 안 된 것 / 다음 세션에서 확인할 것

- EC2 g4dn.xlarge 인스턴스 미기동 (온디맨드 $0.647/h, ap-northeast-2)
- 모델 학습 하이퍼파라미터가 기본값 그대로 갈지, 튜닝할지 미정
- Triton ONNX 변환 여부 — 현재 코드는 TorchScript만 사용(`config.pbtxt`의
  `platform: pytorch_libtorch`), CLAUDE.md에는 "ONNX/TorchScript"로 병기되어
  있어 실제로는 TorchScript만 쓰는지 확인 필요
- 결과 시각화(`experiments/plot_results.py`) 아직 안 읽어봄 — 6단계 완료 후 확인
