FROM python:3.11-slim

WORKDIR /workspace

COPY serving/bentoml/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Model class definition
COPY model/deeplog.py  ./model/deeplog.py
COPY model/__init__.py ./model/__init__.py

# Serving code
COPY serving/bentoml/service.py    ./serving/bentoml/service.py
COPY serving/bentoml/save_model.py ./serving/bentoml/save_model.py
COPY serving/bentoml/__init__.py   ./serving/bentoml/__init__.py

ENV PYTHONPATH=/workspace
ENV BENTOML_MODEL_NAME=deeplog-hdfs
ENV BENTOML_MODEL_TAG=latest

# BentoML store is mounted at runtime via docker-compose volume:
#   bentoml_home:/root/bentoml
# Run save_model.py on host BEFORE starting this container.

EXPOSE 3000
CMD ["bentoml", "serve", "serving.bentoml.service:DeepLogService", \
     "--host", "0.0.0.0", "--port", "3000"]
