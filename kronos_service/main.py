"""
Kronos prediction service — FastAPI.
Run: uvicorn kronos_service.main:app --host 0.0.0.0 --port 8012
"""
from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from kronos_service.predictor import load_model, predict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEVICE = os.environ.get("KRONOS_DEVICE", "mps")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading Kronos-base on device=%s ...", DEVICE)
    load_model(device=DEVICE)
    logger.info("Kronos-base ready.")
    yield


app = FastAPI(title="Kronos Prediction Service", lifespan=lifespan)


class OHLCVBar(BaseModel):
    open: float
    high: float
    low: float
    close: float
    volume: float


class PredictRequest(BaseModel):
    bars: List[OHLCVBar]         # up to 512 bars
    current_price: float
    atr: float
    zone_direction: int          # 1 = bullish, -1 = bearish
    symbol: str = ""
    tf: str = ""


class PredictResponse(BaseModel):
    direction: str               # LONG | SHORT | RANGING
    timeframe: str               # SCALPING | INTRADAY | SWING
    entry: float
    sl: float
    tp1: float
    tp2: float
    confidence: int              # 0-100


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/predict", response_model=PredictResponse)
def predict_endpoint(req: PredictRequest):
    if len(req.bars) < 10:
        raise HTTPException(status_code=422, detail="Need at least 10 bars")
    bars_dicts = [b.model_dump() for b in req.bars]
    try:
        result = predict(
            bars=bars_dicts,
            current_price=req.current_price,
            atr=req.atr,
            zone_direction=req.zone_direction,
            tf=req.tf,
        )
    except Exception as e:
        logger.error("Kronos predict failed for %s %s: %s", req.symbol, req.tf, e)
        raise HTTPException(status_code=500, detail=str(e))
    return PredictResponse(**result)
