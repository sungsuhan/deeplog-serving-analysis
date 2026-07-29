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


def _onehot_batch(idx_batch: torch.Tensor, num_classes: int) -> torch.Tensor:
  """
  정수 인덱스 배치 (B, W-1) -> one-hot (B, W-1, C).

  기존에는 DataLoader가 샘플 1개당 __getitem__을 호출해 scatter_를
  N번(=전체 샘플 수) 실행했다. 배치 전체를 한 번에 벡터화하면 Python
  레벨 호출 횟수가 N/batch_size번으로 줄어든다 — 이 모델(hidden=64,
  2-layer LSTM)처럼 연산량 자체가 작을 땐 이 호출 횟수 차이가 실제
  순전파/역전파 시간보다 훨씬 크게 병목으로 작용한다.
  """
  batch_size, seq_len = idx_batch.shape
  onehot = torch.zeros(batch_size, seq_len, num_classes)
  onehot.scatter_(2, idx_batch.unsqueeze(-1), 1.0)
  return onehot


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


def split_train_val(sequences: np.ndarray, val_split: float, seed: int = 42):
  """
  정상 시퀀스를 train/val 텐서로 분리해 메모리에 전부 올려둔다.

  DataLoader/Dataset을 쓰지 않는 이유:
    이 모델은 연산량이 작아(§_onehot_batch 참고) torch.utils.data.DataLoader의
    멀티프로세스 워커 오버헤드(프로세스 간 텐서 직렬화·전송 — Windows는 fork를
    지원하지 않아 spawn 방식이라 특히 비싸다)가 실제 순전파/역전파 시간보다
    커져 병목이 된다. 전체 입력이 수백 MB 수준으로 한 프로세스 메모리에 다
    들어가는 규모이므로, 워커 없이 메인 프로세스에서 직접 배치를 슬라이싱하는
    편이 훨씬 빠르다.

  val_split으로 일부를 검증셋으로 분리하는 이유:
    이상 데이터가 없어도 과적합(overfitting)을 감지하기 위함.
    val_loss가 수렴하지 않으면 학습을 일찍 멈춰 일반화 성능을 보존한다.
  """
  inputs  = torch.from_numpy(sequences[:, :-1]).long()  # (N, W-1)
  targets = torch.from_numpy(sequences[:, -1]).long()   # (N,)

  n       = inputs.size(0)
  val_len = int(n * val_split)

  perm = torch.randperm(n, generator=torch.Generator().manual_seed(seed))  # 재현 가능한 분할
  val_idx, train_idx = perm[:val_len], perm[val_len:]

  return (
    inputs[train_idx], targets[train_idx],
    inputs[val_idx],   targets[val_idx],
  )


def _batch_indices(n: int, batch_size: int, shuffle: bool, generator=None):
  """배치 크기만큼씩 자른 인덱스 텐서를 순서대로 내놓는다."""
  order = torch.randperm(n, generator=generator) if shuffle else torch.arange(n)
  for start in range(0, n, batch_size):
    yield order[start:start + batch_size]


def train_one_epoch(model, inputs, targets, num_classes, batch_size,
                    optimizer, criterion, device, generator) -> float:
  model.train()
  total_loss = 0.0
  n = inputs.size(0)
  for batch_idx in _batch_indices(n, batch_size, shuffle=True, generator=generator):
    x = _onehot_batch(inputs[batch_idx], num_classes).to(device)
    y = targets[batch_idx].to(device)

    optimizer.zero_grad()
    logits = model(x)
    loss   = criterion(logits, y)
    loss.backward()
    optimizer.step()
    total_loss += loss.item() * len(batch_idx)
  return total_loss / n


@torch.no_grad()
def evaluate(model, inputs, targets, num_classes, batch_size,
            criterion, device) -> float:
  model.eval()
  total_loss = 0.0
  n = inputs.size(0)
  for batch_idx in _batch_indices(n, batch_size, shuffle=False):
    x = _onehot_batch(inputs[batch_idx], num_classes).to(device)
    y = targets[batch_idx].to(device)
    total_loss += criterion(model(x), y).item() * len(batch_idx)
  return total_loss / n


def export_torchscript(model, out_path: Path) -> None:
  """
  모델을 TorchScript(script 방식)로 저장한다.

  TorchScript가 필요한 이유:
    Triton Inference Server의 pytorch_libtorch 백엔드는 Python 인터프리터 없이
    C++ 런타임에서 모델을 직접 실행한다. 이를 위해 모델을 정적 그래프로
    변환한 뒤 직렬화해야 한다. → Python GIL 없이 멀티스레드 추론 가능.

  trace가 아닌 script를 쓰는 이유:
    torch.jit.trace로 내보낸 nn.LSTM 모델은 LibTorch C++ API에서 로드 후
    .to(cuda)로 GPU로 옮길 때 LSTM 가중치가 모듈 파라미터로 제대로 인식되지
    않아 "input tensor at cuda:0 and hidden tensor at cpu" 오류가 발생한다
    (Triton의 instance_group: KIND_GPU에서 실측 확인됨). torch.jit.script는
    nn.LSTM 서브모듈 구조를 그대로 보존해 파라미터가 정상적으로 이동한다.
  """
  model.eval()
  scripted = torch.jit.script(model)
  scripted.save(str(out_path))
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

  train_inputs, train_targets, val_inputs, val_targets = split_train_val(
    sequences, args.val_split
  )
  print(f"[INFO] train={train_inputs.size(0)}  val={val_inputs.size(0)}")
  epoch_generator = torch.Generator().manual_seed(42)

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
    train_loss = train_one_epoch(
      model, train_inputs, train_targets, num_classes, args.batch_size,
      optimizer, criterion, device, epoch_generator,
    )
    val_loss   = evaluate(
      model, val_inputs, val_targets, num_classes, args.batch_size,
      criterion, device,
    )
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
  model.load_state_dict(torch.load(
    ckpt_dir / "deeplog_best.pt", map_location=device, weights_only=True
  ))
  export_torchscript(model, ckpt_dir / "deeplog.ts")

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
