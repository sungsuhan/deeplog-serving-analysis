"""
CI용 소형 합성 데이터셋을 만든다.

CI 러너에는 실제 전처리 산출물(data/processed/)이 없다(용량 문제로 저장소 밖).
대신 preprocess.py와 같은 형식의 파일을 작게 만들어, 학습 → 서빙 패키징 →
세 프레임워크 응답 비교까지 실제 코드 경로를 그대로 태운다.

만드는 파일 (<root>/data/<dataset>/):
  train_normal.npy  (N, window_size) int64 — 학습용 정상 시퀀스
  test.npy          (M, window_size) int64 — 응답 비교용 요청 payload 원천
  vocab.json        {"E1": 0, ...}  — 키 개수가 곧 num_classes

시퀀스는 "다음 키 = 이전 키 + 1 (가끔 무작위)" 규칙으로 만든다. 완전 무작위면
학습된 모델의 상위 후보 확률이 서로 비슷해져, 프레임워크 간 아주 작은 수치
차이만으로도 후보 순서가 뒤바뀔 수 있다. 규칙이 있으면 모델이 뚜렷한 1순위를
학습해 비교가 안정적이다.

이 데이터와 그걸로 학습한 모델은 검사 전용이다 — 성능 측정에 쓰지 않는다.

Usage:
  python ci/make_fixture.py --root /tmp/ci --dataset hdfs
"""

import argparse
import json
from pathlib import Path

import numpy as np


def make_sequences(rng: np.random.Generator, n: int, window: int,
                   vocab: int, noise: float) -> np.ndarray:
  seqs = np.empty((n, window), dtype=np.int64)
  seqs[:, 0] = rng.integers(0, vocab, size=n)
  for t in range(1, window):
    step = (seqs[:, t - 1] + 1) % vocab
    rand = rng.integers(0, vocab, size=n)
    seqs[:, t] = np.where(rng.random(n) < noise, rand, step)
  return seqs


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--root",    type=Path, required=True)
  parser.add_argument("--dataset", choices=["hdfs", "bgl"], default="hdfs")
  parser.add_argument("--vocab",   type=int, default=29)
  parser.add_argument("--window",  type=int, default=10)
  parser.add_argument("--n-train", type=int, default=20000)
  parser.add_argument("--n-test",  type=int, default=500)
  parser.add_argument("--noise",   type=float, default=0.1)
  parser.add_argument("--seed",    type=int, default=0)
  args = parser.parse_args()

  rng = np.random.default_rng(args.seed)
  out = args.root / "data" / args.dataset
  out.mkdir(parents=True, exist_ok=True)

  np.save(out / "train_normal.npy",
          make_sequences(rng, args.n_train, args.window, args.vocab, args.noise))
  np.save(out / "test.npy",
          make_sequences(rng, args.n_test, args.window, args.vocab, args.noise))
  (out / "vocab.json").write_text(
    json.dumps({f"E{i + 1}": i for i in range(args.vocab)}, indent=2)
  )
  print(f"[fixture] {out}  train={args.n_train}  test={args.n_test}  "
        f"vocab={args.vocab}  window={args.window}")


if __name__ == "__main__":
  main()
