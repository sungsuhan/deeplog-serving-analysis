"""
Prepare the Triton model repository from a trained DeepLog checkpoint.

Run ONCE after training, before starting Triton:
    python serving/triton/setup_model.py --dataset hdfs

Creates:
    serving/triton/models/deeplog/
        config.pbtxt          (generated with actual dims)
        1/
            model.pt          (TorchScript model copied from checkpoints)
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent

CONFIG_TEMPLATE = """\
name: "deeplog"
platform: "pytorch_libtorch"
max_batch_size: {max_batch_size}

input [
  {{
    name: "input__0"
    data_type: TYPE_FP32
    dims: [ {seq_len}, {num_classes} ]
  }}
]

output [
  {{
    name: "output__0"
    data_type: TYPE_FP32
    dims: [ {num_classes} ]
  }}
]

dynamic_batching {{
  preferred_batch_size: [ 8, 16, 32, 64 ]
  max_queue_delay_microseconds: 100
}}

instance_group [
  {{
    kind: KIND_GPU
    count: 1
  }}
]
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset",       choices=["hdfs", "bgl"], required=True)
    parser.add_argument("--ckpt-dir",      type=Path, default=None)
    parser.add_argument("--model-repo",    type=Path,
                        default=Path(__file__).parent / "models")
    parser.add_argument("--max-batch-size", type=int, default=64)
    args = parser.parse_args()

    ckpt_dir = args.ckpt_dir or (
        _PROJECT_ROOT / "model" / "checkpoints" / args.dataset
    )
    ts_path     = ckpt_dir / "deeplog.ts"
    config_path = ckpt_dir / "config.json"

    if not ts_path.exists():
        sys.exit(
            f"[ERROR] TorchScript model not found: {ts_path}\n"
            "  Run: python model/train.py --dataset <hdfs|bgl>"
        )

    config      = json.loads(config_path.read_text())
    num_classes = config["num_classes"]
    window_size = config["window_size"]
    seq_len     = window_size - 1          # input sequence length fed to LSTM

    # Build model repository structure
    version_dir = args.model_repo / "deeplog" / "1"
    version_dir.mkdir(parents=True, exist_ok=True)

    # Copy TorchScript model
    dest_model = version_dir / "model.pt"
    shutil.copy2(ts_path, dest_model)
    print(f"[OK] model.pt  -> {dest_model}")

    # Write config.pbtxt
    pbtxt = CONFIG_TEMPLATE.format(
        max_batch_size = args.max_batch_size,
        seq_len        = seq_len,
        num_classes    = num_classes,
    )
    pbtxt_path = args.model_repo / "deeplog" / "config.pbtxt"
    pbtxt_path.write_text(pbtxt)
    print(f"[OK] config.pbtxt -> {pbtxt_path}")

    # Save dataset config alongside (used by the wrapper app)
    (args.model_repo / "deeplog" / "config.json").write_text(
        json.dumps(config, indent=2)
    )
    print(f"[OK] config.json  -> {args.model_repo / 'deeplog' / 'config.json'}")
    print(f"\nTriton model repository ready: {args.model_repo}")
    print("  Start Triton:  docker compose up triton")


if __name__ == "__main__":
    main()
