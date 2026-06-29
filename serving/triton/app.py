"""
Triton Inference Server 래퍼 — DeepLog 이상탐지.

이 스택의 역할 (논문 비교 관점):
  Triton은 NVIDIA가 만든 고성능 추론 서버.
  Python GIL 없이 C++ 런타임에서 TorchScript 모델을 직접 실행하며,
  dynamic batching(요청을 자동으로 묶어 GPU 효율을 높임)을 지원.
  → FastAPI·BentoML 대비 고부하 구간에서 처리량 우위를 검증하는 것이 목적.

아키텍처 (두 컨테이너):
  클라이언트 → [이 래퍼: port 8002] → [Triton 서버: port 8001]

래퍼가 필요한 이유:
  Triton의 네이티브 HTTP API는 입출력 포맷이 달라 벤치마크 스크립트가
  세 프레임워크를 동일한 방식으로 호출할 수 없다.
  래퍼가 전처리(one-hot)·후처리(top-g, 이상 판정)를 담당하고
  FastAPI·BentoML과 동일한 /predict 인터페이스를 노출한다.

환경변수:
  TRITON_URL   Triton HTTP 주소  (기본값: localhost:8001)
  MODEL_NAME   Triton 모델 이름  (기본값: deeplog)
  MODEL_REPO   triton/models/ 경로 (config.json 위치)
"""

import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import numpy as np
import tritonclient.http as httpclient
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

_PROJECT_ROOT = Path(__file__).parent.parent.parent


# ── 싱글톤 상태 ────────────────────────────────────────────────────────────────
class _AppState:
    client:     httpclient.InferenceServerClient  # Triton HTTP 클라이언트
    config:     dict                               # 모델 하이퍼파라미터
    model_name: str


_state = _AppState()


def _load_config() -> None:
    """setup_model.py가 생성한 config.json을 읽어 vocab 크기 등을 확인한다."""
    model_repo  = Path(os.getenv("MODEL_REPO", str(Path(__file__).parent / "models")))
    config_path = model_repo / "deeplog" / "config.json"

    if not config_path.exists():
        raise FileNotFoundError(
            f"config.json 없음: {config_path}\n"
            "  실행: python serving/triton/setup_model.py --dataset <hdfs|bgl>"
        )
    _state.config = json.loads(config_path.read_text())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 Triton 연결 확인, 종료 시 클라이언트 정리."""
    triton_url        = os.getenv("TRITON_URL",  "localhost:8001")
    _state.model_name = os.getenv("MODEL_NAME", "deeplog")

    _load_config()
    _state.client = httpclient.InferenceServerClient(url=triton_url, verbose=False)

    if not _state.client.is_server_live():
        raise RuntimeError(f"Triton 서버에 연결할 수 없습니다: {triton_url}")
    if not _state.client.is_model_ready(_state.model_name):
        raise RuntimeError(f"모델 '{_state.model_name}'이 Triton에 로드되지 않았습니다")

    print(
        f"[Triton 래퍼] 연결 완료  server={triton_url}  "
        f"model={_state.model_name}  window={_state.config['window_size']}"
    )
    yield
    _state.client.close()


app = FastAPI(title="DeepLog — Triton wrapper", version="1.0", lifespan=lifespan)


# ── 요청/응답 스키마 — FastAPI·BentoML과 동일 (공정한 비교 보장) ─────────────────
class PredictRequest(BaseModel):
    log_keys: list[int]
    next_key: Optional[int] = None

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


# ── 추론 ─────────────────────────────────────────────────────────────────────
def _infer(log_keys: list[int]) -> tuple[list[int], list[float]]:
    expected    = _state.config["window_size"] - 1
    num_classes = _state.config["num_classes"]
    g           = _state.config["num_candidates"]

    if len(log_keys) != expected:
        raise HTTPException(
            status_code=422,
            detail=f"log_keys 길이는 {expected}여야 합니다. 현재: {len(log_keys)}",
        )

    # one-hot 인코딩 (numpy): Triton은 numpy 배열로 데이터를 주고받는다
    x = np.zeros((1, expected, num_classes), dtype=np.float32)
    for i, key in enumerate(log_keys):
        x[0, i, key] = 1.0

    # Triton 입력 객체 생성 — 텐서 이름은 config.pbtxt의 input[0].name과 일치해야 함
    infer_input = httpclient.InferInput("input__0", x.shape, "FP32")
    infer_input.set_data_from_numpy(x)
    infer_output = httpclient.InferRequestedOutput("output__0")

    # Triton HTTP 호출 → C++ 런타임에서 TorchScript 모델 실행
    result = _state.client.infer(
        model_name = _state.model_name,
        inputs     = [infer_input],
        outputs    = [infer_output],
    )
    logits = result.as_numpy("output__0")[0]   # (num_classes,)

    # Softmax를 numpy로 계산 (Triton이 로짓만 반환하기 때문)
    # max를 빼는 것은 수치 안정성(overflow 방지)을 위한 표준 기법
    exp_logits = np.exp(logits - logits.max())
    probs      = exp_logits / exp_logits.sum()

    top_idx   = np.argsort(probs)[::-1][:g]
    top_probs = probs[top_idx]
    return top_idx.tolist(), top_probs.tolist()


# ── 엔드포인트 ────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status":         "ok",
        "dataset":        _state.config.get("dataset"),
        "triton_live":    _state.client.is_server_live(),
        "model_ready":    _state.client.is_model_ready(_state.model_name),
        "window_size":    _state.config.get("window_size"),
        "num_candidates": _state.config.get("num_candidates"),
    }


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    t0 = time.perf_counter()
    top_candidates, probabilities = _infer(req.log_keys)
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
