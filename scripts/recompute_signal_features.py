"""
Recompute signal_features JSON from Binance closed candles.

Use after feature logic changes. For each kronos_decisions row, fetch historical
klines ending strictly before decision.created_at, recompute all TF features,
and upsert signal_features. Existing outcome columns stay untouched.

Run:
  DATABASE_URL=postgresql://... python scripts/recompute_signal_features.py --date 2026-05-06
  DATABASE_URL=postgresql://... python scripts/recompute_signal_features.py --limit 50 --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import psycopg2.extras
import requests

from feature_extractor import btc_regime, extract_multi_tf
from rest_client import Bar

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recompute_features")

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fvg:f18bbdd5a18785da8d18d8d92965defc@localhost:5432/fvg",
)
BINANCE_BASE = os.environ.get("BINANCE_BASE", "https://fapi.binance.com")
ALL_TFS = ("15m", "30m", "1h", "2h", "4h")


def fetch_klines_before(symbol: str, tf: str, decision_ms: int, limit: int = 300) -> list[Bar]:
    """Fetch closed futures klines with open/close time strictly before decision_ms."""
    url = f"{BINANCE_BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": tf, "endTime": int(decision_ms) - 1, "limit": limit}
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    bars = []
    for k in resp.json():
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


def fetch_bars_by_tf(symbol: str, decision_ms: int) -> dict[str, list[Bar]]:
    out = {}
    for tf in ALL_TFS:
        out[tf] = fetch_klines_before(symbol, tf, decision_ms)
        time.sleep(0.04)
    return out


def load_rows(conn, *, date: str | None, limit: int | None) -> list[dict]:
    where = "WHERE (%s IS NULL OR k.date = %s::date)"
    limit_sql = "" if limit is None else "LIMIT %s"
    params: list = [date, date]
    if limit is not None:
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT k.id, k.fvg_id, k.created_at, k.date, k.symbol, k.tf
            FROM kronos_decisions k
            {where}
            ORDER BY k.created_at ASC, k.id ASC
            {limit_sql}
            """,
            params,
        )
        return list(cur.fetchall())


def upsert_features(conn, row: dict, features: dict, btc_ctx: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO signal_features
              (decision_id, fvg_id, created_at, date, symbol, tf, features, btc_context)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (decision_id) DO UPDATE
              SET features = EXCLUDED.features,
                  btc_context = EXCLUDED.btc_context,
                  created_at = EXCLUDED.created_at,
                  date = EXCLUDED.date,
                  symbol = EXCLUDED.symbol,
                  tf = EXCLUDED.tf
            """,
            (
                row["id"],
                row["fvg_id"],
                row["created_at"],
                row["date"],
                row["symbol"],
                row["tf"],
                json.dumps(features),
                json.dumps(btc_ctx),
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Only recompute this UTC decision date, e.g. 2026-05-06")
    parser.add_argument("--limit", type=int, help="Maximum decisions to recompute")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and compute only; do not write DB")
    args = parser.parse_args()

    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    btc_cache: dict[int, dict] = {}
    try:
        rows = load_rows(conn, date=args.date, limit=args.limit)
        log.info("Recomputing %d decisions | date=%s dry_run=%s", len(rows), args.date, args.dry_run)
        for i, row in enumerate(rows, 1):
            try:
                decision_ms = int(row["created_at"])
                bars_by_tf = fetch_bars_by_tf(row["symbol"], decision_ms)
                features = extract_multi_tf(bars_by_tf, symbol=row["symbol"], with_ls_ratio=False)

                btc_key = (decision_ms // 3_600_000) * 3_600_000
                if btc_key not in btc_cache:
                    btc_cache[btc_key] = btc_regime({"1h": fetch_klines_before("BTCUSDT", "1h", decision_ms)})
                btc_ctx = btc_cache[btc_key]

                if not args.dry_run:
                    upsert_features(conn, row, features, btc_ctx)
                    conn.commit()
                if i % 25 == 0 or i == len(rows):
                    log.info("progress %d/%d", i, len(rows))
            except Exception as e:
                conn.rollback()
                log.error("failed %s %s: %s", row.get("id"), row.get("symbol"), e)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
