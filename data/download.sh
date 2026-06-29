#!/usr/bin/env bash
# Download HDFS_1 and BGL datasets from LogHub (Zenodo DOI: 10.5281/zenodo.8196385)
set -euo pipefail

RAW_DIR="$(cd "$(dirname "$0")" && pwd)/raw"
mkdir -p "$RAW_DIR"

ZENODO_BASE="https://zenodo.org/record/8196385/files"

download_and_verify() {
    local name="$1"
    local url="$2"
    local dest="$RAW_DIR/$name.zip"

    if [[ -d "$RAW_DIR/$name" ]]; then
        echo "[SKIP] $name already exists at $RAW_DIR/$name"
        return
    fi

    echo "[DOWNLOAD] $name ..."
    curl -L --retry 3 --progress-bar -o "$dest" "$url"

    echo "[EXTRACT] $name ..."
    unzip -q "$dest" -d "$RAW_DIR/$name"
    rm "$dest"
    echo "[DONE] $name -> $RAW_DIR/$name"
}

download_and_verify "HDFS_1" "$ZENODO_BASE/HDFS_1.zip"
download_and_verify "BGL"    "$ZENODO_BASE/BGL.zip"

echo ""
echo "All datasets ready under $RAW_DIR"
echo "  HDFS_1: $RAW_DIR/HDFS_1/"
echo "  BGL:    $RAW_DIR/BGL/"
