"""
Shadow simulate SKIP: MODEL CONFLICT decisions.

For each conflict decision, we already have Model's predicted entry/sl/tp1/tp2/direction
in model_raw. We replay historical Binance klines forward from decision time and
determine what *would* have happened if the trade was taken.

Outcome rules (same as fill_outcomes.py):
  win  = TP2 hit before SL
  tp1  = TP1 hit then SL hit (partial profit)
  loss = SL hit before any TP
  null = neither hit within MAX_BARS (still open / abandoned)

Writes to signal_features.outcome / pnl_pct (only rows currently NULL).
Idempotent — only fills NULL rows.

Run: python scripts/shadow_simulator.py [--max-bars 200]
"""
from __future__ import annotations
import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import psycopg2.extras
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("shadow")

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fvg:f18bbdd5a18785da8d18d8d92965defc@localhost:5432/fvg",
)
BINANCE_BASE = os.environ.get("BINANCE_BASE", "https://fapi.binance.com")
TF_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000}


def fetch_forward_klines(symbol: str, tf: str, start_ms: int, max_bars: int = 200) -> list:
    """Fetch klines starting AFTER start_ms (decision time)."""
    url = f"{BINANCE_BASE}/fapi/v1/klines"
    # Binance returns klines >= startTime; we want strictly after decision close,
    # so add 1 bar duration to skip the bar in which decision was made.
    after = int(start_ms) + TF_MS.get(tf, 3_600_000)
    params = {"symbol": symbol, "interval": tf, "startTime": after, "limit": max_bars}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning("klines %s %s @%s failed: %s", symbol, tf, after, e)
        return []


def simulate(direction: str, entry: float, sl: float, tp1: float, tp2: float, klines: list):
    """
    Walk forward through klines bar-by-bar.
    Long: SL is below entry, TP above. Short: opposite.
    Within a single bar we check the *worse* side first (conservative):
      Long  bar: if low <= sl -> SL hit; elif high >= tp2 -> TP2 hit; elif high >= tp1 -> TP1 hit (mark, continue)
      Short bar: if high >= sl -> SL hit; elif low <= tp2 -> TP2 hit; elif low <= tp1 -> TP1 hit (mark, continue)
    Returns (outcome, pnl_pct, bars_to_close)
    """
    tp1_hit = False
    is_long = direction.lower() == "long"
    risk_pct = abs(entry - sl) / entry * 100
    reward_pct = abs(tp2 - entry) / entry * 100

    for i, k in enumerate(klines, 1):
        high = float(k[2])
        low = float(k[3])
        if is_long:
            sl_hit = low <= sl
            tp2_hit = high >= tp2
            tp1_now = high >= tp1
        else:
            sl_hit = high >= sl
            tp2_hit = low <= tp2
            tp1_now = low <= tp1

        # Conservative: if both SL and TP touched in same bar, assume SL first
        if sl_hit:
            if tp1_hit:
                return ("tp1", 0.0, i)
            return ("loss", -risk_pct, i)
        if tp2_hit:
            return ("win", reward_pct, i)
        if tp1_now:
            tp1_hit = True

    # No close within window
    return (None, None, len(klines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-bars", type=int, default=200, help="bars to replay forward")
    ap.add_argument("--dry-run", action="store_true", help="don't write to DB")
    args = ap.parse_args()

    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("""
              SELECT
                sf.decision_id, sf.symbol, sf.tf, sf.created_at,
                k.model_raw
              FROM signal_features sf
              JOIN signal_decisions k ON k.id = sf.decision_id
              WHERE k.status = 'SKIP: MODEL CONFLICT'
                AND sf.outcome IS NULL
                AND k.model_raw IS NOT NULL
              ORDER BY sf.created_at ASC
            """)
            rows = cur.fetchall()
        log.info("Shadow-simulating %d MODEL CONFLICT decisions", len(rows))

        stats = {"win": 0, "tp1": 0, "loss": 0, "open": 0, "skip": 0}
        for i, r in enumerate(rows, 1):
            kr = r["model_raw"]
            if isinstance(kr, str):
                kr = json.loads(kr)
            direction = (kr.get("direction") or "").lower()
            try:
                entry = float(kr["entry"])
                sl = float(kr["sl"])
                tp1 = float(kr["tp1"])
                tp2 = float(kr["tp2"])
            except (KeyError, TypeError, ValueError):
                stats["skip"] += 1
                continue
            if direction not in ("long", "short"):
                stats["skip"] += 1
                continue

            klines = fetch_forward_klines(r["symbol"], r["tf"], int(r["created_at"]), args.max_bars)
            time.sleep(0.05)
            if not klines:
                stats["skip"] += 1
                continue

            outcome, pnl, bars = simulate(direction, entry, sl, tp1, tp2, klines)
            if outcome is None:
                stats["open"] += 1
                continue
            stats[outcome] += 1

            if not args.dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE signal_features SET outcome=%s, pnl_pct=%s WHERE decision_id=%s",
                        (outcome, round(pnl, 4), r["decision_id"]),
                    )
                conn.commit()
            if i % 20 == 0:
                log.info("  progress %d/%d  stats=%s", i, len(rows), stats)

        log.info("Done. stats=%s", stats)

        # Re-stats after merge
        with conn.cursor() as cur:
            cur.execute("""
              SELECT outcome, COUNT(*) n, AVG(pnl_pct) avg_pnl
              FROM signal_features WHERE outcome IS NOT NULL GROUP BY outcome ORDER BY outcome
            """)
            for r in cur.fetchall():
                log.info("  %s: n=%d avg_pnl=%.2f%%", r["outcome"], r["n"], r["avg_pnl"] or 0)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
