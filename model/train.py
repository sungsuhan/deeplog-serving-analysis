"""
DeepLog 학습 스크립트.

학습 전략:
  - 정상(normal) 시퀀스만으로 학습 → 비지도 이상탐지(unsupervised anomaly detection).
  - 레이블 없이도 운용 가능하다는 점이 실무에서 핵심 장점.
  - 목표: "정상 로그 다음에는 어떤 키가 나오는가"를 모델이 암기하게 만든다.

출력물 (model/checkpoints/{dataset}/):
  deeplog_best.pt  val_loss 기준 최적 가중치 (FastAPI·BentoML에서 로드)
  deeplog.ts       TorchScript 변환본 (Triton에서 C++ 런타임으로 직접 실행)
  config.json      세 서빙 스택이 공유하는 하이퍼파라미터

사용법:
  python model/train.py --dataset hdfs
  python model/train.py --dataset bgl --epochs 50 --batch-size 4096
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset, random_split

sys.path.insert(0, str(Path(__file__).parent))
from deeplog import DeepLog

# ── 논문(Du et al. 2017) 기본 하이퍼파라미터 ──────────────────────────────────
DEFAULT_HIDDEN_SIZE    = 64
DEFAULT_NUM_LAYERS     = 2
DEFAULT_EPOCHS         = 30
DEFAULT_BATCH_SIZE     = 2048
DEFAULT_LR             = 1e-3
DEFAULT_VAL_SPLIT      = 0.1
DEFAULT_NUM_CANDIDATES = 9   # 추론 시 상위 g개 후보 — 이상 판정 기준 (학습엔 미사용)


class LogSequenceDataset(Dataset):
    """
    전처리된 시퀀스 배열을 PyTorch Dataset으로 감싸는 클래스.

    시퀀스 구조 (window_size=10):
      입력 X : seq[0:9]  — 직전 9개 로그 키  → one-hot 인코딩
      정답 y : seq[9]    — 다음에 나타날 로그 키 (다중 클래스 분류)

    one-hot을 여기서 생성하는 이유:
      정수 인덱스(long)로 저장해 두면 메모리를 절약하고,
      실제 텐서 변환은 GPU로 넘기기 직전에 수행해 전송 비용을 줄인다.
    """

    def __init__(self, sequences: np.ndarray, num_classes: int) -> None:
        self.inputs      = torch.from_numpy(sequences[:, :-1]).long()  # (N, W-1)
        self.targets     = torch.from_numpy(sequences[:, -1]).long()   # (N,)
        self.num_classes = num_classes

    def __len__(self) -> int:
        return len(self.inputs)

    def __getitem__(self, idx: int):
        x        = self.inputs[idx]                                  # (W-1,)
        x_onehot = torch.zeros(x.size(0), self.num_classes)         # (W-1, C)
        # scatter_: 인덱스 위치에 1.0을 채우는 in-place one-hot 변환
        x_onehot.scatter_(1, x.unsqueeze(1), 1.0)
        return x_onehot, self.targets[idx]


def load_data(processed_dir: Path, dataset: str):
    """정상 시퀀스와 vocab 크기를 반환한다."""
    data_dir   = processed_dir / dataset
    train_path = data_dir / "train_normal.npy"
    vocab_path = data_dir / "vocab.json"

    if not train_path.exists():
        sys.exit(
            f"[ERROR] {train_path} not found.\n"
            f"  Run: python data/preprocess.py --dataset {dataset}"
        )

    sequences   = np.load(train_path)               # (N, window_size)
    vocab       = json.loads(vocab_path.read_text())
    num_classes = len(vocab)
    return sequences, num_classes


def build_dataloaders(sequences: np.ndarray, num_classes: int,
                      val_split: float, batch_size: int):
    """
    훈련/검증 DataLoader를 생성한다.

    val_split으로 정상 데이터의 일부를 검증셋으로 분리하는 이유:
      이상 데이터가 없어도 과적합(overfitting)을 감지하기 위함.
      val_loss가 수렴하지 않으면 학습을 일찍 멈춰 일반화 성능을 보존한다.
    """
    dataset   = LogSequenceDataset(sequences, num_classes)
    val_len   = int(len(dataset) * val_split)
    train_len = len(dataset) - val_len
    train_ds, val_ds = random_split(
        dataset, [train_len, val_len],
        generator=torch.Generator().manual_seed(42)  # 재현 가능한 분할
    )
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True,
                              num_workers=2, pin_memory=True)
    val_loader   = DataLoader(val_ds,   batch_size=batch_size, shuffle=False,
                              num_workers=2, pin_memory=True)
    return train_loader, val_loader


def train_one_epoch(model, loader, optimizer, criterion, device) -> float:
    model.train()
    total_loss = 0.0
    for x, y in loader:
        x, y = x.to(device), y.to(device)
        optimizer.zero_grad()
        logits = model(x)
        loss   = criterion(logits, y)
        loss.backward()
        optimizer.step()
        total_loss += loss.item() * len(y)
    return total_loss / len(loader.dataset)


@torch.no_grad()
def evaluate(model, loader, criterion, device) -> float:
    model.eval()
    total_loss = 0.0
    for x, y in loader:
        x, y = x.to(device), y.to(device)
        logits = model(x)
        total_loss += criterion(logits, y).item() * len(y)
    return total_loss / len(loader.dataset)


def export_torchscript(model, window_size: int, num_classes: int,
                       device: torch.device, out_path: Path) -> None:
    """
    모델을 TorchScript(trace 방식)로 저장한다.

    TorchScript가 필요한 이유:
      Triton Inference Server의 pytorch_libtorch 백엔드는 Python 인터프리터 없이
      C++ 런타임에서 모델을 직접 실행한다. 이를 위해 모델을 정적 그래프로
      변환(trace)한 뒤 직렬화해야 한다. → Python GIL 없이 멀티스레드 추론 가능.
    """
    model.eval()
    dummy  = torch.zeros(1, window_size - 1, num_classes, device=device)
    traced = torch.jit.trace(model, dummy)
    traced.save(str(out_path))
    print(f"  TorchScript saved -> {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepLog 학습 스크립트")
    parser.add_argument("--dataset",        choices=["hdfs", "bgl"], required=True)
    parser.add_argument("--data-dir",       type=Path,
                        default=Path(__file__).parent.parent / "data" / "processed")
    parser.add_argument("--ckpt-dir",       type=Path,
                        default=Path(__file__).parent / "checkpoints")
    parser.add_argument("--hidden-size",    type=int,   default=DEFAULT_HIDDEN_SIZE)
    parser.add_argument("--num-layers",     type=int,   default=DEFAULT_NUM_LAYERS)
    parser.add_argument("--epochs",         type=int,   default=DEFAULT_EPOCHS)
    parser.add_argument("--batch-size",     type=int,   default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--lr",             type=float, default=DEFAULT_LR)
    parser.add_argument("--val-split",      type=float, default=DEFAULT_VAL_SPLIT)
    parser.add_argument("--num-candidates", type=int,   default=DEFAULT_NUM_CANDIDATES,
                        help="추론 시 top-g 후보 수 (config.json에 저장)")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[INFO] device   : {device}")
    print(f"[INFO] dataset  : {args.dataset}")

    # 데이터 로드 — 정상 시퀀스만 사용
    sequences, num_classes = load_data(args.data_dir, args.dataset)
    window_size = sequences.shape[1]
    print(f"[INFO] sequences: {sequences.shape}  vocab={num_classes}  window={window_size}")

    train_loader, val_loader = build_dataloaders(
        sequences, num_classes, args.val_split, args.batch_size
    )

    # 모델 초기화
    # input_size = num_classes: one-hot 벡터 차원 = vocab 크기
    model = DeepLog(
        input_size  = num_classes,
        hidden_size = args.hidden_size,
        num_layers  = args.num_layers,
        num_classes = num_classes,
    ).to(device)

    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    # CrossEntropyLoss: 다음 로그 키를 맞추는 다중 클래스 분류 손실
    criterion = nn.CrossEntropyLoss()

    ckpt_dir = args.ckpt_dir / args.dataset
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    # 학습 루프 — val_loss 기준 best 모델 체크포인트 저장
    best_val_loss = float("inf")
    best_epoch    = -1

    for epoch in range(1, args.epochs + 1):
        t0         = time.time()
        train_loss = train_one_epoch(model, train_loader, optimizer, criterion, device)
        val_loss   = evaluate(model, val_loader, criterion, device)
        elapsed    = time.time() - t0

        is_best = val_loss < best_val_loss
        if is_best:
            best_val_loss = val_loss
            best_epoch    = epoch
            torch.save(model.state_dict(), ckpt_dir / "deeplog_best.pt")

        print(
            f"Epoch [{epoch:3d}/{args.epochs}] "
            f"train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  "
            f"time={elapsed:.1f}s"
            + ("  *best*" if is_best else "")
        )

    print(f"\n[DONE] Best epoch: {best_epoch}  val_loss: {best_val_loss:.4f}")

    # 최적 가중치 로드 후 TorchScript 변환
    model.load_state_dict(torch.load(ckpt_dir / "deeplog_best.pt", map_location=device))
    export_torchscript(model, window_size, num_classes, device, ckpt_dir / "deeplog.ts")

    # 세 서빙 스택이 공유하는 설정 파일 저장
    # → 이 파일 하나로 FastAPI·BentoML·Triton이 동일한 하이퍼파라미터를 사용
    config = {
        "dataset":        args.dataset,
        "input_size":     num_classes,
        "hidden_size":    args.hidden_size,
        "num_layers":     args.num_layers,
        "num_classes":    num_classes,
        "window_size":    window_size,
        "num_candidates": args.num_candidates,
    }
    config_path = ckpt_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2))
    print(f"  Config saved -> {config_path}")
    print(f"\nCheckpoints at: {ckpt_dir}")


if __name__ == "__main__":
    main()
