"""
Recompute signal_features JSON from Binance closed candles.

Use after feature logic changes. For each kronos_decisions row, fetch historical
klines ending strictly before decision.created_at, recompute all TF features,
and upsert signal_features. Existing outcome columns stay untouched.

By default only recomputes rows missing vol_spike_ratio (stale features).
Use --force to recompute all rows regardless.

Rate limit: Binance Futures klines weight=3 per call (limit=300), 5 TFs + BTC = 18
weight/decision. Default sleep=1s between decisions → ~1080 weight/min (limit 2400).

Run:
  DATABASE_URL=postgresql://... python scripts/recompute_signal_features.py
  DATABASE_URL=postgresql://... python scripts/recompute_signal_features.py --date 2026-05-06
  DATABASE_URL=postgresql://... python scripts/recompute_signal_features.py --force --dry-run
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
    "postgresql://fvg:fvgdb_coolify_2026@zz1q4m2u2363ucx0ebosb41u:5432/fvg",
)
BINANCE_BASE = os.environ.get("BINANCE_BASE", "https://fapi.binance.com")
ALL_TFS = ("15m", "30m", "1h", "2h", "4h")

# SOCKS5 proxy — required on Dell Dubai (geo-blocked by Binance)
_SOCKS5 = os.environ.get("SOCKS5_PROXY_URL")
_PROXIES = {"https": _SOCKS5, "http": _SOCKS5} if _SOCKS5 else None
_TIMEOUT = 30  # seconds — longer for proxy round-trips
_MAX_RETRIES = 3
_RETRY_SLEEP = 5  # seconds between retries
_DECISION_SLEEP = 1.0  # seconds between decisions (~1080 weight/min, limit 2400)
_TF_SLEEP = 0.1  # seconds between TF calls within one decision


def fetch_klines_before(symbol: str, tf: str, decision_ms: int, limit: int = 300) -> list[Bar]:
    """Fetch closed futures klines strictly before decision_ms. Retries on timeout."""
    url = f"{BINANCE_BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": tf, "endTime": int(decision_ms) - 1, "limit": limit}
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=_TIMEOUT, proxies=_PROXIES)
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
        except Exception as e:
            last_exc = e
            if attempt < _MAX_RETRIES:
                log.warning("fetch retry %d/%d %s %s: %s", attempt, _MAX_RETRIES, symbol, tf, e)
                time.sleep(_RETRY_SLEEP)
    raise last_exc  # type: ignore[misc]


def fetch_bars_by_tf(symbol: str, decision_ms: int) -> dict[str, list[Bar]]:
    out = {}
    for tf in ALL_TFS:
        out[tf] = fetch_klines_before(symbol, tf, decision_ms)
        time.sleep(_TF_SLEEP)
    return out


def load_rows(conn, *, date: str | None, limit: int | None, force: bool) -> list[dict]:
    """
    Load decisions to recompute.
    By default: only rows where signal_features is missing vol_spike_ratio (stale).
    With --force: all rows.
    """
    if force:
        stale_filter = ""
    else:
        # Only rows whose signal_features.features->15m->vol_spike_ratio is missing
        stale_filter = """
            AND (
                sf.decision_id IS NULL
                OR sf.features IS NULL
                OR sf.features->'15m'->>'vol_spike_ratio' IS NULL
            )
        """

    date_filter = "AND (%s IS NULL OR k.date = %s::date)"
    limit_sql = "" if limit is None else f"LIMIT {int(limit)}"
    params: list = [date, date]

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT k.id, k.fvg_id, k.created_at, k.date, k.symbol, k.tf
            FROM kronos_decisions k
            LEFT JOIN signal_features sf ON sf.decision_id = k.id
            WHERE 1=1
              {date_filter}
              {stale_filter}
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
    parser.add_argument("--force", action="store_true",
                        help="Recompute all rows, not just stale ones missing vol_spike_ratio")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and compute only; do not write DB")
    args = parser.parse_args()

    proxy_info = _SOCKS5.split("@")[-1] if _SOCKS5 else "none"
    log.info("proxy=%s timeout=%ds decision_sleep=%.1fs", proxy_info, _TIMEOUT, _DECISION_SLEEP)

    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    btc_cache: dict[int, dict] = {}
    failed = 0
    try:
        rows = load_rows(conn, date=args.date, limit=args.limit, force=args.force)
        log.info(
            "Recomputing %d decisions | date=%s force=%s dry_run=%s",
            len(rows), args.date, args.force, args.dry_run,
        )
        if not rows:
            log.info("Nothing to recompute — all features up to date.")
            return

        for i, row in enumerate(rows, 1):
            try:
                decision_ms = int(row["created_at"])
                bars_by_tf = fetch_bars_by_tf(row["symbol"], decision_ms)
                features = extract_multi_tf(bars_by_tf, symbol=row["symbol"], with_ls_ratio=False)

                # Validate: vol_spike_ratio must be present after recompute
                if features.get("15m", {}).get("vol_spike_ratio") is None:
                    log.warning("vol_spike_ratio missing after recompute for %s — skipping", row["id"])
                    continue

                btc_key = (decision_ms // 3_600_000) * 3_600_000
                if btc_key not in btc_cache:
                    btc_cache[btc_key] = btc_regime(
                        {"1h": fetch_klines_before("BTCUSDT", "1h", decision_ms)}
                    )
                btc_ctx = btc_cache[btc_key]

                if not args.dry_run:
                    upsert_features(conn, row, features, btc_ctx)
                    conn.commit()

                if i % 25 == 0 or i == len(rows):
                    log.info("progress %d/%d | failed=%d", i, len(rows), failed)

                # Rate limit guard: sleep between decisions
                time.sleep(_DECISION_SLEEP)

            except Exception as e:
                failed += 1
                conn.rollback()
                log.error("failed %s %s: %s", row.get("id"), row.get("symbol"), e)
                # Extra sleep after failure to avoid hammering on errors
                time.sleep(_RETRY_SLEEP)

        log.info("Done. processed=%d failed=%d", len(rows), failed)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
