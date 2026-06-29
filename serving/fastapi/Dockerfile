FROM python:3.11-slim

WORKDIR /workspace

COPY serving/fastapi/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Model class definition
COPY model/deeplog.py ./model/deeplog.py
COPY model/__init__.py ./model/__init__.py

# Serving code
COPY serving/fastapi/app.py ./serving/fastapi/app.py
COPY serving/fastapi/__init__.py ./serving/fastapi/__init__.py

ENV PYTHONPATH=/workspace
ENV DATASET=hdfs
ENV CKPT_DIR=/workspace/model/checkpoints/hdfs

EXPOSE 8000
CMD ["uvicorn", "serving.fastapi.app:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--loop", "uvloop"]
