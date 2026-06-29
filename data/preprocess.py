"""
Preprocessing pipeline for DeepLog experiments.

Input  : raw LogHub structured CSVs (HDFS_1, BGL)
Output : data/processed/{dataset}/
           train_normal.npy   — shape (N, window_size), int32 log-key sequences
           test.npy           — shape (M, window_size), int32 log-key sequences
           test_labels.npy    — shape (M,), int32  0=normal / 1=anomaly
           vocab.json         — { "event_id": int, ... }  (EventId -> index mapping)
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ── DeepLog hyper-parameters (match these in train.py / serving) ──────────────
WINDOW_SIZE = 10   # number of log keys per sequence (h in the paper)
# ─────────────────────────────────────────────────────────────────────────────


# ── HDFS ─────────────────────────────────────────────────────────────────────

def _build_vocab(event_ids: pd.Series) -> dict[str, int]:
    """Map unique EventId strings to consecutive integers (0-based)."""
    unique = sorted(event_ids.unique())
    return {eid: idx for idx, eid in enumerate(unique)}


def preprocess_hdfs(raw_dir: Path, out_dir: Path) -> None:
    """
    HDFS_1 structured CSV  ->  session-based sequences.

    Each session = all log events belonging to one BlockId.
    Train : normal sessions only.
    Test  : all sessions (labelled via anomaly_label.csv).
    """
    structured_csv = raw_dir / "HDFS_1" / "HDFS.log_structured.csv"
    label_csv      = raw_dir / "HDFS_1" / "anomaly_label.csv"

    if not structured_csv.exists():
        sys.exit(f"[ERROR] Not found: {structured_csv}\n  Run data/download.sh first.")
    if not label_csv.exists():
        sys.exit(f"[ERROR] Not found: {label_csv}\n  Run data/download.sh first.")

    print("[HDFS] Loading structured log ...")
    df = pd.read_csv(structured_csv, usecols=["BlockId", "EventId"])

    print("[HDFS] Loading anomaly labels ...")
    labels_df = pd.read_csv(label_csv)
    labels_df["Label"] = (labels_df["Label"] == "Anomaly").astype(int)
    label_map: dict[str, int] = dict(zip(labels_df["BlockId"], labels_df["Label"]))

    vocab = _build_vocab(df["EventId"])

    seqs: list[list[int]]  = []
    lbls: list[int]         = []

    print("[HDFS] Building session sequences ...")
    for block_id, group in df.groupby("BlockId"):
        keys = group["EventId"].map(vocab).tolist()
        # Sliding window within session
        for i in range(max(1, len(keys) - WINDOW_SIZE + 1)):
            window = keys[i : i + WINDOW_SIZE]
            if len(window) < WINDOW_SIZE:
                window += [0] * (WINDOW_SIZE - len(window))  # pad with 0
            seqs.append(window)
            lbls.append(label_map.get(block_id, 0))

    seqs_arr = np.array(seqs, dtype=np.int32)
    lbls_arr = np.array(lbls, dtype=np.int32)

    normal_mask = lbls_arr == 0
    train_seqs  = seqs_arr[normal_mask]

    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / "train_normal.npy", train_seqs)
    np.save(out_dir / "test.npy",         seqs_arr)
    np.save(out_dir / "test_labels.npy",  lbls_arr)
    (out_dir / "vocab.json").write_text(json.dumps(vocab, ensure_ascii=False, indent=2))

    print(f"[HDFS] train_normal : {train_seqs.shape}")
    print(f"[HDFS] test         : {seqs_arr.shape}  (anomaly rate: {lbls_arr.mean():.2%})")
    print(f"[HDFS] vocab size   : {len(vocab)}")
    print(f"[HDFS] Saved to {out_dir}")


# ── BGL ──────────────────────────────────────────────────────────────────────

def preprocess_bgl(raw_dir: Path, out_dir: Path) -> None:
    """
    BGL structured CSV  ->  fixed sliding-window sequences.

    BGL has no session concept; use a global sliding window over the timeline.
    A window is anomalous if ANY line in it has an alert flag.
    Train : windows with no anomaly.
    Test  : all windows (labelled).
    """
    structured_csv = raw_dir / "BGL" / "BGL.log_structured.csv"

    if not structured_csv.exists():
        sys.exit(f"[ERROR] Not found: {structured_csv}\n  Run data/download.sh first.")

    print("[BGL] Loading structured log ...")
    df = pd.read_csv(structured_csv, usecols=["Label", "EventId"])

    # BGL anomaly: Label column is '-' for normal, otherwise alert type string
    df["is_anomaly"] = (df["Label"] != "-").astype(int)

    vocab = _build_vocab(df["EventId"])

    keys   = df["EventId"].map(vocab).to_numpy(dtype=np.int32)
    is_anom = df["is_anomaly"].to_numpy(dtype=np.int32)

    n = len(keys)
    seqs: list[list[int]] = []
    lbls: list[int]       = []

    print("[BGL] Building sliding-window sequences ...")
    for i in range(0, n - WINDOW_SIZE + 1, 1):   # step=1 (dense)
        seqs.append(keys[i : i + WINDOW_SIZE].tolist())
        lbls.append(int(is_anom[i : i + WINDOW_SIZE].any()))

    seqs_arr = np.array(seqs, dtype=np.int32)
    lbls_arr = np.array(lbls, dtype=np.int32)

    normal_mask = lbls_arr == 0
    train_seqs  = seqs_arr[normal_mask]

    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / "train_normal.npy", train_seqs)
    np.save(out_dir / "test.npy",         seqs_arr)
    np.save(out_dir / "test_labels.npy",  lbls_arr)
    (out_dir / "vocab.json").write_text(json.dumps(vocab, ensure_ascii=False, indent=2))

    print(f"[BGL] train_normal : {train_seqs.shape}")
    print(f"[BGL] test         : {seqs_arr.shape}  (anomaly rate: {lbls_arr.mean():.2%})")
    print(f"[BGL] vocab size   : {len(vocab)}")
    print(f"[BGL] Saved to {out_dir}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Preprocess HDFS/BGL for DeepLog.")
    parser.add_argument(
        "--dataset", choices=["hdfs", "bgl", "all"], default="all",
        help="Which dataset to preprocess (default: all)"
    )
    parser.add_argument(
        "--raw-dir", type=Path, default=Path(__file__).parent / "raw",
        help="Directory containing raw LogHub data (default: data/raw)"
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path(__file__).parent / "processed",
        help="Output directory (default: data/processed)"
    )
    args = parser.parse_args()

    if args.dataset in ("hdfs", "all"):
        preprocess_hdfs(args.raw_dir, args.out_dir / "hdfs")
    if args.dataset in ("bgl", "all"):
        preprocess_bgl(args.raw_dir, args.out_dir / "bgl")


if __name__ == "__main__":
    main()
