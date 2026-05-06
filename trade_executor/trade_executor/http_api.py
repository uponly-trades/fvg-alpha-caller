import base64

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from trade_executor.config import settings
from trade_executor.crypto import encrypt

app = FastAPI(title="trade_executor")


def _master_key() -> bytes:
    return base64.b64decode(settings.MASTER_ENCRYPTION_KEY)


class EncryptIn(BaseModel):
    plaintext: str


class EncryptOut(BaseModel):
    blob_b64: str


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/encrypt", response_model=EncryptOut)
async def encrypt_endpoint(
    body: EncryptIn,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    if x_internal_token != settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")
    blob = encrypt(body.plaintext, _master_key())
    return {"blob_b64": base64.b64encode(blob).decode()}
