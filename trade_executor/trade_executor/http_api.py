import base64

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from trade_executor.account_api import account_summary, save_user_keys
from trade_executor.config import settings
from trade_executor.crypto import encrypt
from trade_executor.db import create_pool

app = FastAPI(title="trade_executor")
_pool = None


async def _pool_or_create():
    global _pool
    if _pool is None:
        _pool = await create_pool(settings.DATABASE_URL, min_size=1, max_size=4)
    return _pool


def _check_internal_token(x_internal_token: str | None) -> None:
    if x_internal_token != settings.INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


def _master_key() -> bytes:
    return base64.b64decode(settings.MASTER_ENCRYPTION_KEY)


class EncryptIn(BaseModel):
    plaintext: str


class EncryptOut(BaseModel):
    blob_b64: str


class KeysIn(BaseModel):
    api_key: str
    api_secret: str


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/encrypt", response_model=EncryptOut)
async def encrypt_endpoint(
    body: EncryptIn,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    _check_internal_token(x_internal_token)
    blob = encrypt(body.plaintext, _master_key())
    return {"blob_b64": base64.b64encode(blob).decode()}


@app.get("/account/summary")
async def account_summary_endpoint(
    telegram_id: int,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    _check_internal_token(x_internal_token)
    pool = await _pool_or_create()
    return await account_summary(pool, telegram_id=telegram_id)


@app.post("/users/{telegram_id}/keys")
async def save_user_keys_endpoint(
    telegram_id: int,
    body: KeysIn,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
):
    _check_internal_token(x_internal_token)
    pool = await _pool_or_create()
    return await save_user_keys(
        pool,
        telegram_id=telegram_id,
        api_key=body.api_key,
        api_secret=body.api_secret,
    )
