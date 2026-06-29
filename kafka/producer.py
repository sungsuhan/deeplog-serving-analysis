"""
Kafka producer — streams preprocessed log sequences for load simulation.

Usage:
    python kafka/producer.py --dataset hdfs --topic deeplog-hdfs --rps 100
    python kafka/producer.py --dataset hdfs --topic deeplog-hdfs --rps 0  # max speed

Each Kafka message is a JSON object:
    { "log_keys": [int, ...], "next_key": int, "seq_id": int }
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
from kafka import KafkaProducer

_PROJECT_ROOT = Path(__file__).parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset",   choices=["hdfs", "bgl"], required=True)
    parser.add_argument("--topic",     default=None,
                        help="Kafka topic (default: deeplog-{dataset})")
    parser.add_argument("--bootstrap", default="localhost:9092")
    parser.add_argument("--rps",       type=float, default=0,
                        help="Target send rate in requests/sec. 0 = as fast as possible.")
    parser.add_argument("--limit",     type=int, default=0,
                        help="Max number of messages to send. 0 = all sequences.")
    parser.add_argument("--data-dir",  type=Path,
                        default=_PROJECT_ROOT / "data" / "processed")
    args = parser.parse_args()

    topic    = args.topic or f"deeplog-{args.dataset}"
    data_dir = args.data_dir / args.dataset

    test_seqs   = np.load(data_dir / "test.npy")       # (N, window_size)
    test_labels = np.load(data_dir / "test_labels.npy")

    # input: seq[:-1],  target: seq[-1]
    inputs  = test_seqs[:, :-1]
    targets = test_seqs[:, -1]

    limit = args.limit if args.limit > 0 else len(inputs)
    print(f"[Producer] dataset={args.dataset}  topic={topic}  "
          f"messages={limit}  rps={'max' if args.rps == 0 else args.rps}")

    producer = KafkaProducer(
        bootstrap_servers = args.bootstrap,
        value_serializer  = lambda v: json.dumps(v).encode("utf-8"),
        compression_type  = "gzip",
        linger_ms         = 5,
        batch_size        = 32768,
    )

    interval = (1.0 / args.rps) if args.rps > 0 else 0.0
    sent     = 0
    t_start  = time.perf_counter()

    try:
        for i in range(limit):
            payload = {
                "seq_id":   i,
                "log_keys": inputs[i].tolist(),
                "next_key": int(targets[i]),
                "label":    int(test_labels[i]),
            }
            producer.send(topic, payload)
            sent += 1

            if interval > 0:
                elapsed  = time.perf_counter() - t_start
                expected = sent * interval
                if expected > elapsed:
                    time.sleep(expected - elapsed)

            if sent % 10000 == 0:
                elapsed = time.perf_counter() - t_start
                print(f"  sent={sent}  elapsed={elapsed:.1f}s  "
                      f"actual_rps={sent/elapsed:.0f}")
    finally:
        producer.flush()
        producer.close()

    elapsed = time.perf_counter() - t_start
    print(f"[Producer] done  sent={sent}  elapsed={elapsed:.2f}s  "
          f"avg_rps={sent/elapsed:.0f}")


if __name__ == "__main__":
    main()
