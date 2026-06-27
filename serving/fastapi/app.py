"""FastAPI serving placeholder for DeepLog."""

from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/predict")
def predict(payload: dict):
    return {"prediction": "placeholder"}
