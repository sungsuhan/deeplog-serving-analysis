"""
FastAPI 서빙 — DeepLog 이상탐지 REST API.

이 스택의 역할 (논문 비교 관점):
  FastAPI는 프레임워크 비교의 '베이스라인'. 별도의 ML 서빙 기능 없이
  순수 Python/PyTorch로 추론하는 가장 단순한 구조.
  → 다른 두 프레임워크의 오버헤드/이점을 측정하는 기준점.

엔드포인트:
  GET  /health   서버 상태 확인
  POST /predict  로그 시퀀스 입력 → 이상 여부 반환

환경변수:
  DATASET   hdfs | bgl (기본값: hdfs)
  CKPT_DIR  체크포인트 디렉토리 경로 (기본값: model/checkpoints/{DATASET})
"""

import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import torch
import torch.nn.functional as F
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

_PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "model"))
from deeplog import DeepLog  # noqa: E402


# ── 싱글톤 상태 (서버 기동 시 1회 초기화) ────────────────────────────────────────
# 모델을 요청마다 로드하지 않고 프로세스 시작 시 메모리에 올려두는 이유:
#   모델 로드에는 수백 ms가 걸린다. 요청마다 로드하면 서빙 불가 수준의 지연 발생.
class _AppState:
    model:  DeepLog
    config: dict
    device: torch.device


_state = _AppState()


def _load_model(ckpt_dir: Path) -> None:
    config_path  = ckpt_dir / "config.json"
    weights_path = ckpt_dir / "deeplog_best.pt"

    if not config_path.exists() or not weights_path.exists():
        raise FileNotFoundError(
            f"체크포인트 없음: {ckpt_dir}\n"
            "  실행: python model/train.py --dataset <hdfs|bgl>"
        )

    _state.config = json.loads(config_path.read_text())
    _state.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    _state.model  = DeepLog(
        input_size  = _state.config["input_size"],
        hidden_size = _state.config["hidden_size"],
        num_layers  = _state.config["num_layers"],
        num_classes = _state.config["num_classes"],
    ).to(_state.device)
    _state.model.load_state_dict(
        torch.load(weights_path, map_location=_state.device, weights_only=True)
    )
    _state.model.eval()  # Dropout·BatchNorm을 추론 모드로 전환
    print(f"[FastAPI] 모델 로드 완료  device={_state.device}  ckpt={ckpt_dir}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: 서버 시작 시 모델 로드, 종료 시 자원 해제."""
    dataset  = os.getenv("DATASET", "hdfs")
    ckpt_dir = Path(
        os.getenv("CKPT_DIR", str(_PROJECT_ROOT / "model" / "checkpoints" / dataset))
    )
    _load_model(ckpt_dir)
    yield


app = FastAPI(title="DeepLog — FastAPI serving", version="1.0", lifespan=lifespan)


# ── 요청/응답 스키마 (Pydantic) ───────────────────────────────────────────────
class PredictRequest(BaseModel):
    log_keys: list[int]
    """입력 윈도우: 정수 로그 키 목록, 길이 == window_size - 1"""

    next_key: Optional[int] = None
    """실제 관측된 다음 로그 키. 제공하면 is_anomaly가 계산됨."""

    @field_validator("log_keys")
    @classmethod
    def _check_length(cls, v: list[int]) -> list[int]:
        if len(v) == 0:
            raise ValueError("log_keys는 비어있을 수 없습니다")
        return v


class PredictResponse(BaseModel):
    top_candidates: list[int]
    """확률 상위 g개 예측 로그 키 (내림차순)"""

    probabilities: list[float]
    """top_candidates에 대응하는 softmax 확률"""

    is_anomaly: Optional[bool]
    """next_key가 top_candidates에 없으면 True. next_key 미제공 시 None."""

    latency_ms: float
    """end-to-end 추론 지연시간 (ms) — 벤치마크 측정용"""


# ── 추론 ─────────────────────────────────────────────────────────────────────
@torch.no_grad()  # 역전파 비활성화 → 추론 메모리 절약 + 속도 향상
def _infer(log_keys: list[int]) -> tuple[list[int], list[float]]:
    expected    = _state.config["window_size"] - 1
    num_classes = _state.config["num_classes"]
    g           = _state.config["num_candidates"]

    if len(log_keys) != expected:
        raise HTTPException(
            status_code=422,
            detail=f"log_keys 길이는 {expected}여야 합니다. 현재: {len(log_keys)}",
        )

    # 로그 키 정수 → one-hot 텐서 (1, window_size-1, num_classes)
    idx = torch.tensor(log_keys, dtype=torch.long, device=_state.device)
    x   = torch.zeros(1, len(log_keys), num_classes, device=_state.device)
    x[0].scatter_(1, idx.unsqueeze(1), 1.0)

    logits = _state.model(x)               # (1, num_classes)
    probs  = F.softmax(logits, dim=-1)[0]  # (num_classes,) 확률 분포

    # 상위 g개 후보 추출
    top_probs, top_idx = torch.topk(probs, k=min(g, num_classes))
    return top_idx.cpu().tolist(), top_probs.cpu().tolist()


# ── 엔드포인트 ────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status":         "ok",
        "dataset":        _state.config.get("dataset"),
        "device":         str(_state.device),
        "window_size":    _state.config.get("window_size"),
        "num_candidates": _state.config.get("num_candidates"),
    }


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    t0 = time.perf_counter()
    top_candidates, probabilities = _infer(req.log_keys)
    latency_ms = (time.perf_counter() - t0) * 1000.0

    # 이상 판정: 실제 다음 키가 모델의 top-g 예측 안에 없으면 이상
    is_anomaly = (
        req.next_key not in top_candidates
        if req.next_key is not None
        else None
    )

    return PredictResponse(
        top_candidates=top_candidates,
        probabilities=probabilities,
        is_anomaly=is_anomaly,
        latency_ms=latency_ms,
    )
