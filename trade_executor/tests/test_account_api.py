import base64
import os

import pytest

from trade_executor.crypto import decrypt


class DummyAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DummyPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return DummyAcquire(self.conn)


class SaveConn:
    def __init__(self):
        self.key_blob = None
        self.secret_blob = None
        self.tail = None

    async def fetchrow(self, query, telegram_id, key_blob, secret_blob, tail, now):
        self.key_blob = key_blob
        self.secret_blob = secret_blob
        self.tail = tail
        return {"id": 42}

    async def execute(self, *args):
        return "INSERT 0 1"


class SummaryConn:
    async def fetchrow(self, query, telegram_id):
        return {
            "id": 42,
            "enabled": True,
            "api_key_tail": "TAIL",
            "binance_api_key_enc": self.key_blob,
            "binance_api_secret_enc": self.secret_blob,
            "risk_pct": 2.0,
            "leverage": 5,
            "max_concurrent": 3,
            "daily_loss_cap_pct": 6.0,
            "paused_until": None,
            "pause_reason": None,
        }


class DummyExchange:
    def __init__(self, balance):
        self.balance = balance
        self.closed = False

    async def fetch_balance(self):
        return self.balance

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def env(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    from trade_executor import config as _cfg
    monkeypatch.setattr(_cfg.settings, "MASTER_ENCRYPTION_KEY", key)
    monkeypatch.setattr(_cfg.settings, "BINANCE_PROXY_URL", None)


@pytest.mark.asyncio
async def test_save_user_keys_validates_encrypts_and_closes(monkeypatch):
    from trade_executor import account_api

    ex = DummyExchange({"USDT": {"free": 1}})
    async def build(api_key, api_secret):
        return ex
    monkeypatch.setattr(account_api, "_build_exchange_for_keys", build)
    conn = SaveConn()

    result = await account_api.save_user_keys(
        DummyPool(conn), telegram_id=777, api_key="APIKEY1234567890", api_secret="SECRET1234567890"
    )

    assert result == {"api_key_tail": "7890"}
    assert conn.tail == "7890"
    assert ex.closed is True
    raw_key = base64.b64decode(account_api.settings.MASTER_ENCRYPTION_KEY)
    assert decrypt(bytes(conn.key_blob), raw_key) == "APIKEY1234567890"
    assert decrypt(bytes(conn.secret_blob), raw_key) == "SECRET1234567890"


@pytest.mark.asyncio
async def test_account_summary_fetches_balance_and_closes(monkeypatch):
    from trade_executor import account_api

    raw_key = base64.b64decode(account_api.settings.MASTER_ENCRYPTION_KEY)
    conn = SummaryConn()
    conn.key_blob = account_api.encrypt("APIKEY1234567890", raw_key)
    conn.secret_blob = account_api.encrypt("SECRET1234567890", raw_key)
    ex = DummyExchange({"USDT": {"free": 10, "used": 2, "total": 12}})
    async def build(api_key, api_secret):
        return ex
    monkeypatch.setattr(account_api, "_build_exchange_for_keys", build)

    result = await account_api.account_summary(DummyPool(conn), telegram_id=777)

    assert result["registered"] is True
    assert result["has_keys"] is True
    assert result["balance"] == {"free": 10.0, "used": 2.0, "total": 12.0}
    assert ex.closed is True
