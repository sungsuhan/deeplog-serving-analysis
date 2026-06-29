FROM python:3.11-slim

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY serving/triton/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY serving/triton/app.py        ./serving/triton/app.py
COPY serving/triton/__init__.py   ./serving/triton/__init__.py

ENV PYTHONPATH=/workspace
# Triton server runs as a separate container; point to its service name in compose
ENV TRITON_URL=triton:8001
ENV MODEL_NAME=deeplog
# models/ dir (with config.json) is mounted at runtime
ENV MODEL_REPO=/workspace/serving/triton/models

EXPOSE 8002
CMD ["uvicorn", "serving.triton.app:app", \
     "--host", "0.0.0.0", "--port", "8002", \
     "--workers", "1", "--loop", "uvloop"]
