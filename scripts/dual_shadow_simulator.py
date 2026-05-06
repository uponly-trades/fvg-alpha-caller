"""
Dual-side shadow simulator.

For EVERY kronos_decision (any status, any event_type), compute theoretical
LONG and SHORT levels using FVG zone geometry, then replay forward Binance
klines to determine what would have happened on each side.

Levels:
  LONG:  entry=current_price, SL=zone.bottom - atr*0.1, TP1=+1R, TP2=+2R
  SHORT: entry=current_price, SL=zone.top    + atr*0.1, TP1=-1R, TP2=-2R

Outcome (per side):
  win  = TP2 hit before SL
  tp1  = TP1 hit then SL
  loss = SL hit before TP
  null = neither within MAX_BARS (ranging)

Schema: adds long_outcome/long_pnl/long_bars and short_* to signal_features.
Idempotent — only fills NULL columns.

Run: python scripts/dual_shadow_simulator.py [--max-bars 200] [--limit N]
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
log = logging.getLogger("dual_shadow")

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fvg:f18bbdd5a18785da8d18d8d92965defc@localhost:5432/fvg",
)
BINANCE_BASE = os.environ.get("BINANCE_BASE", "https://fapi.binance.com")
TF_MS = {"15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000}
TF_MIN = {"15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240}

# Binance USDT-M futures fees (taker, percent of notional)
FEE_TAKER_PCT = 0.04          # per side (0.04% open + 0.04% close = 0.08% round-trip)
FUNDING_PCT_PER_8H = 0.01     # average funding per 8h period


SCHEMA_MIGRATION = """
ALTER TABLE signal_features
  ADD COLUMN IF NOT EXISTS long_outcome      text,
  ADD COLUMN IF NOT EXISTS long_pnl_pct      double precision,
  ADD COLUMN IF NOT EXISTS long_net_pnl_pct  double precision,
  ADD COLUMN IF NOT EXISTS long_bars         integer,
  ADD COLUMN IF NOT EXISTS short_outcome     text,
  ADD COLUMN IF NOT EXISTS short_pnl_pct     double precision,
  ADD COLUMN IF NOT EXISTS short_net_pnl_pct double precision,
  ADD COLUMN IF NOT EXISTS short_bars        integer;
CREATE INDEX IF NOT EXISTS idx_sf_long_outcome  ON signal_features(long_outcome);
CREATE INDEX IF NOT EXISTS idx_sf_short_outcome ON signal_features(short_outcome);
"""


def fee_drag_pct(bars_held: int, tf: str) -> float:
    """Round-trip taker + funding for held duration. Returns % of notional."""
    fee = 2 * FEE_TAKER_PCT
    minutes = bars_held * TF_MIN.get(tf, 60)
    funding_periods = max(0, minutes // 480)
    return fee + funding_periods * FUNDING_PCT_PER_8H


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
    """Bar-by-bar replay. Returns (outcome, gross_pnl_pct, bars_held).

    Same-bar SL+TP ambiguity resolved via open-price proxy: bar's open determines
    which level the price would have approached first. Imperfect (intra-bar path
    unknown without tick data) but unbiased — better than always-SL conservative.
    """
    tp1_hit = False
    risk_pct = abs(entry - sl) / entry * 100
    reward_pct = abs(tp2 - entry) / entry * 100

    for i, k in enumerate(klines, 1):
        bar_open = float(k[1])
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

        if sl_hit and tp2_hit:
            # Both touched in same bar — pick whichever is closer to bar open
            if is_long:
                sl_first = (bar_open - sl) <= (tp2 - bar_open)
            else:
                sl_first = (sl - bar_open) <= (bar_open - tp2)
            if sl_first:
                if tp1_hit:
                    return ("tp1", 0.0, i)
                return ("loss", -risk_pct, i)
            return ("win", reward_pct, i)

        if sl_hit:
            if tp1_hit:
                return ("tp1", 0.0, i)
            return ("loss", -risk_pct, i)
        if tp2_hit:
            return ("win", reward_pct, i)
        if tp1_now:
            tp1_hit = True

    return (None, None, len(klines))


def compute_levels(zone_top: float, zone_bottom: float, atr: float, current_price: float):
    """Return (long_levels, short_levels) where each = (entry, sl, tp1, tp2) or None if invalid."""
    buffer = (atr * 0.1) if atr and atr > 0 else (abs(zone_top - zone_bottom) * 0.1)
    entry = float(current_price)

    long_levels = None
    sl_long = float(zone_bottom) - buffer
    risk_long = entry - sl_long
    if risk_long > 0:
        long_levels = (entry, sl_long, entry + risk_long, entry + risk_long * 2)

    short_levels = None
    sl_short = float(zone_top) + buffer
    risk_short = sl_short - entry
    if risk_short > 0:
        short_levels = (entry, sl_short, entry - risk_short, entry - risk_short * 2)

    return long_levels, short_levels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-bars", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0, help="0 = no limit")
    ap.add_argument("--migrate-only", action="store_true")
    ap.add_argument("--side", choices=["long", "short", "both"], default="both")
    args = ap.parse_args()

    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Migration
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MIGRATION)
        conn.commit()
        log.info("Schema migration applied")

        if args.migrate_only:
            return

        # Pull all decisions with features and zone+atr available, missing dual outcomes
        where_missing = []
        if args.side in ("long", "both"):
            where_missing.append("sf.long_outcome IS NULL")
        if args.side in ("short", "both"):
            where_missing.append("sf.short_outcome IS NULL")
        missing_cond = " OR ".join(where_missing)

        limit_clause = f"LIMIT {args.limit}" if args.limit > 0 else ""

        with conn.cursor() as cur:
            cur.execute(f"""
              SELECT
                sf.decision_id, sf.symbol, sf.tf, sf.created_at,
                k.current_price, z.zone_top, z.zone_bottom, z.atr,
                sf.long_outcome, sf.short_outcome
              FROM signal_features sf
              JOIN kronos_decisions k ON k.id = sf.decision_id
              JOIN fvg_zones z ON z.id = k.fvg_id
              WHERE z.atr IS NOT NULL
                AND ({missing_cond})
              ORDER BY sf.created_at ASC
              {limit_clause}
            """)
            rows = cur.fetchall()
        log.info("Dual-shadow simulating %d decisions", len(rows))

        stats = {
            "long":  {"win": 0, "tp1": 0, "loss": 0, "open": 0, "skip": 0},
            "short": {"win": 0, "tp1": 0, "loss": 0, "open": 0, "skip": 0},
        }
        klines_cache: dict = {}

        for i, r in enumerate(rows, 1):
            cache_key = (r["symbol"], r["tf"], int(r["created_at"]))
            if cache_key in klines_cache:
                klines = klines_cache[cache_key]
            else:
                klines = fetch_forward_klines(
                    r["symbol"], r["tf"], int(r["created_at"]), args.max_bars
                )
                klines_cache[cache_key] = klines
                time.sleep(0.05)

            if not klines:
                stats["long"]["skip"] += 1
                stats["short"]["skip"] += 1
                continue

            long_lv, short_lv = compute_levels(
                float(r["zone_top"]), float(r["zone_bottom"]),
                float(r["atr"]), float(r["current_price"]),
            )

            updates = {}

            if args.side in ("long", "both") and r["long_outcome"] is None:
                if long_lv is None:
                    stats["long"]["skip"] += 1
                else:
                    e, sl, t1, t2 = long_lv
                    out, pnl, bars = simulate(True, e, sl, t1, t2, klines)
                    if out is None:
                        stats["long"]["open"] += 1
                        updates["long_outcome"] = None
                        updates["long_pnl_pct"] = None
                        updates["long_net_pnl_pct"] = None
                        updates["long_bars"] = bars
                    else:
                        stats["long"][out] += 1
                        net = pnl - fee_drag_pct(bars, r["tf"])
                        updates["long_outcome"] = out
                        updates["long_pnl_pct"] = round(pnl, 4)
                        updates["long_net_pnl_pct"] = round(net, 4)
                        updates["long_bars"] = bars

            if args.side in ("short", "both") and r["short_outcome"] is None:
                if short_lv is None:
                    stats["short"]["skip"] += 1
                else:
                    e, sl, t1, t2 = short_lv
                    out, pnl, bars = simulate(False, e, sl, t1, t2, klines)
                    if out is None:
                        stats["short"]["open"] += 1
                        updates["short_outcome"] = None
                        updates["short_pnl_pct"] = None
                        updates["short_net_pnl_pct"] = None
                        updates["short_bars"] = bars
                    else:
                        stats["short"][out] += 1
                        net = pnl - fee_drag_pct(bars, r["tf"])
                        updates["short_outcome"] = out
                        updates["short_pnl_pct"] = round(pnl, 4)
                        updates["short_net_pnl_pct"] = round(net, 4)
                        updates["short_bars"] = bars

            if updates:
                set_clause = ", ".join(f"{k}=%s" for k in updates)
                values = list(updates.values()) + [r["decision_id"]]
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE signal_features SET {set_clause} WHERE decision_id=%s",
                        values,
                    )
                conn.commit()

            if i % 25 == 0:
                log.info("  %d/%d  long=%s short=%s", i, len(rows), stats["long"], stats["short"])

        log.info("Done. long=%s short=%s", stats["long"], stats["short"])

        # Summary
        with conn.cursor() as cur:
            cur.execute("""
              SELECT
                'long' AS side, long_outcome AS outcome, COUNT(*) n,
                ROUND(AVG(long_pnl_pct)::numeric, 3) avg_gross,
                ROUND(AVG(long_net_pnl_pct)::numeric, 3) avg_net
              FROM signal_features WHERE long_outcome IS NOT NULL
              GROUP BY long_outcome
              UNION ALL
              SELECT
                'short', short_outcome, COUNT(*),
                ROUND(AVG(short_pnl_pct)::numeric, 3),
                ROUND(AVG(short_net_pnl_pct)::numeric, 3)
              FROM signal_features WHERE short_outcome IS NOT NULL
              GROUP BY short_outcome
              ORDER BY side, outcome;
            """)
            for r in cur.fetchall():
                log.info(
                    "  %s %s: n=%d gross=%s%% net=%s%%",
                    r["side"], r["outcome"], r["n"], r["avg_gross"], r["avg_net"],
                )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
