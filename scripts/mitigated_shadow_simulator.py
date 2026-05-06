"""
Mitigated FVG shadow simulator.

For every kronos_decision row with event_type IN ('mitigated_breakout',
'mitigated_reversal'), replay forward Binance klines using the row's STORED
entry/sl/tp1/tp2 (NOT recomputed from zone geometry — breakout/reversal SL
anchors differ from entry-style trades) to fill signal_features outcomes.

Outcome (per row, per direction):
  win  = TP2 hit before SL
  tp1  = TP1 hit then SL
  loss = SL hit before TP
  null = neither within MAX_BARS (ranging)

Storage convention (no schema change):
  direction='long'  -> writes long_outcome  / long_pnl_pct  / long_bars
  direction='short' -> writes short_outcome / short_pnl_pct / short_bars
Filter by event_type in the kronos_decisions join when querying WR.

Run: python scripts/mitigated_shadow_simulator.py [--max-bars 200] [--limit N]
"""
from __future__ import annotations
import os
import sys
import time
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import psycopg2.extras
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mitig_shadow")

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fvg:f18bbdd5a18785da8d18d8d92965defc@localhost:5432/fvg",
)
BINANCE_BASE = os.environ.get("BINANCE_BASE", "https://fapi.binance.com")
TF_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000}

MITIG_EVENTS = ("mitigated_breakout", "mitigated_reversal")


def fetch_forward_klines(symbol: str, tf: str, start_ms: int, max_bars: int = 200) -> list:
    url = f"{BINANCE_BASE}/fapi/v1/klines"
    after = int(start_ms) + TF_MS.get(tf, 3_600_000)
    params = {"symbol": symbol, "interval": tf, "startTime": after, "limit": max_bars}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning("klines %s %s @%s failed: %s", symbol, tf, after, e)
        return []


def simulate(is_long: bool, entry: float, sl: float, tp1: float, tp2: float, klines: list):
    tp1_hit = False
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

        if sl_hit:
            if tp1_hit:
                return ("tp1", 0.0, i)
            return ("loss", -risk_pct, i)
        if tp2_hit:
            return ("win", reward_pct, i)
        if tp1_now:
            tp1_hit = True

    return (None, None, len(klines))


def _decision_created_ms(decision_id: str, fallback_ms: int) -> int:
    """Decision IDs use born_time; for forward sim we want mitigation time.
    Use the actual signal_features.created_at if available — that's set to
    born_time too, so use bar's open_time via klines that follow."""
    return int(fallback_ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-bars", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0, help="0 = no limit")
    args = ap.parse_args()

    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Mitigated decisions: pull each row that still has its direction's
        # outcome unset. A breakout-row (direction=short, say) writes to
        # short_outcome; a reversal-row (direction=long) writes to long_outcome.
        # The two rows share the same fvg_id but DIFFERENT decision_ids; only
        # ONE of them gets a signal_features row (linked via FK to the
        # mitigated_breakout decision_id, see main.py). So we LEFT JOIN
        # signal_features to find the canonical features row by fvg_id.
        limit_clause = f"LIMIT {args.limit}" if args.limit > 0 else ""

        with conn.cursor() as cur:
            cur.execute(f"""
              SELECT
                k.id           AS decision_id,
                k.fvg_id,
                k.symbol, k.tf, k.event_type, k.direction,
                k.entry, k.sl, k.tp1, k.tp2,
                sf.decision_id AS sf_decision_id,
                sf.long_outcome, sf.short_outcome
              FROM kronos_decisions k
              JOIN signal_features sf ON sf.fvg_id = k.fvg_id
                                      AND sf.decision_id IN (
                                        SELECT id FROM kronos_decisions
                                        WHERE fvg_id = k.fvg_id
                                          AND event_type = 'mitigated_breakout'
                                      )
              WHERE k.event_type IN ('mitigated_breakout','mitigated_reversal')
                AND k.valid = true
                AND k.entry IS NOT NULL
                AND (
                     (k.direction = 'long'  AND sf.long_outcome  IS NULL)
                  OR (k.direction = 'short' AND sf.short_outcome IS NULL)
                )
              ORDER BY k.created_at ASC
              {limit_clause}
            """)
            rows = cur.fetchall()
        log.info("Mitigated shadow simulating %d decisions", len(rows))

        if not rows:
            log.info("No mitigated decisions to backfill. Done.")
            return

        stats = {"win": 0, "tp1": 0, "loss": 0, "open": 0, "skip": 0}
        klines_cache: dict = {}

        for i, r in enumerate(rows, 1):
            cache_key = (r["symbol"], r["tf"], int(r["created_at"]) if r.get("created_at") else 0)
            # created_at not in SELECT — fetch from kronos_decisions
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT created_at FROM kronos_decisions WHERE id = %s",
                    (r["decision_id"],),
                )
                created_row = cur.fetchone()
            if not created_row:
                stats["skip"] += 1
                continue
            created_at = int(created_row["created_at"])
            cache_key = (r["symbol"], r["tf"], created_at)

            if cache_key in klines_cache:
                klines = klines_cache[cache_key]
            else:
                klines = fetch_forward_klines(
                    r["symbol"], r["tf"], created_at, args.max_bars,
                )
                klines_cache[cache_key] = klines
                time.sleep(0.05)

            if not klines:
                stats["skip"] += 1
                continue

            is_long = r["direction"] == "long"
            entry = float(r["entry"])
            sl = float(r["sl"])
            tp1 = float(r["tp1"])
            tp2 = float(r["tp2"])

            out, pnl, bars = simulate(is_long, entry, sl, tp1, tp2, klines)

            if out is None:
                stats["open"] += 1
                col_outcome = None
                col_pnl = None
            else:
                stats[out] += 1
                col_outcome = out
                col_pnl = round(pnl, 4) if pnl is not None else None

            if is_long:
                set_clause = "long_outcome = %s, long_pnl_pct = %s, long_bars = %s"
            else:
                set_clause = "short_outcome = %s, short_pnl_pct = %s, short_bars = %s"

            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE signal_features SET {set_clause} WHERE decision_id = %s",
                    (col_outcome, col_pnl, bars, r["sf_decision_id"]),
                )
            conn.commit()

            if i % 25 == 0:
                log.info("  %d/%d  stats=%s", i, len(rows), stats)

        log.info("Done. stats=%s", stats)

        # Summary by event_type
        with conn.cursor() as cur:
            cur.execute("""
              SELECT k.event_type,
                     CASE WHEN k.direction='long' THEN sf.long_outcome
                          ELSE sf.short_outcome END AS outcome,
                     COUNT(*) AS n,
                     ROUND(AVG(CASE WHEN k.direction='long' THEN sf.long_pnl_pct
                                    ELSE sf.short_pnl_pct END)::numeric, 3) AS avg_pnl
              FROM kronos_decisions k
              JOIN signal_features sf ON sf.fvg_id = k.fvg_id
                                      AND sf.decision_id IN (
                                        SELECT id FROM kronos_decisions
                                        WHERE fvg_id = k.fvg_id
                                          AND event_type = 'mitigated_breakout'
                                      )
              WHERE k.event_type IN ('mitigated_breakout','mitigated_reversal')
                AND k.valid = true
                AND ((k.direction='long'  AND sf.long_outcome  IS NOT NULL)
                  OR (k.direction='short' AND sf.short_outcome IS NOT NULL))
              GROUP BY 1, 2
              ORDER BY 1, 2;
            """)
            for r in cur.fetchall():
                log.info("  %s %s: n=%d avg_pnl=%s%%",
                         r["event_type"], r["outcome"], r["n"], r["avg_pnl"])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
