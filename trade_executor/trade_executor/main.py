import asyncio
import base64
import logging
import sys

import uvicorn

import binance_limit
from trade_executor.config import settings
from trade_executor.crypto import decrypt
from trade_executor.db import create_pool
from trade_executor.exchange import build_exchange
from trade_executor.http_api import app
from trade_executor.orchestrator import handle_signal_for_user
from trade_executor.pnl_aggregator import reconcile_user
from trade_executor.resume import resume_in_flight
from trade_executor.signal_poller import (load_last_seen, poll_once,
                                          save_last_seen, list_enabled_users)
from trade_executor.trail_manager import run_mark_price_ws

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("trade_executor")

_master = base64.b64decode(settings.MASTER_ENCRYPTION_KEY)


async def _build_user_ex(pool, user_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT binance_api_key_enc, binance_api_secret_enc FROM users WHERE id=$1",
            user_id,
        )
    if not row or not row["binance_api_key_enc"]:
        raise RuntimeError(f"user {user_id} has no keys")
    key = decrypt(bytes(row["binance_api_key_enc"]), _master)
    sec = decrypt(bytes(row["binance_api_secret_enc"]), _master)
    return build_exchange(key, sec, proxy_url=settings.BINANCE_PROXY_URL)


async def signal_loop(pool):
    while True:
        try:
            ban_ms = await binance_limit.is_banned_async()
            if ban_ms > 0:
                log.warning("signal_loop: Binance REST circuit-open %.0fs — skipping poll", ban_ms / 1000)
                await asyncio.sleep(min(ban_ms / 1000.0, settings.SIGNAL_POLL_INTERVAL_S))
                continue
            async with pool.acquire() as conn:
                last = await load_last_seen(conn)
                signals = await poll_once(conn, last_seen_ms=last)
                users = await list_enabled_users(conn) if signals else []
            for sig in signals:
                all_ok = True
                for u in users:
                    try:
                        ex = await _build_user_ex(pool, u["id"])
                        await handle_signal_for_user(
                            pool, user_id=u["id"], signal=sig, ex=ex,
                            risk_pct=float(u["risk_pct"]),
                            leverage=int(u["leverage"]),
                            max_concurrent=int(u["max_concurrent"]),
                            daily_loss_cap_pct=float(u["daily_loss_cap_pct"]),
                        )
                        await ex.close()
                    except binance_limit.BinanceBannedError as e:
                        log.warning("handle_signal_for_user banned user=%s: %s", u["id"], e)
                        all_ok = False
                    except Exception as e:
                        log.exception("handle_signal_for_user failed user=%s: %s", u["id"], e)
                        all_ok = False
                if all_ok:
                    async with pool.acquire() as conn:
                        await save_last_seen(conn, int(sig["created_at"]))
                else:
                    log.warning("signal %s not advancing last_seen — at least one user failed", sig.get("id"))
        except Exception as e:
            log.exception("signal_loop error: %s", e)
        await asyncio.sleep(settings.SIGNAL_POLL_INTERVAL_S)


async def reconcile_loop(pool):
    while True:
        try:
            async with pool.acquire() as conn:
                users = await list_enabled_users(conn)
            for u in users:
                try:
                    ex = await _build_user_ex(pool, u["id"])
                    await reconcile_user(pool, ex=ex, user_id=u["id"])
                    await ex.close()
                except Exception as e:
                    log.exception("reconcile failed user=%s: %s", u["id"], e)
        except Exception as e:
            log.exception("reconcile_loop error: %s", e)
        await asyncio.sleep(settings.PNL_RECONCILE_INTERVAL_S)


async def http_server():
    config = uvicorn.Config(app, host="0.0.0.0", port=settings.HTTP_PORT, log_level="info")
    await uvicorn.Server(config).serve()


async def get_active_symbols(pool):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT symbol FROM user_trades WHERE status IN ('open','tp1_trailed')"
        )
    return [r["symbol"] for r in rows]


async def run():
    pool = await create_pool(settings.DATABASE_URL, min_size=2, max_size=10)
    log.info("DB pool created")

    async def ex_factory(uid):
        return await _build_user_ex(pool, uid)
    await resume_in_flight(pool, ex_factory=ex_factory)
    log.info("Restart resume complete")

    await asyncio.gather(
        http_server(),
        signal_loop(pool),
        reconcile_loop(pool),
        run_mark_price_ws(
            pool, ex_factory=ex_factory,
            get_active_symbols=lambda: get_active_symbols(pool),
            proxy_url=settings.BINANCE_PROXY_URL,
        ),
    )


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
