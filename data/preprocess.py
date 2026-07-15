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
  HDFS_1 -> session-based sequences.

  Built from preprocessed/Event_traces.csv, which already carries the
  per-BlockId ordered EventId sequence (Features column) — the LogHub
  Zenodo package for HDFS_1 does not ship a line-level
  HDFS.log_structured.csv, only this pre-aggregated trace file.

  Each session = all log events belonging to one BlockId.
  Train : normal sessions only.
  Test  : all sessions (labelled via anomaly_label.csv).
  """
  traces_csv = raw_dir / "HDFS_1" / "preprocessed" / "Event_traces.csv"
  label_csv  = raw_dir / "HDFS_1" / "preprocessed" / "anomaly_label.csv"

  if not traces_csv.exists():
    sys.exit(f"[ERROR] Not found: {traces_csv}\n  Run data/download.sh first.")
  if not label_csv.exists():
    sys.exit(f"[ERROR] Not found: {label_csv}\n  Run data/download.sh first.")

  print("[HDFS] Loading event traces ...")
  traces_df = pd.read_csv(traces_csv, usecols=["BlockId", "Features"])
  traces_df["Features"] = traces_df["Features"].str.strip("[]").str.split(",")

  print("[HDFS] Loading anomaly labels ...")
  labels_df = pd.read_csv(label_csv)
  labels_df["Label"] = (labels_df["Label"] == "Anomaly").astype(int)
  label_map: dict[str, int] = dict(zip(labels_df["BlockId"], labels_df["Label"]))

  vocab = _build_vocab(pd.Series(
    [event_id for seq in traces_df["Features"] for event_id in seq]
  ))

  seqs: list[list[int]]  = []
  lbls: list[int]         = []

  print("[HDFS] Building session sequences ...")
  for block_id, features in zip(traces_df["BlockId"], traces_df["Features"]):
    keys = [vocab[event_id] for event_id in features]
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

# Drain parameters tuned for BGL (Zhu et al., "Tools and Benchmarks for
# Automated Log Parsing", ICSE-SEIP 2019).
_BGL_DRAIN_SIM_TH = 0.5
_BGL_DRAIN_DEPTH  = 4


def _parse_bgl_structured(raw_log: Path, cache_dir: Path) -> pd.DataFrame:
  """
  Parse raw BGL.log into (Label, EventId) rows using Drain.

  The LogHub Zenodo package for BGL ships only the raw log — no
  line-level structured CSV or event templates — so log parsing runs
  here instead of a separate preprocessing tool. BGL log line format:
    <Label> <Timestamp> <Date> <Node> <Time> <NodeRepeat> <Type> <Component> <Level> <Content>
  Label is '-' for normal lines, otherwise the alert type.

  The ~4.7M-line parse is cached to cache_dir so it only runs once.
  """
  structured_csv = cache_dir / "BGL.log_structured.csv"
  templates_csv  = cache_dir / "BGL.log_templates.csv"

  if structured_csv.exists():
    print(f"[BGL] Using cached parse: {structured_csv}")
    return pd.read_csv(structured_csv, usecols=["Label", "EventId"])

  from drain3 import TemplateMiner
  from drain3.masking import MaskingInstruction
  from drain3.template_miner_config import TemplateMinerConfig

  config = TemplateMinerConfig()
  config.masking_instructions = [
    MaskingInstruction(r"0x[0-9a-fA-F]+", "HEX"),
    MaskingInstruction(r"\d+\.\d+\.\d+\.\d+", "IP"),
    MaskingInstruction(r"\d+", "NUM"),
  ]
  config.drain_sim_th = _BGL_DRAIN_SIM_TH
  config.drain_depth  = _BGL_DRAIN_DEPTH
  template_miner = TemplateMiner(config=config)

  labels: list[str]            = []
  event_ids: list[str]         = []
  cluster_to_eid: dict[int, str] = {}

  print(f"[BGL] Parsing {raw_log} with Drain (sim_th={_BGL_DRAIN_SIM_TH}, "
        f"depth={_BGL_DRAIN_DEPTH}) - this can take several minutes ...")
  with open(raw_log, encoding="utf-8", errors="replace") as f:
    for i, line in enumerate(f):
      parts = line.rstrip("\n").split(None, 9)
      if len(parts) < 10:
        continue  # malformed line, skip
      label, content = parts[0], parts[9]
      cluster_id = template_miner.add_log_message(content)["cluster_id"]
      if cluster_id not in cluster_to_eid:
        cluster_to_eid[cluster_id] = f"E{len(cluster_to_eid) + 1}"
      labels.append(label)
      event_ids.append(cluster_to_eid[cluster_id])

      if (i + 1) % 500_000 == 0:
        print(f"  parsed={i + 1}  clusters={len(cluster_to_eid)}")

  df = pd.DataFrame({"Label": labels, "EventId": event_ids})
  cache_dir.mkdir(parents=True, exist_ok=True)
  df.to_csv(structured_csv, index=False)

  templates_df = pd.DataFrame([
    {"EventId": cluster_to_eid[cluster.cluster_id], "EventTemplate": cluster.get_template()}
    for cluster in template_miner.drain.clusters
  ]).sort_values("EventId")
  templates_df.to_csv(templates_csv, index=False)

  print(f"[BGL] Parsed {len(df)} lines -> {len(cluster_to_eid)} templates")
  print(f"[BGL] Cached structured log -> {structured_csv}")
  return df


def preprocess_bgl(raw_dir: Path, out_dir: Path) -> None:
  """
  BGL  ->  fixed sliding-window sequences.

  BGL has no session concept; use a global sliding window over the timeline.
  A window is anomalous if ANY line in it has an alert flag.
  Train : windows with no anomaly.
  Test  : all windows (labelled).
  """
  raw_log = raw_dir / "BGL" / "BGL.log"

  if not raw_log.exists():
    sys.exit(f"[ERROR] Not found: {raw_log}\n  Run data/download.sh first.")

  df = _parse_bgl_structured(raw_log, cache_dir=raw_dir / "BGL")

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
