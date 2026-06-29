"""
BentoML v1.x 서빙 — DeepLog 이상탐지.

이 스택의 역할 (논문 비교 관점):
  BentoML은 ML 서빙 전용 프레임워크. FastAPI 대비 추가로 제공하는 것:
    - 모델 버전 관리 (model store: name:tag 형태)
    - API 스키마 자동 생성 및 문서화
    - 서비스 리소스 선언 (memory, cpu, gpu)
    - 트래픽 제어 (timeout, max_concurrency)
  → 운영 편의성이 높지만 프레임워크 자체 오버헤드가 있는지 측정하는 것이 목적.

실행 순서:
  1. python model/train.py --dataset hdfs
  2. python serving/bentoml/save_model.py --dataset hdfs  ← BentoML store 등록
  3. bentoml serve serving.bentoml.service:DeepLogService  (로컬)
     또는 docker compose up bentoml                        (컨테이너)

환경변수:
  BENTOML_MODEL_NAME   모델 이름 (기본값: deeplog)
  BENTOML_MODEL_TAG    버전 태그  (기본값: latest)
"""

import os
import sys
import time
from pathlib import Path
from typing import Optional

import bentoml
import torch
import torch.nn.functional as F
from pydantic import BaseModel, field_validator

_PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "model"))
from deeplog import DeepLog  # noqa: E402


# ── 요청/응답 스키마 — FastAPI와 동일하게 유지 (공정한 비교를 위해) ──────────────
class PredictRequest(BaseModel):
    log_keys: list[int]
    """입력 윈도우: 정수 로그 키 목록, 길이 == window_size - 1"""

    next_key: Optional[int] = None
    """실제 관측된 다음 로그 키. 제공하면 is_anomaly가 계산됨."""

    @field_validator("log_keys")
    @classmethod
    def _check_nonempty(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("log_keys는 비어있을 수 없습니다")
        return v


class PredictResponse(BaseModel):
    top_candidates: list[int]
    probabilities:  list[float]
    is_anomaly:     Optional[bool]
    latency_ms:     float


# ── BentoML 서비스 정의 ────────────────────────────────────────────────────────
@bentoml.service(
    name      = "deeplog-bentoml",
    resources = {"memory": "1Gi"},   # 컨테이너 리소스 상한 선언
    traffic   = {"timeout": 10},     # 10초 초과 시 자동 타임아웃
)
class DeepLogService:
    """
    BentoML 서비스 클래스.

    FastAPI와의 구조적 차이:
      - 모델을 파일 경로가 아닌 BentoML model store(로컬 레지스트리)에서 로드.
      - __init__이 워커 프로세스마다 1회 실행되어 모델을 메모리에 올림.
      - @bentoml.api 데코레이터가 라우팅·직렬화·문서화를 자동 처리.
    """

    def __init__(self) -> None:
        model_name = os.getenv("BENTOML_MODEL_NAME", "deeplog")
        model_tag  = os.getenv("BENTOML_MODEL_TAG",  "latest")

        # BentoML store에서 모델 참조 → 메타데이터(config)도 함께 로드
        bento_ref   = bentoml.models.get(f"{model_name}:{model_tag}")
        self.config = bento_ref.info.metadata  # train.py가 저장한 config.json 내용
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        # BentoML이 관리하는 저장소에서 PyTorch 모델 복원
        self.model: DeepLog = bentoml.pytorch.load_model(bento_ref)
        self.model.to(self.device).eval()

        print(
            f"[BentoML] 모델 로드 완료  tag={bento_ref.tag}  "
            f"device={self.device}  window={self.config['window_size']}"
        )

    @torch.no_grad()
    def _infer(self, log_keys: list[int]) -> tuple[list[int], list[float]]:
        expected    = self.config["window_size"] - 1
        num_classes = self.config["num_classes"]
        g           = self.config["num_candidates"]

        if len(log_keys) != expected:
            raise ValueError(
                f"log_keys 길이는 {expected}여야 합니다. 현재: {len(log_keys)}"
            )

        # 로그 키 → one-hot 텐서
        idx = torch.tensor(log_keys, dtype=torch.long, device=self.device)
        x   = torch.zeros(1, len(log_keys), num_classes, device=self.device)
        x[0].scatter_(1, idx.unsqueeze(1), 1.0)

        logits = self.model(x)
        probs  = F.softmax(logits, dim=-1)[0]

        top_probs, top_idx = torch.topk(probs, k=min(g, num_classes))
        return top_idx.cpu().tolist(), top_probs.cpu().tolist()

    @bentoml.api(route="/health", input_spec=None)
    def health(self) -> dict:
        return {
            "status":         "ok",
            "dataset":        self.config.get("dataset"),
            "device":         str(self.device),
            "window_size":    self.config.get("window_size"),
            "num_candidates": self.config.get("num_candidates"),
        }

    @bentoml.api(route="/predict")
    def predict(self, req: PredictRequest) -> PredictResponse:
        t0 = time.perf_counter()

        try:
            top_candidates, probabilities = self._infer(req.log_keys)
        except ValueError as exc:
            raise bentoml.exceptions.InvalidArgument(str(exc)) from exc

        latency_ms = (time.perf_counter() - t0) * 1000.0

        is_anomaly = (
            req.next_key not in top_candidates
            if req.next_key is not None
            else None
        )

        return PredictResponse(
            top_candidates = top_candidates,
            probabilities  = probabilities,
            is_anomaly     = is_anomaly,
            latency_ms     = latency_ms,
        )
