"""
Register trained DeepLog weights in the BentoML model store.

Run ONCE after training, before starting the BentoML service:
  python serving/bentoml/save_model.py --dataset hdfs

The model tag printed at the end is loaded by service.py at startup.
"""

import argparse
import json
import sys
from pathlib import Path

import torch
import bentoml

_PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "model"))
from deeplog import DeepLog  # noqa: E402


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--dataset",  choices=["hdfs", "bgl"], required=True)
  parser.add_argument("--ckpt-dir", type=Path, default=None,
                      help="Override checkpoint directory path")
  parser.add_argument("--model-name", default="deeplog",
                      help="Name to register in BentoML store (default: deeplog)")
  args = parser.parse_args()

  ckpt_dir = args.ckpt_dir or (
    _PROJECT_ROOT / "model" / "checkpoints" / args.dataset
  )
  config_path  = ckpt_dir / "config.json"
  weights_path = ckpt_dir / "deeplog_best.pt"

  if not config_path.exists() or not weights_path.exists():
    sys.exit(
      f"[ERROR] Checkpoint not found at {ckpt_dir}.\n"
      "  Run: python model/train.py --dataset <hdfs|bgl>"
    )

  config = json.loads(config_path.read_text())

  model = DeepLog(
    input_size  = config["input_size"],
    hidden_size = config["hidden_size"],
    num_layers  = config["num_layers"],
    num_classes = config["num_classes"],
  )
  model.load_state_dict(
    torch.load(weights_path, map_location="cpu", weights_only=True)
  )
  model.eval()

  bento_model = bentoml.pytorch.save_model(
    args.model_name,
    model,
    metadata=config,
    signatures={"__call__": {"batchable": False}},
  )
  print(f"[BentoML] Saved: {bento_model.tag}")
  print(f"  List all: bentoml models list")


if __name__ == "__main__":
  main()
