# EXPERIMENT.md — deeplog-serving-analysis 실험 문서

> 이 문서는 **코드 없이 논문을 쓰는 사람(채팅 Claude/ChatGPT 등)을 위한 스냅샷**입니다.
> 소스는 항상 코드이고, 이 문서는 특정 시점의 재생성 가능한 요약본입니다.
> **마지막 갱신: 2026-11-02. 기말(2026-08-11) 발표 라운드는 HDFS만으로 종료됐고
> (FastAPI/BentoML/Triton 모두 n=5, 27건 중 25건 p<0.05 — §8), 현재 졸업논문
> BGL 라운드 진행 중이다. 이번 갱신 내용: CI/CD를 범위 밖에서 병행 항목으로
> 올리고 두 단계를 모두 마쳤다 — 자동 검사(CI, GitHub Actions)와 서빙 이미지의
> GitHub Container Registry 푸시(CD)(§스코프 이력 2026-11-02). 실험 변수·측정
> 조건은 바뀌지 않았고, HDFS 측정값과 결론(§8)도 그대로다.**
> 코드가 바뀌면 이 문서도 다시 생성해야 합니다 — 특히 하이퍼파라미터·설정값은
> 아래 표만 믿지 말고 실제 config 파일과 대조하세요.

---

## 0. 진행 상황

| 단계 | 상태 | 비고 |
|---|---|---|
| 1. 환경 설정 (.env) | ✅ 완료 | `DEEPLOG_S3_BUCKET` 추가됨(§6) |
| 2. 데이터 다운로드·전처리 | ✅ 완료(HDFS·BGL 모두) | §2 참조. 기말 라운드는 HDFS만 사용, BGL 라운드에서 BGL 산출물 사용 |
| 3. DeepLog 학습 (HDFS) | ✅ 완료 | §3 참조. BGL 학습은 아래 12행 |
| 4. 모델 변환/패키징 (Triton TorchScript, BentoML) | ✅ 완료 | §3·§4 참조 |
| 5. Docker 스택 통합·스모크 테스트 | ✅ 완료(로컬) | §4 참조 — 버그 5개 발견·수정 |
| 6. EC2 인프라 준비 | ✅ 완료 (quota 승인됨, 사용한 인스턴스 3대 모두 terminate함 — §6.2) | §6 참조 |
| 7. 벤치마크 실행 | ✅ **완료** (EC2 g4dn.xlarge T4, HDFS, FastAPI/BentoML/Triton 모두 n=5) | §8 참조 |
| 8. 결과 시각화·통계 검정 | ✅ 완료 (fig1~6, Mann-Whitney U, 27건 중 25건 p<0.05) | §8 참조 |
| 9. 결과 보관 | ✅ 완료 (2026-09-12) | 결과 CSV·그림을 `.gitignore`에서 빼 저장소에 포함 — §스코프 이력 2026-09-12 |
| 10. BGL 라운드 스코프 확정 | ✅ 완료 (2026-10-13) | §스코프 이력 2026-10-13 |
| 11. BGL 이상 판정 기준(`num_candidates`) 결정 | ✅ 완료 (2026-10-13, g=9) | §스코프 이력 2026-10-13 |
| 12. BGL 학습 | ⏳ 진행 중 (2026-10-31 재시작, 로컬 GPU, 이어 학습 방식으로 30에폭 약 7시간을 나눠 진행. 2026-11-02 기준 19/30에폭, val_loss 0.1411) | §스코프 이력 2026-10-31 |
| 12-1. 서빙 스택 데이터셋 분리 + 서빙 데이터셋 검증 장치 | ✅ 코드 수정 완료 / ⬜ 실제 기동 검증 대기 | §스코프 이력 2026-10-31 |
| 12-2. BGL 서빙 패키징·로컬 검증 | ⬜ 대기 (학습 완료 후) | §9 |
| 13. BGL EC2 벤치마크·분석 | ⬜ 대기 | §9 |
| 병행 A. CI — 자동 검사 (GitHub Actions) | ✅ 완료 (2026-11-02, GitHub에서 static·smoke·images 통과, Triton 포함 세 프레임워크 응답 일치) | §스코프 이력 2026-11-02 |
| 병행 B. CD — 서빙 이미지 레지스트리 푸시 | ✅ 완료 (2026-11-02, ghcr.io 공개 이미지 3종. 실험 절차에서 이 이미지를 쓸지는 미결정) | §스코프 이력 2026-11-02 |

**HDFS는 논문에 쓸 실측 수치·그래프·통계 검정 결과가 전부 있고 원본도
저장소에 있다. 현재는 BGL 라운드 진행 중이다(§9).**

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

당시 스코프 규칙은 CLAUDE.md "현재 스코프" 섹션에 있었다(2026-10-13에
BGL 라운드 기준으로 교체됨 — 아래 10-13 이력 참고) — 이 문서(EXPERIMENT.md)는 "왜"를, CLAUDE.md는 "지금 무엇을"을 기록한다.

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

### 2026-08-04 ~ 08-05 — EC2 실행, 실전에서만 드러난 버그 다수 수정, 공식 벤치마크 완료

quota는 8/4에 승인됐지만 실제로 인스턴스를 띄워 벤치마크를 끝내기까지
**세션 3회, 인스턴스 3대**가 걸렸다 — 전부 로컬 검증만으로는 드러나지
않는, EC2라는 실제 환경에서만 나타난 문제였다. 기록해두는 이유: 다음에
비슷한 인프라를 구성할 때 같은 실수를 반복하지 않기 위함.

**겪은 문제와 수정 (시간순)**:
1. **스팟 가격 상한이 시장가보다 낮음** — `EC2_SPOT_MAX_PRICE` 기본값을
   0.30→0.45로.
2. **스팟 용량 부족으로 인스턴스 회수** — 스팟의 고유 리스크. 재현성보다
   신뢰성이 급해 **온디맨드 옵션**(`launch_ondemand_instance.sh` 신규,
   `EC2_LAUNCH_MODE=spot|ondemand` 토글)을 추가.
3. **온디맨드 `--user-data`가 실행되지 않음** — Windows Git Bash에서
   `file://$SCRIPT_DIR/...` 경로를 AWS CLI가 못 읽는 문제 + `$(cat file)`로
   바꾼 뒤에도 원인 불명 실행 안 됨 이슈가 있었으나, 최종적으로는 스크립트
   내용 자체는 정상 전달되고 있었음(§아래 5번과 연결).
4. **재부팅 후 완료 마커 소실** — `setup_instance.sh`가 완료 마커를
   `/tmp/setup_instance_done`에 남겼는데, 드라이버 설치 후 바로 이어지는
   재부팅으로 `/tmp`(tmpfs)가 지워져 실제로는 520초 만에 성공했는데도
   `one_shot_launch.sh`가 "안 끝남"으로 오판해 타임아웃. **`/var/lib/`로
   마커 위치 변경**, 대기 타임아웃도 600s→1500s로 연장.
5. **회사 WiFi가 SSH(22번)와 해당 EC2 IP 자체를 차단** — AWS 쪽(보안그룹·
   라우트 테이블·인스턴스 상태) 전부 정상이었는데도 접속 불가. 원인은
   로컬 네트워크(회사망) 방화벽. 집 네트워크로 옮기니 즉시 해결 — 인프라
   문제가 아니라 실행 위치 문제였음을 완전히 특정하는 데 시간이 걸렸다.
6. **`python3-venv` 미설치** — Ubuntu 22.04 기본 이미지엔 venv 모듈이 없어
   `python3 -m venv`가 "ensurepip is not available"로 실패.
   `setup_instance.sh`(사전 설치) + `bootstrap_ec2.sh`(방어적 재설치)
   양쪽에 추가.
7. **EC2 인스턴스에 AWS 자격증명이 없음** — `bootstrap_ec2.sh`의
   `aws s3 ls` 체크가 자격증명 없이 조용히 실패해 "S3에 없음" 분기를
   타고, **데이터·체크포인트를 하나도 못 받은 채로 이미지 빌드까지
   성공**해버려 한참 뒤 벤치마크 실행 시점에야 문제가 드러남(BentoML이
   모델을 못 찾아 20분 넘게 응답 없이 행). `one_shot_launch.sh`가
   로컬 `~/.aws/credentials`를 인스턴스로 자동 복사하도록 추가, 두
   스크립트 모두 자격증명 없으면 조용히 넘어가지 않고 경고하도록 수정.
8. **`wait_for_health`의 curl에 타임아웃이 없음** — BentoML 앱이
   위 7번 문제로 크래시 상태였는데, `docker-proxy`는 포트를 열어둔
   채라 curl이 TCP 연결은 되고 응답만 영원히 안 와 **20분 넘게 무한
   대기**. `--max-time 5` 추가.
9. **BentoML `/health`가 GET에서 무한 대기** — §4 버그 3번(POST 전용)의
   실제 영향이 여기서 드러남: GET은 그냥 405가 아니라 응답 자체가 안
   와서 위 8번과 겹쳐 문제를 키움. `wait_for_health`가 `label=="bentoml"`
   이면 POST로 호출하도록 수정.
10. **`pkill -f run_benchmark.sh`가 자기 자신을 죽임** — `-f`(전체 명령행
    매칭)라 pkill을 실행 중인 SSH 셸 자신의 명령행에도 "run_benchmark.sh"
    문자열이 들어 있어 셸이 죽고 SSH가 exit 255로 끊김. PID로 직접
    kill하는 것으로 우회(스크립트에는 반영 안 함 — 1회성 디버깅 명령).
11. **`spot_backup_watcher.sh`를 `/tmp/`에서 실행해 자기 위치 기준 경로
    계산이 깨짐** — `${BASH_SOURCE[0]}` 기준으로 프로젝트 루트를 찾는데
    `/tmp/spot_backup_watcher.sh`로 scp한 뒤 거기서 실행하면
    `_PROJECT_ROOT`가 `/`가 되어 백업이 조용히 아무 일도 안 함. 이번엔
    결과를 scp로 직접 받아 우회했고 당시엔 스크립트를 고치지 않았다.
    → **2026-09-12에 수정 완료**(아래 09-12 이력 참고) — 지금은 `/tmp`에서
    실행해도 정상 동작한다.

**결과**: 위 수정을 전부 반영한 뒤 재시도해 **3사이클 전부(단, cycle1의
triton 1건은 첫 이미지 pull 직후 콜드스타트로 헬스체크 실패 — 재시도
없이 스킵되고 다음 프레임워크로 자동 진행되어 이후 사이클엔 영향 없음)
정상 완료**. 결과는 §8 참고.

### 2026-08-06 — 사이클 3→5 확장 중 Triton 래퍼 버그 발견, 기존 데이터 오염 확인, 재측정

**배경**: 3사이클(n=3)로는 Mann-Whitney U가 도달 가능한 최소 p-value가
0.1이라 27건 전부 `p<0.05` 도달이 구조적으로 불가능했다(§5.1). 표본을
5사이클로 늘리기로 하고, 기존 3사이클 결과에 `run_benchmark.sh`를
`CYCLE_START` 옵션(신규 추가)으로 cycle4·5만 이어붙이는 방식을 택함 —
코드 변경 없음을 `git log`로 확인했으니 사이클을 이어붙여도 측정 조건은
동일하다고 판단했다.

**세 프레임워크 재검증 중 실제 버그 발견**: EC2를 재기동해 fastapi·bentoml·
triton을 동시에 띄우고 CLAUDE.md 검증 절차(§4)를 다시 돌리는 과정에서
`triton-wrapper`의 `/health`가 500을 냈다. 원인은
`serving/triton/app.py`가 동기(gevent 기반) `tritonclient.http`를
FastAPI의 sync 엔드포인트로 노출해, uvicorn/anyio가 요청을 스레드풀에
분산 디스패치할 때 gevent greenlet이 자신을 생성한 스레드가 아닌 다른
스레드에서 재개되면서 `greenlet.error: Cannot switch to a different
thread`가 발생하는 것이었다.

**기존 데이터 오염 확인**: 이 버그가 8/5 원본 3사이클 중 cycle2·3에도
있었다는 걸 그 사이클의 `n_errors` 컬럼에서 재확인했다(concurrency=4·8에서만
11~30건, 다른 구간은 0 — 스레드풀이 여러 스레드를 쓰는 저·중 동시성
구간에서만 발현). 버그가 있는 상태에서 실패한 요청은 대기 없이 즉시
에러 처리되어 **성공한 요청의 지연시간은 실제보다 낮게, 처리량은 실제보다
낮게** 측정돼 있었다. 즉 cycle2·3의 triton 수치는 "다른 조건에서 측정한
값"이 아니라 "고장난 상태에서 측정한 잘못된 값"이라 통계 분석에 그대로
쓸 수 없었다.

**결정**: cycle2·3의 triton CSV만 통계 분석에서 제외한다(fastapi·bentoml은
이 버그와 무관해 그대로 유효). 파일은 삭제하지 않고
`experiments/results/_archived_buggy_triton/`로 옮겨 보관(사유는 그
디렉토리의 `README.md` 참고). `tritonclient.http.aio`(asyncio 기반
비동기 클라이언트)로 교체해 버그를 수정하고, EC2를 한 번 더 재기동해
**triton만 3사이클(cycle6·7·8) 재측정**해 triton도 fastapi·bentoml과
동일하게 n=5(전부 수정판 코드로 측정)를 채웠다.

**수정 검증**: 재빌드한 wrapper에 health 10회 연속 호출 + predict 40건
동시 호출(20 병렬 × 2라운드)을 스트레스 테스트해 에러 0건 확인. 실제
벤치마크에서도 concurrency=4 기준 처리량이 버그 있던 버전 대비 약 2배로
늘었다(300 rps → 620 rps) — 스레드 직렬화가 사라지며 진짜 동시성을
처리하게 된 효과.

**최종 결과**: FastAPI·BentoML은 cycle1~5(n=5), Triton은 cycle4~8(전부
수정판 코드, n=5)로 재분석. 27건의 Mann-Whitney U 검정 중 **25건이
`p<0.05`**(§8 참고) — 남은 2건은 bentoml-triton 쌍(가장 근접한 두
프레임워크)이라 통계적으로 자연스럽다.

**교훈**: 통계적 검정력을 올리려고 사이클 수를 늘리는 작업이 오히려
기존 코드의 잠재 버그(스레드 경쟁)를 실전에서 드러냈다. 로컬 검증에서는
안 보였던 문제였음 — 동시성이 실제로 여러 스레드를 타야 재현되는
버그라 단발성 predict 호출로는 절대 못 잡는다.

### 2026-09-12 — 발표 후 저장소 점검: 문서·코드·결과 보관의 정합성 정리

기말 발표(8/11) 이후 저장소 상태를 점검하면서, **실험은 끝났는데 문서와 코드가
서로 어긋나 있던 지점**들을 정리했다. 측정값·그래프·통계 결과(§8)는 전혀
건드리지 않았고 결론도 그대로다 — 바뀐 건 다음 라운드(BGL)에서 같은 함정을
밟지 않게 하는 설정·스크립트·보관 정책이다.

**1. 사이클 수 불일치 해소 (문서 5 vs 코드 3)**: 8/6에 표본을 n=5로 늘려
분석까지 마쳤는데, `run_benchmark.sh`의 `CYCLES` 기본값은 3으로 남아 있었고
CLAUDE.md도 "3사이클"로 적혀 있었다. 그대로 두면 **다음 라운드에서 옵션 없이
스크립트를 돌렸을 때 n=3 표본이 나와 §5.1의 검정력 한계(최소 도달 가능
p-value 약 0.1)를 그대로 반복**하게 된다. `CYCLES` 기본값을 5로, CLAUDE.md
기술도 5사이클로 맞췄다.

**2. `spot_backup_watcher.sh` 경로 버그 수정** (08-04~05 이력 11번, §6.1):
`/tmp`로 scp해서 실행하면 `${BASH_SOURCE[0]}` 기준 루트 계산이 `/`가 되어
백업이 조용히 아무 일도 하지 않던 문제. 프로젝트 루트를 후보 순서대로
검증하며 찾도록 바꿨고(명시 지정 `DEEPLOG_PROJECT_ROOT` → 실행 시점 CWD →
스크립트 위치 → 클론 기본 경로), 어느 후보도 레포가 아니면 조용히 넘어가지
않고 즉시 종료한다. `backup()`도 "백업 대상 디렉토리 없음"과 "sync 실패"를
구분해 경고하도록 했다 — 이 프로젝트는 경로 버그와 EC2 자격증명 누락
(08-04~05 이력 7번) 양쪽 모두로 "로그는 멀쩡한데 백업이 안 된" 사고를 겪었기
때문에, 실패는 반드시 소리를 내야 한다. 레포 밖 실행·탐색 실패·명시 지정·
대상 없음 4개 시나리오를 실제로 실행해 확인했다.

**3. 벤치마크 결과를 버전 관리에 포함**: `experiments/results/`와
`experiments/figures/`가 `.gitignore`에 걸려 있어 **논문 수치의 원본이 로컬
사본 하나로만 존재**했다. 전체 약 740KB로 용량 부담이 없고, 데이터·체크포인트와
달리 재생성에 EC2 재기동이 필요한 산출물이라 저장소에 넣었다. CLAUDE.md 규칙상
로컬 드라이런 결과는 애초에 이 디렉토리에 저장하지 않으므로, 여기 들어오는
파일은 전부 EC2 공식 측정치다.

부수적으로 이 문서의 사소한 불일치 두 건도 함께 고쳤다 — §0의 EC2 인스턴스
대수(2대 → §6.2 기준 3대), §5의 `plot_results.py` 입력 경로 표기
(`cycle{1,2,3}/` → 실제 코드의 `cycle*/` glob).

### 2026-10-13 — 졸업논문 스코프로 전환: BGL 라운드 시작

**배경**: 기말 라운드는 시간 제약으로 HDFS만 다뤘다(07-30 이력). 졸업논문은
§연구 목적대로 HDFS·BGL 두 데이터셋을 다루므로, BGL을 추가하기 전에 두
데이터셋 결과를 같은 표에서 비교할 수 있도록 조건을 먼저 고정했다. CLAUDE.md
"현재 스코프" 섹션을 이 내용으로 교체했다.

**결정**:
- **데이터셋: HDFS + BGL.** HDFS는 기말 측정분을 그대로 쓰고 재측정하지 않는다.
- **모델 아키텍처 고정, vocab 크기만 데이터셋을 따른다.** BGL은 템플릿이 991개로
  HDFS(29개)의 30배 이상이라 one-hot 입력이 훨씬 넓어지고, 같은 구조에서도
  파라미터가 약 5.9만 개 → 약 36.8만 개로 늘어난다. 그래도 임베딩 레이어 추가
  같은 구조 변경은 하지 않는다 — 구조가 다르면 데이터셋 간 비교가 깨지고
  "모델 고정" 원칙에도 어긋난다. hidden size·층 수·window도 HDFS와 동일하게
  유지한다. 오히려 같은 서빙 스택에 더 무거운 워크로드를 하나 더 얹는 셈이라,
  HDFS에서 본 프레임워크 순위가 모델이 무거워져도 유지되는지가 논의 거리가 된다.
  vocab 크기는 학습·서빙·Triton 설정 생성 모두 `vocab.json`/`config.json`에서
  읽으며 어디에도 하드코딩돼 있지 않음을 확인했다. (당시 "코드 변경은 필요
  없다"고 판단했으나 이는 학습까지만 맞았다 — 서빙 스택이 데이터셋을 하나만
  담는 구조라 수정이 필요했다. 아래 10-31 이력 참고.) `model/deeplog.py` 주석의 "vocab 100개 수준이라 one-hot으로
  충분" 문구만 이 원칙으로 교체했다.
- **이상 판정 기준(`num_candidates`, top-g): BGL도 HDFS와 같은 9로 결정.**
  `model/train.py` 기본값 그대로라 학습 시 별도 옵션이 필요 없다. 이 값은
  학습에는 쓰이지 않고 `config.json`에만 기록되며, 서빙 시 `torch.topk`의 k로
  쓰여 **응답의 `top_candidates`/`probabilities` 배열 길이를 결정한다.** 즉
  응답 크기와 직렬화 비용, 따라서 지연시간·처리량에 직접 영향을 준다.
  - 검토한 대안 1 — **vocab 대비 비율을 HDFS와 맞춤**(9/29 ≈ 31% → BGL 약 307):
    기각. 응답 JSON이 약 250바이트에서 약 6.1KB로 24배가량 커져, 데이터셋 간
    성능 차이에 "모델이 무거워진 효과"와 "응답이 커진 효과"가 섞인다.
  - 검토한 대안 2 — **BGL 검증셋에서 정상 데이터 오탐률이 HDFS와 비슷해지는 g를
    데이터로 선택**: 탐지 관점에서는 더 공정하지만 g가 달라져 대안 1과 같은
    교란이 생기고, 모델 성능 튜닝은 연구 범위 밖이다. 기각.
  - 채택안 — **두 데이터셋 모두 g=9**: 응답 스키마와 크기가 동일해져 데이터셋
    간에 바뀌는 변수가 모델 크기(입력 폭) 하나로 고정된다. 원 논문 기본값이기도
    하다.
  - **대가와 처리**: BGL은 vocab이 991개라 상위 9개 안에 실제 다음 키가 들어올
    확률이 HDFS보다 낮아, 정상 시퀀스도 이상으로 판정되는 비율이 높을 수 있다.
    이 연구는 탐지 성능을 비교하지 않으므로 `is_anomaly` 기반 탐지 수치는 논문의
    비교 결과로 쓰지 않고, 필요하면 한계로 명시한다.
  - 정정: 앞서 이 항목을 "지연시간·처리량에는 영향이 없다"고 적었으나, 응답
    크기를 결정하므로 영향이 있다. 코드 확인 후 바로잡았다.
- **측정 조건은 HDFS 라운드와 동일**(프레임워크 3종, concurrency sweep, 단계별
  측정 시간, 5사이클) — `run_benchmark.sh` 기본값 그대로 실행한다.
- **Kafka는 시스템 아키텍처로 포함하고, 성능 비교 측정 경로에서는 제외**한다 —
  07-31 결정(§4.2)을 BGL 라운드에도 그대로 적용.
- **범위 밖(별도 확장 챕터 또는 향후 연구)**: K8s 확장, CI/CD 구축, Kafka 경로
  end-to-end 지연 측정. (CI/CD는 2026-11-02에 병행 항목으로 변경 — 아래 11-02.)

### 2026-10-31 — BGL 학습 시작, 서빙 스택의 "모델 하나" 전제 발견·수정

**BGL 학습**: `python model/train.py --dataset bgl`, 하이퍼파라미터·g 모두
기본값(= HDFS와 동일). 본 학습 전에 몇 배치로 소요 시간을 측정해 실행 위치를
정했다.

| 항목 | 로컬 CPU | 로컬 GPU (MX450) |
|---|---|---|
| 배치당 (2048) | 약 621ms | 약 259ms |
| 30에폭 추정 (학습만) | 약 9.8시간 | 약 4.1시간 |

HDFS 때는 모델이 작아 로컬 GPU가 오히려 CPU보다 느렸지만(§3), BGL은 one-hot
입력 폭이 34배라 GPU가 2.4배 빨라 로컬 GPU를 택했다. 실제 1에폭은 검증 포함
약 851초(val_loss 0.2192)로, 30에폭 약 7시간이 예상된다. 학습 로그는
`model/checkpoints/bgl/train.log`(체크포인트 디렉토리라 저장소에는 없음).
학습 위치(로컬)는 서빙 성능 측정과 무관하다 — 공식 벤치마크는 그대로 EC2에서만
한다.

**발견한 문제 — 서빙 스택이 데이터셋을 하나만 담는 구조였다**: 학습을 기다리며
BGL 서빙 준비를 점검하다 발견. 세 프레임워크가 모두 "모델은 하나"를 전제했다.
- FastAPI: `docker-compose.yml`에 `DATASET=hdfs`, `CKPT_DIR=.../hdfs` 하드코딩.
- BentoML: 모델 이름이 항상 `deeplog`이고 `latest` 태그를 띄움 — 마지막에
  등록한 데이터셋 모델이 서빙됨.
- Triton: `setup_model.py`가 항상 `serving/triton/models/deeplog/`에 씀 —
  나중에 만든 데이터셋이 앞의 것을 덮어씀.
- `run_benchmark.sh bgl`은 `benchmark.py`의 요청 payload만 BGL로 바꾸고 컨테이너
  쪽 모델은 바꾸지 않았다.

**영향(수정하지 않았다면)**: `run_benchmark.sh bgl`을 그대로 돌리면 HDFS 모델에
BGL 입력(키 값이 vocab 29를 넘음)이 들어가 에러가 나거나 엉뚱한 값이 측정됐을
것이다. 더 위험한 건 EC2 경로다 — `bootstrap_ec2.sh`는 `DATASETS="hdfs bgl"`을
차례로 돌며 Triton 레포 생성과 BentoML 등록을 하므로, BGL이 S3에 올라간 뒤
부트스트랩하면 **Triton·BentoML은 BGL, FastAPI는 HDFS를 서빙**하는 상태가 된다.
이 상태로 측정하면 프레임워크마다 다른 모델을 비교하게 되고, 에러 없이 조용히
진행될 수 있었다. HDFS 라운드에는 BGL이 S3에 없어 부트스트랩이 BGL을 건너뛰었기
때문에 영향이 없었다.

**수정**:
- **데이터셋 선택을 `DEEPLOG_DATASET` 하나로 통일** — `docker-compose.yml`에서
  FastAPI 체크포인트 경로, BentoML 모델 이름, Triton 레포 마운트가 모두 이 값을
  따른다(기본 `hdfs`). 변수 이름을 `DATASET`으로 하지 않은 이유: `run_benchmark.sh`
  안에서 `DATASET`이 `all`일 수 있고 `.env`의 학습용 `DATASET`과도 겹치기 때문.
- **저장 위치를 데이터셋별로 분리** — BentoML은 `deeplog-<dataset>` 이름으로
  등록(`save_model.py` 기본값), Triton은 `serving/triton/models/<dataset>/deeplog/`
  에 생성(`setup_model.py` 기본값). Triton 서버에는 한 번에 한 데이터셋 레포만
  마운트되므로, 모델 두 개가 동시에 GPU에 올라가 측정 조건이 HDFS 라운드와
  달라지는 일도 없다.
- **측정 직전 서빙 데이터셋 검증** — `run_benchmark.sh`가 데이터셋마다
  `DEEPLOG_DATASET`을 export하고, 헬스체크 통과 후 각 서버 `/health`의 `dataset`
  필드(세 프레임워크 공통)를 읽어 기대값과 다르면 **측정 없이 즉시 중단**한다.
  기존 헬스체크 실패는 해당 프레임워크만 건너뛰지만, 이 불일치는 설정 오류라 모든
  사이클에 영향을 주므로 전체를 멈춘다. 확인된 값은 `run_manifest_*.txt`에
  `served_dataset=`으로 남는다. 가짜 서버로 일치·불일치·무응답 세 경우를
  확인했다.
- 부수 수정: `.env.example`, BentoML `Dockerfile`·`service.py` 기본 모델 이름,
  `bootstrap_ec2.sh` 안내 문구, CLAUDE.md 실행 방법.
- 로컬의 기존 HDFS Triton 레포를 `serving/triton/models/hdfs/`로 옮겼다(저장소
  밖 산출물, 재생성 가능).

**HDFS 결과에 미치는 영향 없음**: 모델 가중치·전처리·응답 스키마·측정 조건은
그대로이고, 바뀐 건 모델을 찾는 경로와 이름뿐이다. HDFS를 재측정하지 않는다는
10-13 결정도 그대로 유지한다.

**아직 확인하지 못한 것**: 서빙 설정을 바꿨지만 CLAUDE.md 검증 절차대로 세
프레임워크를 실제로 띄워 보지는 못했다. 로컬 GPU 메모리가 2GB라 학습 중 서빙
컨테이너를 올리면 학습이 중단될 수 있어서다. 학습이 끝난 뒤 로컬 검증 단계에서
HDFS·BGL 둘 다 확인한다. 이때 로컬 BentoML 저장소(docker 볼륨 `bentoml_home`)의
HDFS 모델은 옛 이름(`deeplog`)이라 `save_model.py --dataset hdfs`로 다시 등록해야
한다. EC2는 부트스트랩이 새 이름으로 등록하므로 따로 할 일이 없다.

**학습 중단과 이어 학습 기능 추가 (같은 날 오후)**: 노트북을 7시간 연속으로 켜 둘
수 없는 환경이라 3에폭(val_loss 0.1591)에서 학습을 중단했다. 당시 스크립트는
가중치만 저장하고 옵티마이저 상태·진행 에폭·셔플 난수를 저장하지 않아 이어서
학습할 수 없었으므로 처음부터 다시 시작했다. 3에폭 가중치와 로그는
`model/checkpoints/_bgl_partial_epoch3/`에 따로 보관했다(학습 결과로 쓰지 않음).

`model/train.py`에 `--resume`을 추가했다.
- 매 에폭 끝에 `train_state.pt`(가중치·옵티마이저·셔플 난수 상태·best 기록·설정
  서명)를 저장하고, `--resume`으로 다시 실행하면 다음 에폭부터 이어 간다. 중단 시
  잃는 건 진행 중이던 에폭 하나다.
- **학습 도중에는 정식 산출물(`deeplog_best.pt`·`deeplog.ts`·`config.json`)을 만들지
  않는다.** 도중의 최적 가중치는 `best_in_progress.pt`에 두고, 마지막 에폭이 끝난
  뒤에만 정식 이름으로 복사·변환한다. 기존 방식은 학습 도중에도 `deeplog_best.pt`를
  썼기 때문에, 중단된 폴더를 `bootstrap_ec2.sh`나 `sync_to_s3.sh`가 정식
  체크포인트로 착각할 수 있었다.
- 진행 상태가 있는데 `--resume` 없이 실행하면 덮어쓰지 않고 거부한다. 저장된
  설정(하이퍼파라미터·데이터 크기)과 현재 설정이 다르면 차이를 출력하고 재개를
  거부한다.
- 저장은 임시 파일에 쓴 뒤 교체하는 방식이라, 저장 도중 전원이 꺼져도 기존 파일이
  깨지지 않는다.

**검증 중 발견한 재현성 구멍 — 초기 가중치 시드 없음**: 기존 학습은 데이터 분할과
셔플에만 시드가 있고 모델 초기 가중치에는 없었다. 같은 설정으로 두 번 돌리면 첫
에폭 손실부터 달랐다. 이어 학습의 결과가 끊김 없는 학습과 같은지 검증하려면
출발점이 같아야 하므로 모델 생성 직전에 `torch.manual_seed(42)`를 추가했다.
구조·하이퍼파라미터는 그대로라 "모델 고정" 원칙과 충돌하지 않는다. HDFS
체크포인트는 이 수정 전에 학습됐고 재학습하지 않으므로 HDFS 결과에는 영향이 없다.

**이어 학습 검증** (HDFS 앞 15만 시퀀스, 4에폭, CPU):

| 시나리오 | 결과 |
|---|---|
| 2에폭 후 강제 종료한 폴더 | `train_state.pt`·`best_in_progress.pt`만 존재, 정식 산출물 없음 |
| 진행 상태가 있는데 `--resume` 없이 실행 | 거부 |
| 학습률을 바꿔 `--resume` | 설정 차이를 출력하고 거부 |
| 끊었다 이어 간 학습 vs 한 번에 끝까지 돌린 학습 | 에폭별 손실 동일, 최종 가중치 비트 단위 동일 |

CPU 기준 비트 단위 동일을 확인했다. GPU에서는 cuDNN LSTM 연산 자체가 실행마다
미세하게 달라질 수 있어(끊김 없는 학습끼리도 마찬가지) 비트 단위 동일까지는
보장되지 않는다. 데이터 순서와 학습 상태는 같게 복원된다.

이후 BGL 학습을 이 방식으로 처음부터 다시 시작했다. 노트북을 끌 때는 그냥 끄고,
다시 켜면 `python model/train.py --dataset bgl --resume`으로 이어 간다.

### 2026-11-02 — CI/CD를 범위 밖에서 병행 항목으로 변경, 자동 검사 구축

**결정**: 10-13에 범위 밖으로 둔 CI/CD를 BGL 라운드와 병행하는 항목으로 올렸다.
실험 변수(모델·데이터·측정 조건)를 건드리지 않는 저장소 품질 작업이라 본 실험과
충돌하지 않는다. 순서는 ① 자동 검사(CI) → ② 서빙 이미지 레지스트리 푸시(CD).
K8s와 Kafka E2E 측정은 그대로 범위 밖이다.

**지키는 선** (CLAUDE.md 현재 스코프에도 기록):
- CI는 벤치마크를 돌리지 않고 성능 수치를 만들지 않는다. GitHub 러너에는 GPU가
  없고, 공식 측정은 EC2에서만 한다. CI 산출물은 `experiments/results/`에 쓰지 않는다.
- 실제 데이터·체크포인트는 저장소에 없으므로 소형 합성 데이터로 학습한 모델을 쓴다.
- CI 편의를 위해 서빙 코드 기본값을 바꾸지 않는다. CPU 러너에서 Triton을 띄우는
  설정은 옵션으로만 준다.

**구성** (`.github/workflows/ci.yml`, 푸시·PR마다 실행):

| 작업 | 내용 |
|---|---|
| static | 워크플로 검사(actionlint), 전체 파이썬 문법(`py_compile`), 셸 문법(`bash -n`), `ci/check_compose.py` — `DEEPLOG_DATASET` 미지정/hdfs/bgl 각각에서 세 프레임워크가 같은 데이터셋 모델을 가리키는지 |
| smoke | `ci/smoke.sh` — 합성 데이터 생성(`ci/make_fixture.py`) → 2에폭 CPU 학습 → 이어 학습 안전장치 3종 → 서빙 패키징 → FastAPI·BentoML·Triton(+래퍼) 기동 → `ci/check_serving.py`로 응답 대조 |
| images | `docker compose build fastapi bentoml triton-wrapper` (PR·푸시 모두, 레지스트리에는 올리지 않음) |
| publish (CD) | main 푸시이고 위 세 작업이 모두 통과했을 때만, 서빙 이미지 3종을 빌드해 ghcr.io에 푸시 |

`ci/check_serving.py`는 CLAUDE.md "검증"의 수동 절차를 자동화한 것이다. `/health`의
dataset·window_size·num_candidates 일치, 테스트 시퀀스 50개에 대한 `/predict`
응답 키·타입 일치, 확률 오차 1e-4 이내, 같은 순위 후보 일치(동률일 때만 순서
차이 허용), `is_anomaly` 일치를 본다. `latency_ms`는 키·타입만 보고 값은 비교하지
않는다.

**코드 변경** (실험 기본값 불변):
- `serving/triton/setup_model.py`: `--instance-kind {KIND_GPU,KIND_CPU}` 추가(기본
  KIND_GPU). 기본값으로 생성한 `config.pbtxt`가 기존 HDFS 설정과 동일함을 확인했다.
  `--model-repo` 기본값은 `serving/triton/models/<dataset>`(10-31 데이터셋 분리와 일치).

**검사 자체의 약점 발견·수정**: 처음 설정(학습 기본 배치 2048)에서는 합성 데이터
2만 개로 에폭당 9스텝뿐이라 모델이 거의 학습되지 않았다(1순위 후보 평균 확률
0.037). 후보 확률이 모두 비슷하면 동률 허용 때문에 순서 비교가 사실상 아무것도
검사하지 못한다. 스모크 학습만 `--batch-size 128`로 바꿔 0.467로 올렸고, 평균이
0.3 미만이면 검사 자체를 실패시키는 장치를 넣었다. 이 배치 크기는 CI 전용이며
실제 학습 기본값과 무관하다.

**로컬 검증** (Windows, CPU):

| 항목 | 결과 |
|---|---|
| static 3종 | 통과 |
| FastAPI `DATASET` 하드코딩(10-31 버그)을 되살렸을 때 | `check_compose.py`가 실패로 잡음 |
| 합성 학습 + 이어 학습 안전장치 3종 | 통과 |
| FastAPI·BentoML 응답 50건 대조 | 일치 (1순위 평균 확률 0.467) |
| Triton 포함 대조, images 작업 | 로컬에서는 미확인(Docker 미기동) — 아래 GitHub 실행으로 확인 |

**GitHub 첫 실행 — 워크플로 자체가 무효였다**: 첫 푸시(`c2174f5`)의 실행은 작업이
하나도 생성되지 않은 채 실패했다. smoke 작업 수준 `env`에서 `${{ runner.temp }}`를
썼는데, 이 위치에서는 `runner` 컨텍스트가 허용되지 않아 GitHub이 워크플로 전체를
무효로 처리했다. YAML 문법은 정상이라 YAML 파서로는 잡히지 않았고, 워크플로 전용
검사기 actionlint로 원인을 확인했다. 수정:
- `CI_ROOT`를 단계 수준 `env`로 옮겼다(단계 수준에서는 `runner` 허용).
- 셸 문법 검사 루프가 마지막 파일의 실패만 반영하던 버그를 고쳤다.
- static 작업에 actionlint를 추가했다(셸 스타일 검사인 shellcheck 연동은 끔). 단,
  워크플로가 무효면 이 단계도 돌지 못하므로 `ci.yml`을 고칠 때는 로컬에서도
  actionlint를 돌린다.

**GitHub 두 번째 실행** (`6065f0d`, 약 7분 41초): static·smoke·images 모두 통과.

| 항목 | 결과 |
|---|---|
| Triton 서버 (KIND_CPU) | 컨테이너 기동 후 2초 만에 ready |
| `/health` | 세 프레임워크 모두 dataset=hdfs, window_size=10, num_candidates=9 |
| `/predict` 50건 | 응답 키·타입 세 프레임워크 공통, 후보·확률·`is_anomaly` 일치 (1순위 평균 확률 0.467) |
| 서빙 이미지 3종 빌드 | 통과 |

**덤으로 확인된 재현성 — OS가 달라도 CPU 학습 손실이 같다**: 같은 합성 데이터·같은
설정에서 GitHub 러너(Linux)와 로컬(Windows)의 에폭별 손실이 소수점 4자리까지
일치했다(1에폭 train 2.8340 / val 2.1530, 2에폭 1.7463 / 1.4558). 10-31에 추가한
초기 가중치 시드(`torch.manual_seed(42)`)의 효과다. 단 CPU 기준이며, GPU(cuDNN
LSTM)는 10-31 기록대로 비트 단위 동일이 보장되지 않는다.

**관찰 — `/health` 키는 프레임워크마다 다르다**: Triton 래퍼의 `/health`에는
`device`가 없고 `triton_live`·`model_ready`가 더 있다. 기존부터 그랬고, 벤치마크
CSV를 결정하는 `/predict` 응답은 같으므로 측정에는 영향이 없다. 검사는 공통
필드(dataset·window_size·num_candidates)만 비교한다.

**실행 경고 정리**: 실행 결과에 두 종류의 경고가 붙었다 — ① `actions/checkout@v4`·
`actions/setup-python@v5`가 지원 종료된 Node.js 20 대상, ② `ubuntu-latest`가 Ubuntu
26으로 이동 중(2026-10-19부터). 둘 다 코드 변경 없이 CI를 깨뜨릴 수 있어
`checkout@v5`·`setup-python@v6`(Node 24 대상 중 가장 낮은 메이저 — 동작 변화 최소)로
올리고 러너를 `ubuntu-24.04`로 고정했다. 다음 실행(`1572a2f`)에서 세 작업 모두
`ubuntu-24.04`로 통과했고, static 작업의 경고는 0건이 됐다.

**CD — 서빙 이미지 레지스트리 푸시**: 레지스트리는 GitHub Container Registry
(ghcr.io)로 정했다. 요금 비교(2026-11-02 각 공식 요금 페이지 기준):

| | ghcr.io | AWS ECR (비공개) |
|---|---|---|
| 공개 이미지 | 무료 | (ECR Public을 따로 쓰면 50GB 무료) |
| 비공개 이미지 | 저장·전송 "현재 무료"(변경 시 최소 1개월 전 공지) | GB당 월 $0.10, 신규 계정만 1년간 500MB 무료 |
| EC2로 받기 | 무료 | 같은 리전이면 무료 |
| 인증 | 워크플로 자동 토큰(`GITHUB_TOKEN`) — 별도 비밀값 없음 | AWS 키를 GitHub 시크릿으로 관리해야 함 |

FastAPI·BentoML 이미지는 CUDA 빌드 torch 때문에 각각 수 GB라 ECR 무료 한도를
바로 넘는다. ghcr.io는 이미지를 공개로 두면 요금 정책과 무관하게 무료다. 저장소가
공개이고, 이미지에는 코드와 의존성만 들어가므로(Dockerfile이 파일을 하나씩 COPY,
가중치·데이터·`.env` 없음) 공개해도 문제없다.

구성 (`ci.yml`의 `publish` 작업):
- 조건: main 푸시이고 static·smoke·images가 모두 통과했을 때만. PR에서는 돌지 않는다.
- 이미지: `ghcr.io/sungsuhan/deeplog-fastapi`, `deeplog-bentoml`,
  `deeplog-triton-wrapper`. 태그는 커밋 SHA(어떤 코드로 만든 이미지인지 추적)와
  `latest`. 이미지에 저장소 주소·커밋 레이블을 붙여 저장소의 Packages에 연결된다.

결과(`92c9652`): 네 작업 모두 통과(publish 약 11분). 로그인 없이 ghcr.io를 조회해 세
이미지 모두 SHA·`latest` 태그로 응답함을 확인했다 — 공개 저장소에 연결돼 공개
상태로 만들어졌다.

**바꾸지 않은 것**: EC2와 docker-compose는 지금처럼 Dockerfile로 직접 빌드한다.
EC2에서 이 이미지를 받아 쓰도록 바꾸는 것은 측정 절차 변경이라 별도로 결정한다.

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
- **실험 환경**: AWS EC2 g4dn.xlarge (T4 GPU 16GB), 온디맨드로 실행(§6) —
  벤치마크 완료, 결과는 §8.

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
  반면 **벤치마크 결과(`experiments/results/`)와 그림(`experiments/figures/`)은
  2026-09-12부터 저장소에 포함**한다(전체 약 740KB) — 논문 수치의 원본이라
  용량보다 추적성이 우선이기 때문.

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
  - 재현 시드: 데이터 train/val 분할과 에폭별 셔플 모두 `manual_seed(42)`.
    **단, HDFS 학습 당시에는 모델 초기 가중치에 시드가 없었다** — 같은 설정으로
    다시 돌려도 가중치가 똑같이 나오지 않는다. 2026-10-31에 초기화 시드를
    추가했으며(BGL부터 적용), HDFS는 재학습하지 않으므로 이미 측정에 쓴
    체크포인트가 그대로 기준이다(§스코프 이력 2026-10-31).
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
| 데이터셋 선택 (2026-10-31~) | `model/checkpoints/<dataset>/` | 모델 이름 `deeplog-<dataset>` | 레포 `serving/triton/models/<dataset>/` 마운트 — 셋 다 `DEEPLOG_DATASET` 하나로 결정 |
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
  돼 있지만, 성능 비교 수치에는 포함하지 않는다(기말 라운드에 이어 BGL
  라운드에도 동일 적용 — 2026-10-13).

**시스템 아키텍처 (발표 다이어그램용 텍스트 설명)**:

```
[HDFS/BGL 로그 시퀀스] → [Kafka Producer] → [Kafka Topic] → [Kafka Consumer]
        → [서빙 프레임워크 POST /predict: FastAPI | BentoML | Triton]
        → [응답 (top_candidates, is_anomaly, latency_ms)]
```

- `kafka/producer.py`가 전처리된 로그 시퀀스를 스트리밍으로 Kafka 토픽에
  흘려보내고, `kafka/consumer.py`가 이를 소비해 서빙 프레임워크의
  `POST /predict`를 호출한 뒤 이상 판정 응답을 받는다 — 이 경로 전체가
  "실시간 로그 이상탐지" 연구 주제를 아키텍처로 뒷받침한다.
- **다만 프레임워크 성능 비교 수치(HDFS·BGL 모두)는 이 Kafka 경로를
  거치지 않는다.** `experiments/benchmark.py`가 서빙 프레임워크의
  `POST /predict`를 concurrency sweep으로 **직접** 호출해 지연시간·처리량을
  측정한다 — 위 다이어그램에서 [서빙 프레임워크] 박스 이후만 측정 대상이고,
  Kafka 구간(Producer→Topic→Consumer)은 측정에 포함되지 않는다.
- 발표 자료 작성 시 주의: Kafka는 "이 시스템이 실시간 스트리밍 워크로드에서
  동작한다"는 아키텍처적 증거로 포함하되, "프레임워크 A가 B보다 빠르다"는
  수치적 주장은 Kafka를 거치지 않은 concurrency sweep 결과로만 뒷받침해야
  한다 — 이 둘을 같은 그래프·같은 수치로 섞지 않는 것이 방법론적으로 중요함.

### 4.3 해결됨 (2026-08-06) — Triton 래퍼의 스레드 경쟁 버그 (gevent × threadpool)

**증상**: `serving/triton/app.py`의 `/health`가 간헐적으로 500을 반환,
로그에 `greenlet.error: Cannot switch to a different thread`.

**원인**: `tritonclient.http`(동기, gevent/geventhttpclient 기반)를
FastAPI의 `def`(동기) 엔드포인트에서 호출하고 있었다. FastAPI/Starlette는
동기 엔드포인트를 anyio 스레드풀에 분산 디스패치하는데, gevent의
greenlet은 자신이 생성된 OS 스레드에 고정되어 있어 **다른 스레드에서
재개하려 하면 즉시 예외**를 던진다. `/predict`도 같은 클라이언트를
쓰지만 매번 크래시하지는 않아 로컬 1회성 검증(§4)에서는 발견되지 않았고
— concurrency가 낮을 때(4~8)만, 스레드풀이 실제로 여러 스레드를 오갈 때만
간헐적으로 재현됐다(§스코프 이력 2026-08-06 참고, 8/5 원본 데이터의
cycle2·3에도 이미 있었음).

**수정**: `tritonclient.http.aio`(asyncio/aiohttp 기반 비동기 클라이언트)로
교체. `lifespan`·`health()`·`_infer()`·`predict()`를 전부 `async def`로
바꾸고 client 호출에 `await`를 붙였다 — `InferInput`/`InferRequestedOutput`은
순수 데이터 구조라 동기 모듈(`tritonclient.http`) 것을 그대로 재사용
가능(aio 클라이언트의 `infer()`가 동일 타입을 받음, 실측 확인됨). 요청/응답
JSON 스키마는 변경 없음.

**부수 효과**: 스레드 직렬화가 사라지며 동시성 처리 능력 자체가
개선됐다 — concurrency=4 기준 처리량이 300 rps → 620 rps로 약 2배
증가(§8). 이건 버그 수정의 부산물이지 별도 최적화가 아니다.

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
- **반복 측정**: **5사이클** (통계 검정력 확보를 위해 3→5로 확장,
  §스코프 결정 이력 2026-08-06 참고. `run_benchmark.sh`의 `CYCLES` 기본값도
  2026-09-12에 5로 맞춰, 옵션 없이 실행해도 n=5가 나온다). `run_benchmark.sh`가 사이클마다
  `shuf`로 프레임워크 실행 순서를 랜덤화하고, 결과를
  `experiments/results/cycle{N}/`에 분리 저장 + `run_manifest_*.txt`에
  실제 사용된 순서를 기록. `CYCLE_START` 환경변수(신규)로 기존 사이클을
  덮어쓰지 않고 이어붙일 수 있음(`CYCLE_START=4 CYCLES=5 bash
  run_benchmark.sh hdfs`처럼). **단, Triton은 cycle1~3에 래퍼 버그가
  있어(§4.3) cycle4~8 중 5개(4,5,6,7,8)가 최종 유효 표본** — fastapi·
  bentoml은 cycle1~5 그대로 유효. `experiments/results/
  _archived_buggy_triton/`에 폐기된 triton cycle2·3 데이터를 사유와 함께
  보관해둠.
- **컨테이너 생명주기**: 프레임워크 1개씩 `docker compose up` → 헬스체크
  대기(Triton은 서버 ready 확인 후 wrapper 기동) → 벤치마크 → `stop`+`rm`
  → 다음 프레임워크. 세 프레임워크를 동시에 띄우지 않음(CLAUDE.md 규칙).
- **서빙 데이터셋 검증**(2026-10-31 추가): 데이터셋마다 `DEEPLOG_DATASET`을
  export해 세 프레임워크가 같은 데이터셋 모델을 띄우게 하고, 헬스체크 직후
  `/health`의 `dataset`이 기대값과 다르면 측정 없이 전체를 중단한다. 확인된 값은
  manifest에 `served_dataset=`으로 기록(§스코프 이력 2026-10-31).
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
  - `plot_results.py` — `results/cycle*/`를 전부 읽어 사이클 평균±표준편차로
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
  작성)로 구현됨. 사이클(기본 5회)을 검정의 표본 단위로 삼는다 —
  benchmark.py CSV엔 개별 요청의 원본 지연시간이 없고 concurrency 레벨별
  요약 통계만 있기 때문. n=3이던 원래 3사이클로는 Mann-Whitney U(양측)가
  도달 가능한 최소 p-value가 약 0.1로 제한돼 27건 전부 유의하지 않게
  나왔는데, n=5로 늘린 뒤(§스코프 이력 2026-08-06)에는 최소 p-value가
  약 0.008까지 낮아져 **27건 중 25건이 `p<0.05`**에 도달했다(§8). 스크립트가
  이 구조적 한계를 결과와 함께 출력해 오독을 방지한다.
- **핫패스에 pandas 미사용** — `kafka/*.py`, `serving/*/app.py`, `service.py`,
  `experiments/benchmark.py` 확인됨(2026-07-30 재확인). pandas는
  `data/preprocess.py`·`experiments/plot_results.py`에서만 사용.

---

## 6. EC2 인프라 — **사용 완료, 인스턴스 3대 모두 terminate됨**

### 6.1 자동화 스크립트 (`experiments/aws/`)

| 스크립트 | 역할 |
|---|---|
| `setup_instance.sh` | user-data — NVIDIA 드라이버·CUDA·Docker+compose 플러그인·nvidia-container-toolkit·git·AWS CLI v2·python3-venv 설치 |
| `launch_spot_instance.sh` | g4dn.xlarge 스팟 인스턴스 요청 |
| `launch_ondemand_instance.sh` (2026-08-04 신규) | 스팟 용량 부족/인터럽션 시 대안 — 즉시 기동, 요금 확정형 |
| `sync_to_s3.sh` | (로컬 실행) `data/processed/`·`model/checkpoints/` → S3 업로드 |
| `bootstrap_ec2.sh` | (EC2에서 실행) git clone + S3 다운로드 + Triton 레포 생성 + Docker 이미지 빌드 + BentoML 모델 등록 |
| `spot_backup_watcher.sh` | (EC2, 백그라운드) 인터럽션 감지 시 즉시 백업 + 5분마다 정기 백업 → S3. **레포 밖(`/tmp` 등)으로 scp해서 실행해도 정상 동작**한다 — 프로젝트 루트 후보를 검증하며 탐색하고, 못 찾으면 조용히 넘어가지 않고 즉시 종료(2026-09-12 수정. 이전에는 경로가 `/`로 계산돼 백업이 조용히 누락됐음 — §스코프 이력 08-05의 11번). `DEEPLOG_PROJECT_ROOT`로 루트를 직접 지정할 수도 있다 |
| `one_shot_launch.sh` | 스팟/온디맨드 전체 오케스트레이터(`EC2_LAUNCH_MODE=spot\|ondemand`). AWS 자격증명 자동 복사 단계 포함(2026-08-05 추가). `run_benchmark.sh`는 의도적으로 자동 실행 안 함 |
| `update_sg_ip.sh` | 보안그룹 인바운드를 실행 시점의 현재 공인 IP로 갱신 |

### 6.2 사용한 AWS 리소스 (ap-northeast-2)

| 리소스 | ID/이름 |
|---|---|
| 서브넷 | `subnet-0f9c2552a0f61660c` (ap-northeast-2a) |
| 보안그룹 | `sg-063774d1ab4874831` (`deeplog-serving-sg`) — 22/8000/3000/8001/8002/9092, `update_sg_ip.sh`로 인바운드 IP 갱신 |
| 키페어 | `deeplog-ec2-key` |
| S3 버킷 | `deeplog-serving-2025720449` — 데이터/체크포인트 업로드용. 8/5~8/6 당시엔 백업 워처 버그로 벤치마크 결과가 여기 안 올라가 로컬 scp로 직접 회수했음(그 버그는 2026-09-12 수정됨 — §6.1) |
| 벤치마크 인스턴스 (1차, 8/5) | `i-0ab588b123116424e` (온디맨드, g4dn.xlarge, T4) — HDFS 3사이클. **terminate 완료** |
| 벤치마크 인스턴스 (2차, 8/6) | `i-0c20e3ae6e8b2214e` (온디맨드) — cycle4·5 추가 + Triton 버그 발견·수정(§4.3). **terminate 완료** |
| 벤치마크 인스턴스 (3차, 8/6) | `i-0049b56fde150edea` (온디맨드) — Triton만 cycle6·7·8 재측정(수정판 코드). **terminate 완료**, 더 이상 과금 없음 |

### 6.3 재실행 방법 (BGL 등 후속 라운드용)

```
bash experiments/aws/update_sg_ip.sh   # 네트워크 바뀌었으면
EC2_LAUNCH_MODE=ondemand bash experiments/aws/one_shot_launch.sh
# 완료 후 SSH 접속해 bash experiments/run_benchmark.sh <dataset>
```
스팟이 다시 값이 괜찮으면 `EC2_LAUNCH_MODE=spot`(기본값)로 비용을 아낄 수
있지만, 인터럽션 위험을 감수해야 한다(§스코프 이력 08-04~05 참고).

---

## 8. 벤치마크 결과 (HDFS, EC2 g4dn.xlarge T4, 2026-08-05~06)

**측정 조건**: concurrency sweep 1/4/8/16/32/64, 단계별 30초 + 워밍업 5초.
FastAPI·BentoML은 cycle1~5(n=5, 8/5 측정), **Triton은 cycle4~8(n=5, 전부
2026-08-06 버그 수정판 코드로 측정 — cycle1~3의 triton은 래퍼 버그로
폐기, §4.3·§스코프 이력 참고)**. 원본 CSV: `experiments/results/cycle{N}/`,
폐기분은 `experiments/results/_archived_buggy_triton/`. 그림:
`experiments/figures/fig1~6`. 통계 검정:
`experiments/results/stat_test_hdfs.csv`.

### 8.1 저/중/고 대표값 (사이클 평균, fig4 참고)

| 부하 | 지표 | FastAPI | BentoML | Triton |
|---|---|---|---|---|
| 저 (c=4)  | p50 (ms)     | 5.3    | 8.7    | 6.0    |
| 저 (c=4)  | throughput   | 655.1  | 430.7  | 601.7  |
| 중 (c=16) | p50 (ms)     | 29.0   | 36.2   | 33.3   |
| 중 (c=16) | throughput   | 418.4  | 388.8  | 367.3  |
| 고 (c=64) | p50 (ms)     | 141.0  | 185.1  | 159.3  |
| 고 (c=64) | p99 (ms)     | 1162.4 | 2299.0 | 1656.9 |
| 고 (c=64) | throughput   | 297.9  | 174.6  | 242.3  |

### 8.2 관찰된 패턴

- **FastAPI가 전 구간에서 처리량·지연시간 1위** — 프레임워크 오버헤드가
  없는 베이스라인이 저·중·고 부하 모두에서 가장 유리했다.
- **저부하(c=4)에서 Triton이 BentoML을 앞선다** (처리량 601.7 vs 430.7,
  p50 6.0ms vs 8.7ms) — **버그 수정 전에는 이 반대로 보였다**(Triton이
  세 프레임워크 중 최하위, 처리량 305.9)는 점이 중요하다. §4.3의 스레드
  경쟁 버그가 저·중 동시성 구간에서 일부 요청을 조용히 실패시켜 Triton의
  실제 성능을 과소평가하게 만들었었다 — 버그를 못 잡았다면 논문에 잘못된
  결론(Triton이 저부하에서 가장 느리다)이 실릴 뻔했다.
- **고부하(c=64)로 갈수록 BentoML이 급격히 무너진다** — p99가 2.3초까지
  치솟고 처리량은 세 프레임워크 중 최하(174.6). **Triton은 고부하에서
  BentoML을 확실히 앞지르며**(처리량 242.3 vs 174.6) "고성능 서빙"이라는
  포지셔닝과 부합하는 패턴을 보인다. 이 구간은 버그의 영향을 받지 않았던
  구간이라(§4.3) 버그 수정 전후 수치가 거의 동일함(244.4 → 242.3).
- **GPU 사용률**(fig6)은 세 프레임워크 모두 0이 아닌 값을 보인다 — §4.1
  GPU confound 수정이 실제 T4 환경에서도 유효했음을 데이터로 확인.
- **통계적 유의성**: n=5(양쪽)로 Mann-Whitney U가 도달 가능한 최소
  p-value가 약 0.008까지 낮아지면서, **27건 중 25건이 `p<0.05`**에
  도달했다. FastAPI-BentoML·FastAPI-Triton 쌍은 저/중/고 전 구간·전
  지표(p50/p99/throughput)에서 유의했고, BentoML-Triton 쌍만 9건 중
  2건(c=16 p99, c=64 p50)이 유의하지 않았다 — 이 둘이 성능상 가장 근접한
  쌍이라는 관찰과 일치한다. 상세 p-value는
  `experiments/results/stat_test_hdfs.csv` 참고.

### 8.3 사용 가능한 그림 목록

| 파일 | 내용 |
|---|---|
| `fig1_p50_latency.png` / `fig2_p99_latency.png` | concurrency sweep(1~64) 전체, 사이클 평균±표준편차 |
| `fig3_throughput.png` | 처리량 sweep |
| `fig4_summary_bar_hdfs.png` | 저/중/고 3단계 요약 비교 (위 8.1 표의 그래프 버전) |
| `fig5_cpu_usage.png` / `fig6_gpu_usage.png` | CPU/GPU 사용률(%) sweep |

모든 그림은 2026-08-06 최종(FastAPI/BentoML n=5, Triton n=5 클린 데이터)
기준으로 재생성됨.

---

## 9. 다음 단계 — BGL 라운드

HDFS 결과는 확정됐다(§8). BGL 라운드 스코프는 2026-10-13에 확정됐고, 학습은
2026-10-31에 시작했다(§스코프 이력 10-13, 10-31). 열린 항목은 서빙 스택 변경의
실제 기동 검증 하나다(4번에서 처리). 진행 순서:

1. ~~이상 판정 기준 결정~~ — 완료. HDFS와 같은 g=9(§스코프 이력 2026-10-13).
2. **학습** — ⏳ 진행 중. `python model/train.py --dataset bgl`, 하이퍼파라미터와
   g 모두 기본값(= HDFS와 동일). 로컬 GPU, 30에폭 약 7시간. 중단되면
   `python model/train.py --dataset bgl --resume`으로 이어 간다. 30에폭이 끝나
   `model/checkpoints/bgl/`에 `config.json`이 생기면 완료다.
3. **서빙 패키징** — `serving/bentoml/save_model.py`와
   `serving/triton/setup_model.py`를 `--dataset bgl`로 실행. 결과는 데이터셋별로
   분리 저장된다(BentoML `deeplog-bgl`, Triton `serving/triton/models/bgl/`).
4. **로컬 검증** — 10-31 서빙 변경의 기동 검증을 겸한다. 로컬 BentoML 저장소에
   HDFS를 `save_model.py --dataset hdfs`로 새 이름으로 다시 등록한 뒤,
   `DEEPLOG_DATASET=hdfs`와 `bgl` 각각으로 세 프레임워크를 띄워 CLAUDE.md 검증
   절차(`/health`의 `dataset` 값 확인 + 동일 입력 `/predict` 스키마·값 대조)를
   돌린다. 이어서 짧은 벤치마크 드라이런으로 서빙 데이터셋 검증 장치가 통과하는지
   본다. 드라이런 결과는 `experiments/results/`에 저장하지 않는다.
5. **EC2 공식 측정** — 데이터·체크포인트를 S3에 올리고 §6.3 절차로 기동,
   `bash experiments/run_benchmark.sh bgl`(옵션 없이 = HDFS와 같은 조건).
   `run_manifest_*.txt`에 프레임워크별 `served_dataset=bgl`이 찍혔는지 확인한다.
   스팟이면 `spot_backup_watcher.sh`도 함께 띄운다. 끝나면 결과 회수 후
   인스턴스 terminate.
6. **분석** — `plot_results.py`·`stat_test.py --dataset bgl`로 그림과
   Mann-Whitney U 검정 생성. 핵심 질문: HDFS의 프레임워크 순위가 더 무거운
   모델에서도 유지되는가, 특히 Triton이 FastAPI와의 격차를 줄이는가.
7. **문서화와 논문 작성** — 이 문서를 재생성하고, HDFS·BGL 결과를 함께 놓고
   방법론·결과·논의를 쓴다. README 실행 방법은 채워 두었으니 제출
   전에 실제 절차와 맞는지만 다시 본다.

**병행 — CI/CD** (§스코프 이력 2026-11-02, 위 순서와 독립):
- A. 자동 검사 — ✅ 완료. GitHub에서 static·smoke(Triton 포함)·images 통과.
- B. 서빙 이미지 레지스트리 푸시 — ✅ 완료. ghcr.io 공개 이미지 3종(SHA·`latest`
  태그).
- 미결정: EC2 측정에서 직접 빌드 대신 ghcr.io 이미지(SHA 태그)를 받아 쓸지.
