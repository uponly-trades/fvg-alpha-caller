"""
Backfill signal_features for existing kronos_decisions rows.

Per row: fetch historical klines around decision_at, compute multi-TF features,
write to signal_features table. Idempotent — skips already-backfilled rows.

Run: python scripts/backfill_features.py
Env:  DATABASE_URL (default: localhost), BINANCE_BASE (default: fapi binance)
"""
from __future__ import annotations
import os
import sys
import json
import time
import logging
from pathlib import Path

# Make repo root importable when running from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import psycopg2.extras
import requests

from rest_client import Bar
from feature_extractor import extract_multi_tf, btc_regime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill")

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fvg:f18bbdd5a18785da8d18d8d92965defc@localhost:5432/fvg",
)
BINANCE_BASE = os.environ.get("BINANCE_BASE", "https://fapi.binance.com")
ALL_TFS = ("15m", "30m", "1h", "2h", "4h")
TF_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000}


def fetch_klines_at(symbol: str, tf: str, end_ms: int, limit: int = 300) -> list:
    """Fetch up to `limit` closed klines ending at end_ms (inclusive of decision time)."""
    url = f"{BINANCE_BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": tf, "endTime": int(end_ms) - 1, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        log.warning("klines %s %s @%s failed: %s", symbol, tf, end_ms, e)
        return []
    bars = []
    for k in raw:
        bars.append(Bar(
            open_time=int(k[0]),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
            is_closed=True,
        ))
    return bars


def fetch_bars_by_tf(symbol: str, end_ms: int) -> dict:
    out = {}
    for tf in ALL_TFS:
        out[tf] = fetch_klines_at(symbol, tf, end_ms)
        time.sleep(0.05)  # gentle rate-limit
    return out


def make_zone_stub(row):
    """Lightweight object compatible with feature_extractor's expectations (only attrs accessed)."""
    class _Z:
        pass
    z = _Z()
    z.symbol = row["symbol"]
    z.tf = row["tf"]
    z.born_time = int(row["created_at"])
    z.direction = int(row["zone_dir"])
    return z


def main():
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    btc_cache = {}  # end_ms (rounded to 1h) -> btc_context

    try:
        with conn.cursor() as cur:
            cur.execute("""
              SELECT k.id, k.fvg_id, k.created_at, k.symbol, k.tf, k.zone_dir
              FROM kronos_decisions k
              LEFT JOIN signal_features sf ON sf.decision_id = k.id
              WHERE sf.decision_id IS NULL
              ORDER BY k.created_at ASC
            """)
            rows = cur.fetchall()
        log.info("Backfilling %d decisions", len(rows))

        for i, row in enumerate(rows, 1):
            decision_id = row["id"]
            end_ms = int(row["created_at"])
            symbol = row["symbol"]

            try:
                bars_by_tf = fetch_bars_by_tf(symbol, end_ms)
                features = extract_multi_tf(bars_by_tf, symbol=symbol, with_ls_ratio=False)

                # BTC context: cache per-hour bucket to save API calls
                btc_key = (end_ms // 3_600_000) * 3_600_000
                if btc_key not in btc_cache:
                    btc_bars = {tf: fetch_klines_at("BTCUSDT", tf, end_ms) for tf in ("1h",)}
                    btc_cache[btc_key] = btc_regime(btc_bars)
                btc_ctx = btc_cache[btc_key]

                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO signal_features
                           (decision_id, fvg_id, created_at, date, symbol, tf, features, btc_context)
                           VALUES (%s, %s, %s, to_timestamp(%s/1000.0)::date, %s, %s, %s, %s)
                           ON CONFLICT (decision_id) DO NOTHING""",
                        (
                            decision_id,
                            row["fvg_id"],
                            end_ms,
                            end_ms,
                            symbol,
                            row["tf"],
                            json.dumps(features),
                            json.dumps(btc_ctx),
                        ),
                    )
                conn.commit()
                if i % 10 == 0:
                    log.info("  progress: %d/%d", i, len(rows))
            except Exception as e:
                conn.rollback()
                log.error("Failed %s: %s", decision_id, e)

        log.info("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
