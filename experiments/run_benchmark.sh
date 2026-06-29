#!/usr/bin/env bash
# Full benchmark pipeline — runs load tests against all three serving frameworks.
#
# Prerequisites:
#   1. python model/train.py --dataset hdfs && python model/train.py --dataset bgl
#   2. python serving/bentoml/save_model.py --dataset hdfs  (and bgl)
#   3. python serving/triton/setup_model.py --dataset hdfs  (and bgl)
#   4. docker compose -f docker/docker-compose.yml up -d
#
# Usage:
#   bash experiments/run_benchmark.sh [hdfs|bgl|all]
set -euo pipefail

DATASET="${1:-all}"
DURATION=30
CONCURRENCY="1 4 8 16 32 64"
RESULTS_DIR="experiments/results"
PYTHON="python"

# Endpoint map
FASTAPI_URL="http://localhost:8000"
BENTOML_URL="http://localhost:3000"
TRITON_URL="http://localhost:8002"

mkdir -p "$RESULTS_DIR"

run_framework() {
    local framework="$1"
    local endpoint="$2"
    local dataset="$3"

    echo ""
    echo "═══════════════════════════════════════════════"
    echo "  framework : $framework"
    echo "  dataset   : $dataset"
    echo "  endpoint  : $endpoint"
    echo "═══════════════════════════════════════════════"

    # Health check
    if ! curl -sf "$endpoint/health" > /dev/null; then
        echo "[SKIP] $endpoint not reachable — is the container running?"
        return
    fi

    $PYTHON experiments/benchmark.py \
        --framework   "$framework" \
        --endpoint    "$endpoint" \
        --dataset     "$dataset" \
        --concurrency $CONCURRENCY \
        --duration    "$DURATION" \
        --output      "$RESULTS_DIR"
}

run_dataset() {
    local dataset="$1"
    echo ""
    echo "▶ Dataset: $dataset"
    run_framework "fastapi" "$FASTAPI_URL" "$dataset"
    run_framework "bentoml" "$BENTOML_URL" "$dataset"
    run_framework "triton"  "$TRITON_URL"  "$dataset"
}

if [[ "$DATASET" == "all" ]]; then
    run_dataset "hdfs"
    run_dataset "bgl"
else
    run_dataset "$DATASET"
fi

echo ""
echo "════════════════════════════════════"
echo "  Benchmark complete"
echo "  Results in: $RESULTS_DIR/"
echo "════════════════════════════════════"
ls -lh "$RESULTS_DIR/"
