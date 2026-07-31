# EXPERIMENT.md — deeplog-serving-analysis 실험 문서

> 이 문서는 **코드 없이 논문을 쓰는 사람(채팅 Claude/ChatGPT 등)을 위한 스냅샷**입니다.
> 소스는 항상 코드이고, 이 문서는 특정 시점의 재생성 가능한 요약본입니다.
> **마지막 갱신: 2026-07-31, GPU confound 제거(FastAPI/BentoML도 GPU 사용) +
> concurrency 대표값(저/중/고) 확정 + Kafka 스코프 정리 + 통계 검정 스크립트
> 작성 + S3 버킷 생성 완료 시점.**
> 코드가 바뀌면 이 문서도 다시 생성해야 합니다 — 특히 하이퍼파라미터·설정값은
> 아래 표만 믿지 말고 실제 config 파일과 대조하세요.

---

## 0. 진행 상황

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. 환경 설정 (.env) | ✅ 완료 | `DEEPLOG_S3_BUCKET` 추가됨(§6) |
| 2. 데이터 다운로드·전처리 | ✅ 완료(HDFS·BGL 모두) | §2 참조. 단 **이번 기말 스코프는 HDFS만** — §스코프 참고 |
| 3. DeepLog 학습 | ✅ 완료(HDFS만) | §3 참조. BGL은 스코프 밖 |
| 4. 모델 변환/패키징 (Triton TorchScript, BentoML) | ✅ 완료 | §3·§4 참조 |
| 5. Docker 스택 통합·스모크 테스트 | ✅ 완료(로컬) | §4 참조 — 버그 5개 발견·수정 |
| 6. EC2 인프라 준비 | ✅ 스크립트·AWS 리소스 완료, ⬜ **spot quota 승인 대기 중** | §6 참조 |
| 7. 벤치마크 실행 | ⬜ 미실행 | quota 승인 후 EC2에서 실행 예정 |
| 8. 결과 시각화 | ⬜ 미실행 | 7단계 완료 후 |

**즉 지금 이 시점에서 논문에 "확정"으로 쓸 수 있는 건 데이터셋 통계 + HDFS
학습 결과(수렴 loss)뿐이고, 서빙 프레임워크 간 성능 비교 수치는 EC2 스팟
quota 승인 후에나 나온다.**

---

## 스코프 결정 이력

### 2026-07-30 — 기말(2026-08-11 발표) 스코프를 졸업논문 스코프에서 분리

**배경**: 발표일이 8/11로 확정되며 실질 가용 기간이 약 2주로 줄었다. 세
프레임워크(FastAPI/BentoML/Triton) 비교라는 연구 novelty의 핵심은 그대로
유지하면서, 시간이 부족할 때 가장 먼저 줄일 수 있는 축을 골라야 했다.

**결정**: 데이터셋 범위를 HDFS 하나로 좁혔다(BGL 학습·벤치마크·EDA 전부
제외). 프레임워크 수·부하 단계(저/중/고)는 줄이지 않았다 — 이게 연구의
핵심 기여이기 때문. 대신 반복 측정 사이클을 원래 계획한 5회에서 3회로
줄여 전체 벤치마크 소요 시간을 더 확보했다.

**변경 사항 요약**:
- 데이터셋: HDFS만 (BGL 코드·전처리 산출물은 남겨두되 이번 결과물에서 제외)
- 프레임워크: FastAPI / BentoML / Triton 3종 유지
- 부하 단계: 저/중/고 3단계 유지
- 반복 측정: 5사이클 → **3사이클** (`experiments/run_benchmark.sh`의
  `CYCLES` 기본값)
- 자원 사용률(CPU/GPU) 로깅: 유지 (`experiments/benchmark.py`
  `--container`/`--monitor-gpu`)

현재 스코프 규칙은 CLAUDE.md "현재 스코프 — 2026-08-11 기말 발표" 섹션에
있음 — 이 문서(EXPERIMENT.md)는 "왜"를, CLAUDE.md는 "지금 무엇을"을 기록한다.

### 2026-07-31 — GPU confound 제거, concurrency 대표값 확정, Kafka 스코프 정리

7-30 시점에 §4.1(FastAPI/BentoML GPU 미사용)·§5 concurrency 매핑·Kafka
포함 여부 세 가지를 미결정으로 남겨뒀는데, 이 세션에서 전부 결정하고
(GPU는 코드까지) 반영했다.

- **GPU confound**: FastAPI/BentoML도 Triton과 동일하게 GPU를 쓰도록
  고친다 — "CPU 베이스라인"으로 의도적으로 남기는 대안은 기각(연구 질문이
  프레임워크 비교이지 CPU/GPU 비교가 아니므로). 코드 수정·로컬 검증 완료
  (§4.1).
- **concurrency 대표값**: 저=4 / 중=16 / 고=64로 확정. 전체 sweep(6개 값)은
  그대로 측정하고, 압축은 시각화(`plot_results.py`)·통계
  (`stat_test.py`) 단계에서만 한다(§5).
- **Kafka 역할**: 삭제하지 않고 아키텍처로 유지하되, 성능 비교의 측정
  경로에서는 제외 — Kafka 기반 end-to-end 지연 측정은 후속 연구로 미룬다
  (§4.2).

---

## 1. 연구 개요

- **연구 질문**: 실시간 로그 이상탐지 워크로드에서 ML 서빙 프레임워크(FastAPI,
  BentoML, Triton)의 선택이 추론 지연시간·처리량에 미치는 영향과, 부하 조건별
  트레이드오프.
- **모델은 고정**: DeepLog(Du et al., 2017) 구현·개선은 연구 범위 밖. 세 서빙
  구현은 **동일 가중치·동일 전처리**를 공유 — `model/train.py`가 한 번 학습한
  체크포인트를 FastAPI/BentoML/Triton이 그대로 로드하며, 로컬 검증에서 세
  프레임워크가 동일 입력에 사실상 동일 출력(top-1 확률 소수점 7자리까지 일치)을
  냄을 확인함(§4).
- **비교 대상**: FastAPI(베이스라인, 순수 PyTorch) / BentoML(범용 ML 서빙) /
  Triton(GPU 고성능 서빙, dynamic batching)
- **이번 기말 스코프**: HDFS만 — 위 "스코프 결정 이력" 참고. 이하 §2의 BGL
  관련 내용은 전처리 코드가 존재한다는 기록일 뿐, 이번 라운드 결과물이 아님.
- **실험 환경**: AWS EC2 g4dn.xlarge (T4 GPU 16GB) — §6 참조, spot quota
  승인 대기 중.

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

### 2.2 HDFS_1 — **이번 기말 스코프**

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

### 2.3 BGL — **이번 기말 스코프 제외** (전처리 산출물은 존재, 후속 연구용)

- LogHub Zenodo 패키지에는 **raw 로그(`BGL.log`)만 존재**, 템플릿·구조화 파일이
  전혀 없음 → **Drain3 기반 로그 파서를 신규 구현** (`data/preprocess.py`의
  `_parse_bgl_structured`).
  - 파라미터: `sim_th=0.5`, `depth=4` (Zhu et al., ICSE-SEIP 2019 벤치마크의 BGL
    튜닝값을 따름)
  - 마스킹: hex 주소(`0x...`), IP, 순수 숫자를 `<*>`로 치환 후 클러스터링
  - 파싱 결과는 `data/raw/BGL/BGL.log_structured.csv`로 캐싱 (재실행 시 재파싱 안 함)
- 세션 개념이 없어 **전역 슬라이딩 윈도우**(`WINDOW_SIZE=10`, step=1) 사용. 윈도우
  내 라인 중 하나라도 이상이면 그 윈도우는 이상으로 라벨링.
- **최종 통계** (참고용 — 이번 라운드에서 학습/벤치마크에 쓰지 않음):

  | 항목 | 값 |
  |---|---|
  | 원본 로그 라인 | 4,713,493 |
  | 라인 단위 이상 비율 | **7.39%** (CLAUDE.md 명시값 7.34%와 근접 — 파서 정확도 검증됨) |
  | Drain 추출 템플릿(vocab) 수 | 991 |
  | train_normal.npy | (4,323,231, 10) |
  | test.npy / test_labels.npy | (4,713,484, 10) |
  | 윈도우 단위 이상 비율 | 8.28% |

### 2.4 재현 환경

- Python 3.11.9 venv (`.venv/`, 로컬 global Python은 3.13이라 torch/numpy
  구버전과 비호환 — 3.11로 별도 설치). **EC2(Ubuntu 22.04)에서는 시스템 기본
  python3(3.10)로 충분** — 호스트에서는 벤치마크 클라이언트(`experiments/`)만
  돌리고 torch/bentoml은 컨테이너 안에서만 쓰기 때문(§6).
- `data/requirements.txt`: `numpy==1.26.4`, `pandas==2.2.3`, `drain3==0.9.11`
- 데이터·전처리 산출물은 `.gitignore`로 제외 (`data/raw/`, `data/processed/`) —
  용량 문제(raw만 2.5GB) 때문에 저장소에는 스크립트만 커밋, 산출물은 각자 재생성.

---

## 3. 모델 — DeepLog (`model/deeplog.py`, `model/train.py`) — **HDFS 학습 완료**

- **구조**: 2-layer LSTM → Linear. 입력은 로그 키 시퀀스의 one-hot 인코딩
  `(batch, window_size-1, num_classes)`, 출력은 다음 로그 키에 대한 클래스 로짓
  `(batch, num_classes)`. 임베딩 레이어 없음(원 논문 방식, vocab이 작아 one-hot으로 충분).
- **학습**: 정상 세션/윈도우만으로 비지도 학습(다음 로그 키 예측, CrossEntropyLoss).
  val_loss 기준 best checkpoint 저장.
- **배치 처리 — DataLoader를 쓰지 않는다**: 원래는 `torch.utils.data.DataLoader`
  (num_workers=2)를 썼으나, 이 모델(hidden=64) 규모에서는 Windows의 멀티프로세스
  워커 스폰 오버헤드가 실제 순전파/역전파 시간보다 커서 병목이었다(1에폭
  624.4s). 전체 정상 시퀀스를 텐서로 메모리에 올려두고 메인 프로세스에서 직접
  배치를 슬라이싱 + one-hot을 배치 단위로 벡터화하는 방식으로 바꿔 520.5s로
  단축(§3.1). `split_train_val()`/`_batch_indices()`/`_onehot_batch()` 참고.
- **이상 판정**: 추론 시 실제 다음 키가 top-g(`num_candidates`) 예측 안에 없으면
  anomaly.
- **하이퍼파라미터** (`model/train.py` 상단 상수, HDFS 학습에 그대로 사용):

  | 파라미터 | 값 |
  |---|---|
  | hidden_size | 64 |
  | num_layers | 2 |
  | epochs | 30 |
  | batch_size | 2048 |
  | learning rate | 1e-3 |
  | val_split | 0.1 |
  | num_candidates (top-g) | 9 |

- **학습 결과 (HDFS, 2026-07-29 완료)**:
  - Best epoch: **27**, val_loss **0.1775** (최종 30에폭 val_loss도 0.1775로
    사실상 수렴, train_loss 0.1778)
  - 총 학습 시간 약 **3시간 40분**(30에폭, 에폭당 평균 440초, 로컬 CPU 4코어
    기준 — 로컬 GPU(MX450, VRAM 2GB)로 테스트했을 때 오히려 CPU보다 느렸음
    [548.8s vs 520.5s/epoch], 이 모델 크기에서는 저사양 GPU의 커널 실행/전송
    오버헤드가 이득을 상쇄하는 것으로 보임 — **공식 벤치마크는 반드시 EC2
    T4에서**, 이 로컬 실측치는 참고용)
  - 재현 시드: 데이터 train/val 분할과 에폭별 셔플 모두 `manual_seed(42)`
- **산출물** (`model/checkpoints/hdfs/`, 존재함):
  - `deeplog_best.pt` — FastAPI/BentoML이 로드
  - `deeplog.ts` — TorchScript. **`torch.jit.trace`가 아니라 `torch.jit.script`
    로 export**(§3.1 참고 — Triton 검증 중 trace 방식의 문제를 발견해 전환함)
  - `config.json` — 세 서빙 스택이 공유하는 하이퍼파라미터

### 3.1 trace → script 전환 이유 (2026-07-29, Triton 로컬 검증 중 발견)

`torch.jit.trace`로 export한 LSTM 모델을 Triton(LibTorch C++ 런타임,
`instance_group: KIND_GPU`)에 로드하면 **모델 로딩 자체는 성공하지만 추론
시점에 500 에러**가 났다: `RuntimeError: Input and hidden tensors are not
at the same device, found input tensor at cuda:0 and hidden tensor at cpu`.
trace로 내보낸 `nn.LSTM`은 가중치가 LibTorch의 `.to(cuda)` 파라미터 순회에서
제대로 인식되지 않아 GPU로 이동하지 않는 알려진 이슈. `torch.jit.script`는
`nn.LSTM` 서브모듈 구조를 그대로 보존해 파라미터가 정상적으로 이동한다 —
재학습 없이 기존 체크포인트에서 `torch.jit.script()`로 재추출해 해결,
eager 모드와 출력이 `torch.allclose`로 완전히 일치함을 확인.

---

## 4. 서빙 아키텍처 — **로컬(Windows, Docker Desktop) 검증 완료** (2026-07-29)

세 프레임워크 모두 **동일한 요청/응답 스키마**를 노출 (공정 비교 목적):

```
POST /predict
  요청: { "log_keys": [int, ...] (길이 = window_size-1), "next_key": int? }
  응답: { "top_candidates": [int,...], "probabilities": [float,...],
          "is_anomaly": bool?, "latency_ms": float }
GET  /health   (BentoML만 POST — 아래 버그 3번 참고)
```

| 항목 | FastAPI (port 8000) | BentoML (port 3000) | Triton (port 8001 서버 / 8002 래퍼) |
|---|---|---|---|
| 역할 | 베이스라인 — 순수 PyTorch, 프레임워크 오버헤드 없음 | 범용 ML 서빙 — 모델 스토어, 자동 스키마 문서화, 리소스/타임아웃 선언 | GPU 고성능 서빙 — C++ 런타임, dynamic batching |
| 모델 로드 | `torch.load(deeplog_best.pt)`, lifespan에서 1회 | BentoML model store에서 `bentoml.pytorch.load_model()` | `deeplog.ts`(TorchScript, script 방식)를 Triton 서버가 로드, 래퍼는 HTTP 클라이언트로 위임 |
| 전처리(one-hot) | 서버 프로세스 내(PyTorch) | 서버 프로세스 내(PyTorch) | **래퍼**가 numpy로 수행 후 Triton에 전달 |
| 동시성 제어 | 없음(uvicorn 기본) | `traffic.timeout=10s` 선언 | `max_batch_size=64`, `dynamic_batching`(preferred batch 8/16/32/64, max_queue_delay=100us) |
| 리소스 선언 | 없음 | `resources.memory=1Gi` | `instance_group: KIND_GPU, count=1`, docker-compose `gpus: all` |

**로컬 검증 결과**: 동일 입력(`log_keys=[2,28,2,28,18,18,18,15,15], next_key=15`)에
세 프레임워크 모두 `top_candidates=[15,11,13,23,22,25,17,18,9]`, top-1 확률
0.9999996으로 사실상 동일 출력(마지막 몇 자리 부동소수점 차이는 실행 백엔드
차이로 정상). 이 과정에서 **실제 버그 5개를 발견·수정**했다:

1. **`docker-compose.yml` BentoML 서비스에 체크포인트 볼륨 마운트 누락** —
   FastAPI에는 `../model/checkpoints:/workspace/model/checkpoints:ro`가 있는데
   BentoML에는 없어 컨테이너가 모델을 찾지 못함. 추가함.
2. **BentoML `/predict` 요청 스키마가 FastAPI/Triton과 다름** — BentoML 1.3
   신규 SDK는 단일 Pydantic 파라미터(`req: PredictRequest`)를 파라미터 이름으로
   한 번 더 감싼 `{"req": {...}}`를 요구해, `benchmark.py`가 보내는 flat body와
   불일치(그대로 뒀으면 BentoML 벤치마크 전량이 422로 실패했을 것). `predict`
   시그니처를 `log_keys`/`next_key` 개별 파라미터로 바꿔 flat body로 통일.
3. **BentoML `/health`는 GET 불가, POST 전용** — `@bentoml.api`가 구조적으로
   POST만 지원(코드로 못 고침). `run_benchmark.sh`의 헬스체크와 CLAUDE.md
   검증 절차에 문서화하는 것으로 처리.
4. **Triton 포트 설정 미스매치** — `docker-compose.yml`이 포트 오버라이드 없이
   기동해 8001이 실제로는 Triton 기본 gRPC 포트였음(HTTP는 기본 8000). 이
   프로젝트 전체가 "8001=HTTP"로 문서화돼 있어(.env.example 등) 이름 체계를
   유지한 채 `--http-port=8001 --grpc-port=8003 --metrics-port=8004`를
   명시 지정.
5. **Triton GPU 컨테이너 기동 실패 (로컬)** — `deploy.resources.reservations.
   devices`(Swarm 지향 문법)가 Docker Desktop WSL2에서 `nvml error: not
   supported: unknown`으로 실패. `gpus: all` 축약 문법으로 전환해 해결 —
   네이티브 Linux Docker Engine(EC2)에서도 동일하게 지원되는 문법이라 EC2에도
   그대로 적용됨.
6. **TorchScript trace→script 전환** — §3.1 참고. 이건 버그라기보다 모델
   export 방식 자체의 변경.

**Triton 구조가 2단**이라는 점이 논문에서 특히 설명이 필요한 부분: 클라이언트 →
래퍼(8002, FastAPI+tritonclient) → Triton 서버(8001, C++ 런타임) 순으로 홉이 하나
더 있음. 벤치마크 시 이 홉의 오버헤드도 결과 해석에 포함해야 함.

### 4.1 해결됨 (2026-07-31) — FastAPI/BentoML도 Triton과 동일하게 GPU 사용

기존에는 `serving/fastapi/requirements.txt`·`serving/bentoml/requirements.txt`가
`torch==2.4.1`만 지정(CUDA index-url 없음)해 CPU 전용 torch가 설치됐다.
EC2(T4 GPU)에서 실행해도 FastAPI/BentoML은 CPU 추론, Triton만 GPU 추론이 되어
"프레임워크 선택의 영향"이 아니라 "CPU vs GPU"를 비교하게 되는 confound가
있었음.

**결정**: 고친다. "FastAPI/BentoML=CPU 베이스라인"으로 의도적으로 남기는
대안도 검토했으나, 연구 질문이 "프레임워크 선택의 영향"이지 "CPU vs GPU"가
아니므로 confound를 제거하는 쪽을 택함.

**수정 내용**:
- `serving/fastapi/requirements.txt`·`serving/bentoml/requirements.txt`에
  `--extra-index-url https://download.pytorch.org/whl/cu121` +
  `torch==2.4.1+cu121` 지정. 버전에 로컬 세그먼트(`+cu121`)까지 명시해야
  하는 이유: `--extra-index-url`만 쓰고 `torch==2.4.1`로 두면 PyPI 기본
  인덱스의 CPU 휠과 이 인덱스의 GPU 휠이 둘 다 그 버전 문자열을 만족해
  어느 쪽이 설치될지 모호해짐.
- `docker-compose.yml`의 `fastapi`·`bentoml` 서비스에 Triton과 동일하게
  `gpus: all` 추가.
- 로컬(MX450)에서 재빌드 후 컨테이너 안에서 직접
  `torch.cuda.is_available()`을 실행해 FastAPI·BentoML 둘 다 `True`,
  `device: NVIDIA GeForce MX450`을 확인함.

### 4.2 Kafka 스트리밍 아키텍처의 역할과 위치 (2026-07-31 결정)

**결정: Kafka는 삭제하지 않고 "구현된 실시간 스트리밍 아키텍처"로 유지한다.
다만 프레임워크 성능 비교의 주 측정 도구로는 쓰지 않는다.**

**이유**: 연구 주제("실시간 로그 이상탐지 워크로드")가 스트리밍 구조를
전제하므로 Kafka는 시스템 아키텍처로 남아 있어야 한다. 하지만 프레임워크 간
성능 비교는 부하를 정밀 통제할 수 있는 concurrency sweep
(`experiments/benchmark.py`)으로 하는 게 방법론적으로 더 적합하다 — Kafka를
거치면 프로듀서 전송 속도·컨슈머 처리 속도 등 추가 변수가 섞여 "프레임워크
선택의 순수한 영향"을 분리해서 보기 어려워진다.

**역할 분담**:
- **Kafka(`kafka/producer.py`, `kafka/consumer.py`) = 구현된 실시간 스트리밍
  아키텍처** — 발표·논문의 시스템 다이어그램에 포함(아래 텍스트 설명 참고).
  코드는 그대로 유지, 삭제하지 않음.
- **프레임워크 성능 비교 = `experiments/benchmark.py`의 concurrency sweep만
  사용.** `run_benchmark.sh`가 기동하는 컨테이너(fastapi/bentoml/triton
  (-wrapper))에 kafka 서비스가 끼어들지 않음을 코드로 확인함(2026-07-31 —
  `run_benchmark.sh`·`benchmark.py` 둘 다 "kafka" 문자열 검색 결과 0건).
- **Kafka 기반 end-to-end 지연 측정은 후속 연구로 남긴다** —
  `kafka/consumer.py`가 이미 요청별 `latency_ms`를 CSV로 남기도록 구현은
  돼 있지만, 이번 기말 결과물의 성능 비교 수치에는 포함하지 않는다.

**시스템 아키텍처 (발표 다이어그램용 텍스트 설명)**:

```
[HDFS 로그 시퀀스] → [Kafka Producer] → [Kafka Topic] → [Kafka Consumer]
        → [서빙 프레임워크 POST /predict: FastAPI | BentoML | Triton]
        → [응답 (top_candidates, is_anomaly, latency_ms)]
```

- `kafka/producer.py`가 전처리된 로그 시퀀스를 스트리밍으로 Kafka 토픽에
  흘려보내고, `kafka/consumer.py`가 이를 소비해 서빙 프레임워크의
  `POST /predict`를 호출한 뒤 이상 판정 응답을 받는다 — 이 경로 전체가
  "실시간 로그 이상탐지" 연구 주제를 아키텍처로 뒷받침한다.
- **다만 이번 기말 결과물의 프레임워크 성능 비교 수치는 이 Kafka 경로를
  거치지 않는다.** `experiments/benchmark.py`가 서빙 프레임워크의
  `POST /predict`를 concurrency sweep으로 **직접** 호출해 지연시간·처리량을
  측정한다 — 위 다이어그램에서 [서빙 프레임워크] 박스 이후만 측정 대상이고,
  Kafka 구간(Producer→Topic→Consumer)은 측정에 포함되지 않는다.
- 발표 자료 작성 시 주의: Kafka는 "이 시스템이 실시간 스트리밍 워크로드에서
  동작한다"는 아키텍처적 증거로 포함하되, "프레임워크 A가 B보다 빠르다"는
  수치적 주장은 Kafka를 거치지 않은 concurrency sweep 결과로만 뒷받침해야
  한다 — 이 둘을 같은 그래프·같은 수치로 섞지 않는 것이 방법론적으로 중요함.

---

## 5. 벤치마크 설계 (`experiments/benchmark.py`, `experiments/run_benchmark.sh`)

- **방식**: Closed-loop — `asyncio.Semaphore(concurrency)`로 항상 N개 요청을
  in-flight 상태로 유지, 하나 끝나면 즉시 다음 요청 전송.
- **부하 단계(concurrency sweep)**: 1 → 4 → 8 → 16 → 32 → 64 전체를 측정하고,
  **저=4 / 중=16 / 고=64를 논문·발표용 대표값으로 매핑**(2026-07-31 결정).
  `experiments/benchmark.py`의 `--concurrency`는 6개 값 그대로 실행한다(전체
  sweep이 있어야 fig1~3 곡선이 나옴) — 압축은 시각화·통계 단계에서만 한다:
  `plot_results.py`의 `LOAD_LEVELS` 상수와 `stat_test.py`의 `--concurrency`
  기본값(4 16 64)이 이 매핑과 동기화돼 있음.
- **단계별 측정 시간**: 30초 (기본값), 워밍업 5초(concurrency=1) 선행
- **반복 측정**: **3사이클** (`CYCLES` 기본값, §스코프 결정 이력 참고).
  `run_benchmark.sh`가 사이클마다 `shuf`로 프레임워크 실행 순서를
  랜덤화하고, 결과를 `experiments/results/cycle{1,2,3}/`에 분리 저장 +
  `run_manifest_*.txt`에 실제 사용된 순서를 기록.
- **컨테이너 생명주기**: 프레임워크 1개씩 `docker compose up` → 헬스체크
  대기(Triton은 서버 ready 확인 후 wrapper 기동) → 벤치마크 → `stop`+`rm`
  → 다음 프레임워크. 세 프레임워크를 동시에 띄우지 않음(CLAUDE.md 규칙).
- **자원 사용률 로깅**: `--container <docker container id>`(docker stats
  기반 CPU%·메모리) + `--monitor-gpu`(nvidia-smi 기반 GPU 사용률·메모리)를
  latency 측정과 동일한 `time.time()` 타임스탬프로 백그라운드 샘플링(기본
  1초 간격). 결과 CSV 2종:
  - `{framework}_{dataset}.csv` — concurrency별 지연시간/처리량 + 자원
    사용률 평균/최댓값
  - `{framework}_{dataset}_resources.csv` — 원본 시계열(타임스탬프,
    concurrency, cpu_pct, mem_used_mb, gpu_util_pct, gpu_mem_used_mb)
- **요청 payload**: 각 데이터셋 test.npy에서 2,000개 무작위 샘플링 (캐시 편향 방지)
- **결과 분석 도구** (`experiments/`, 2026-07-31 갱신):
  - `plot_results.py` — `results/cycle{1,2,3}/`를 전부 읽어 사이클 평균±표준편차로
    fig1(p50)~fig3(throughput) sweep 곡선을 그리고, fig4는 저/중/고 3단계
    대표값만 데이터셋별로 압축해 비교차트로 그린다. 한글 라벨(저/중/고)이
    기본 폰트(DejaVu Sans)에 없어 깨지는 문제가 있어 Malgun Gothic 등
    한글 폰트를 자동 탐지해 적용하도록 처리함.
  - `stat_test.py` (신규) — Mann-Whitney U 검정. 아래 §5.1 참고.
  - 둘 다 합성 데이터로 로딩→집계→출력 전 과정 스모크 테스트 완료.
- Kafka 스트리밍 경로(`kafka/producer.py`, `kafka/consumer.py`)는 구현된
  아키텍처로 유지하되 본 성능 비교엔 쓰지 않음 — §4.2 참고.

### 5.1 측정 운영 규칙 (CLAUDE.md와 동기화)

- **세 프레임워크를 동시에 실행하지 않는다** — `run_benchmark.sh`가 코드로
  강제(§5 컨테이너 생명주기).
- **순서 효과 통제** — 사이클마다 `shuf`로 랜덤화, manifest에 기록.
- **공식 측정은 EC2 g4dn.xlarge에서만** — 로컬(개발 랩탑) 벤치마크 결과는
  `experiments/results/`에 저장하지 않음. 로컬 GPU(MX450)는 애초에 T4와
  특성이 달라 비교 불가.
- **통계 검정: Mann-Whitney U** — `experiments/stat_test.py`(2026-07-31 신규
  작성)로 구현됨. 사이클(기본 3회)을 검정의 표본 단위로 삼는다 —
  benchmark.py CSV엔 개별 요청의 원본 지연시간이 없고 concurrency 레벨별
  요약 통계만 있기 때문. 사이클 수가 적으면(n=3) Mann-Whitney U(양측)가
  도달 가능한 최소 p-value가 약 0.1로 제한되므로, 스크립트가 그 한계를
  결과와 함께 출력해 오독을 방지한다.
- **핫패스에 pandas 미사용** — `kafka/*.py`, `serving/*/app.py`, `service.py`,
  `experiments/benchmark.py` 확인됨(2026-07-30 재확인). pandas는
  `data/preprocess.py`·`experiments/plot_results.py`에서만 사용.

---

## 6. EC2 준비 상태 — **스크립트·AWS 리소스 완료, quota 승인 대기**

### 6.1 자동화 스크립트 (`experiments/aws/`, 전부 로컬에서 문법·일부 실행 검증됨)

| 스크립트 | 역할 |
|---|---|
| `setup_instance.sh` | user-data — NVIDIA 드라이버·CUDA·Docker+compose 플러그인·nvidia-container-toolkit·git·AWS CLI v2 설치 |
| `launch_spot_instance.sh` | g4dn.xlarge 스팟 인스턴스 요청, `setup_instance.sh`를 user-data로 전달 |
| `sync_to_s3.sh` | (로컬 실행) `data/processed/`·`model/checkpoints/` → S3 업로드 |
| `bootstrap_ec2.sh` | (EC2에서 실행) git clone + S3 다운로드 + Triton 레포 생성 + Docker 이미지 빌드 + BentoML 모델 등록(컨테이너 안에서, 호스트에 torch/bentoml 불필요) |
| `spot_backup_watcher.sh` | (EC2, 백그라운드) 스팟 인터럽션 통지 감지 시 즉시 백업 + 5분마다 정기 백업 → S3 (EBS 대신 S3를 택한 이유: 루트 볼륨이 `DeleteOnTermination=true`라 인스턴스 종료 시 함께 사라짐) |
| `one_shot_launch.sh` | 위 전부를 순서대로 실행하는 오케스트레이터. **`run_benchmark.sh` 자체는 의도적으로 자동 실행하지 않음** — 마지막 확인은 사람이 하도록 |
| `update_sg_ip.sh` | 보안그룹 인바운드를 실행 시점의 현재 공인 IP로 갱신(재택/이동 시 반복 실행용). 로컬 상태 저장 없이 SG 자체를 진실의 원천으로 삼아 revoke/authorize — 정상 케이스·IP 변경 케이스 둘 다 로컬에서 실행 검증됨(2026-07-30) |

### 6.2 이미 생성된 AWS 리소스 (ap-northeast-2)

| 리소스 | ID/이름 |
|---|---|
| 서브넷 | `subnet-0f9c2552a0f61660c` (ap-northeast-2a, 기본 VPC — g4dn.xlarge 제공 AZ로 확인 후 선택) |
| 보안그룹 | `sg-063774d1ab4874831` (`deeplog-serving-sg`) — 22/8000/3000/8001/8002/9092, 인바운드는 실행 시점 공인 IP로 제한(`update_sg_ip.sh`로 갱신) |
| 키페어 | `deeplog-ec2-key`, `.pem`은 로컬 `~/.ssh/deeplog-ec2-key.pem`에 저장(본인 읽기 전용) |
| S3 버킷 | `deeplog-serving-2025720449` (ap-northeast-2, 2026-07-31 생성) |

### 6.3 남은 것

- **g4dn.xlarge 스팟 quota 증가 요청 — 승인 대기 중** (2026-07-30 요청, 승인까지
  수시간~하루 예상). 이게 전체 파이프라인의 마지막 병목.
- quota 승인되면: `sync_to_s3.sh` → `one_shot_launch.sh` (환경변수는 위 6.2
  값 그대로 사용) → EC2에서 `run_benchmark.sh hdfs` 직접 실행.

---

## 7. 다음 세션에서 확인할 것

- **g4dn.xlarge 스팟 quota 승인 여부** — 유일하게 남은 외부 의존성. 승인되면
  `one_shot_launch.sh` 실행.
- quota 승인 후 EC2에서 벤치마크를 실제로 돌리기 전에, FastAPI/BentoML
  이미지가 GPU torch(cu121)로 다시 빌드되는지 확인(로컬에서는 검증했지만
  EC2 쪽 `bootstrap_ec2.sh` 빌드 경로로도 한 번 더 확인 권장).
