"""
Fill signal_features.outcome + pnl_pct from sim_trades.

Join logic: kronos_decisions.trade_id -> sim_trades.id (status, direction, entry, sl, tp2)
            -> signal_features.decision_id

Outcome tiers:
  'win'      = TP2 hit (full)
  'tp1'      = TP1 hit, reversed at SL (partial profit)
  'loss'     = SL hit before any TP
  null       = still open / no trade

pnl_pct uses fixed RR 1:2:
  win  -> +2 * risk_pct
  tp1  -> 0 (TP1 hit then SL — net break-even-ish; conservative)
  loss -> -1 * risk_pct
"""
from __future__ import annotations
import os
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("outcome")

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fvg:f18bbdd5a18785da8d18d8d92965defc@localhost:5432/fvg",
)


def main():
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Join via fvg_id: every decision (new_fvg/approach/touch) on a given FVG
        # inherits the outcome of the sim_trade born from that FVG. This way
        # touch+approach signals also get labeled with eventual win/loss.
        with conn.cursor() as cur:
            cur.execute("""
              SELECT DISTINCT ON (sf.decision_id)
                sf.decision_id,
                s.id AS trade_id,
                s.status,
                s.direction,
                s.entry,
                s.sl,
                s.tp1,
                s.tp2
              FROM signal_features sf
              JOIN sim_trades s ON s.fvg_id = sf.fvg_id
              WHERE s.status IN ('win', 'loss', 'tp1_hit')
                AND (sf.outcome IS NULL OR sf.pnl_pct IS NULL)
              ORDER BY sf.decision_id, s.created_at ASC
            """)
            rows = cur.fetchall()
        log.info("Filling outcomes for %d closed trades", len(rows))

        updated = 0
        for r in rows:
            entry = float(r["entry"])
            sl = float(r["sl"])
            tp2 = float(r["tp2"])
            risk_pct = abs(entry - sl) / entry * 100

            if r["status"] == "win":
                outcome = "win"
                pnl = abs(tp2 - entry) / entry * 100
                if r["direction"] == "short":
                    pnl = abs(entry - tp2) / entry * 100
            elif r["status"] == "loss":
                outcome = "loss"
                pnl = -risk_pct
            elif r["status"] == "tp1_hit":
                outcome = "tp1"
                pnl = 0.0  # conservative: TP1 then SL ~ breakeven
            else:
                continue

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE signal_features SET outcome=%s, pnl_pct=%s WHERE decision_id=%s",
                    (outcome, round(pnl, 4), r["decision_id"]),
                )
            updated += 1

        conn.commit()
        log.info("Updated %d rows", updated)

        # Stats
        with conn.cursor() as cur:
            cur.execute("""
              SELECT outcome, COUNT(*) as n, AVG(pnl_pct) as avg_pnl
              FROM signal_features WHERE outcome IS NOT NULL GROUP BY outcome ORDER BY outcome
            """)
            for r in cur.fetchall():
                log.info("  %s: n=%d avg_pnl=%.2f%%", r["outcome"], r["n"], r["avg_pnl"] or 0)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
