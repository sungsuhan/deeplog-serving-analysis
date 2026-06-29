"""
Kafka consumer — reads log sequences, calls serving endpoint, records latency.

Usage:
    python kafka/consumer.py \\
        --topic deeplog-hdfs \\
        --endpoint http://localhost:8000 \\
        --framework fastapi \\
        --output experiments/results/kafka_fastapi_hdfs.csv

One row per consumed message is appended to the output CSV:
    seq_id, latency_ms, is_anomaly, label, framework, timestamp
"""

import argparse
import csv
import json
import signal
import sys
import time
from pathlib import Path

import httpx
from kafka import KafkaConsumer


_RUNNING = True


def _handle_sigint(sig, frame):
    global _RUNNING
    _RUNNING = False
    print("\n[Consumer] stopping...")


def main() -> None:
    global _RUNNING
    signal.signal(signal.SIGINT, _handle_sigint)

    parser = argparse.ArgumentParser()
    parser.add_argument("--topic",      required=True)
    parser.add_argument("--endpoint",   required=True,
                        help="Serving endpoint base URL, e.g. http://localhost:8000")
    parser.add_argument("--framework",  required=True,
                        choices=["fastapi", "bentoml", "triton"])
    parser.add_argument("--bootstrap",  default="localhost:9092")
    parser.add_argument("--group-id",   default="deeplog-consumer")
    parser.add_argument("--output",     type=Path,
                        default=Path("experiments/results/kafka_results.csv"))
    parser.add_argument("--timeout-ms", type=int, default=5000,
                        help="Kafka poll timeout in ms")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_header = not args.output.exists()

    consumer = KafkaConsumer(
        args.topic,
        bootstrap_servers = args.bootstrap,
        group_id          = args.group_id,
        auto_offset_reset = "earliest",
        value_deserializer = lambda v: json.loads(v.decode("utf-8")),
        consumer_timeout_ms = args.timeout_ms,
    )

    predict_url = f"{args.endpoint}/predict"
    http_client = httpx.Client(timeout=10.0)

    processed = 0
    errors     = 0

    print(f"[Consumer] topic={args.topic}  endpoint={predict_url}  "
          f"output={args.output}")

    with open(args.output, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "seq_id", "latency_ms", "is_anomaly", "label",
            "framework", "timestamp",
        ])
        if write_header:
            writer.writeheader()

        try:
            for msg in consumer:
                if not _RUNNING:
                    break

                payload = msg.value
                request_body = {
                    "log_keys": payload["log_keys"],
                    "next_key": payload["next_key"],
                }

                t0 = time.perf_counter()
                try:
                    resp = http_client.post(predict_url, json=request_body)
                    resp.raise_for_status()
                    latency_ms = (time.perf_counter() - t0) * 1000.0
                    result     = resp.json()

                    writer.writerow({
                        "seq_id":     payload.get("seq_id", processed),
                        "latency_ms": round(latency_ms, 3),
                        "is_anomaly": result.get("is_anomaly"),
                        "label":      payload.get("label"),
                        "framework":  args.framework,
                        "timestamp":  time.time(),
                    })
                    processed += 1

                except Exception as exc:
                    errors += 1
                    print(f"  [WARN] request failed: {exc}")

                if processed % 1000 == 0 and processed > 0:
                    print(f"  processed={processed}  errors={errors}")

        finally:
            consumer.close()
            http_client.close()

    print(f"[Consumer] done  processed={processed}  errors={errors}  "
          f"saved -> {args.output}")


if __name__ == "__main__":
    main()
