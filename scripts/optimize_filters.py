"""
Grid-search simple v2 filters against stored sim outcomes.

Requires recomputed signal_features first. Scores configs by expectancy while
requiring minimum sample size to avoid overfit.

Run:
  DATABASE_URL=postgresql://... python scripts/optimize_filters.py --date 2026-05-06 --direction long
  DATABASE_URL=postgresql://... python scripts/optimize_filters.py --min-trades 30 --top 20
"""
from __future__ import annotations

import argparse
import itertools
import os
import statistics
from dataclasses import dataclass
from typing import Any

import psycopg2
import psycopg2.extras

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fvg:f18bbdd5a18785da8d18d8d92965defc@localhost:5432/fvg",
)

WIN_STATUSES = {"win", "closed_tp2"}
LOSS_STATUSES = {"loss", "closed_sl"}
PARTIAL_STATUSES = {"tp1_hit", "closed_breakeven"}


@dataclass(frozen=True)
class Candidate:
    direction: str
    params: dict[str, float | None]
    trades: int
    wins: int
    losses: int
    partials: int
    winrate: float
    expectancy_r: float
    total_r: float
    max_loss_streak: int


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_rows(conn, *, date: str | None, direction: str | None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              k.id AS decision_id,
              k.direction,
              k.date,
              k.event_type,
              s.status AS trade_status,
              sf.features
            FROM signal_decisions k
            JOIN signal_features sf ON sf.decision_id = k.id
            JOIN sim_trades s ON s.fvg_id = k.fvg_id AND s.direction = k.direction
            WHERE (%s IS NULL OR k.date = %s::date)
              AND (%s IS NULL OR k.direction = %s)
              AND s.status IN ('win','loss','tp1_hit','closed_tp2','closed_sl','closed_breakeven')
            """,
            (date, date, direction, direction),
        )
        return list(cur.fetchall())


def row_passes(row: dict, params: dict[str, float | None]) -> bool:
    f = row["features"] or {}
    f15 = f.get("15m") or {}
    f1 = f.get("1h") or {}
    f4 = f.get("4h") or {}

    vol_spike = as_float(f15.get("vol_spike_ratio"))
    vol_z = as_float(f15.get("vol_z"))
    rsi7 = as_float(f15.get("rsi7"))
    rsi7_slope = as_float(f15.get("rsi7_slope"))
    rsi14_4h = as_float(f4.get("rsi14"))
    e20_4h = as_float(f4.get("ema20_dist_pct"))
    e20_1h = as_float(f1.get("ema20_dist_pct"))
    bb15 = as_float(f15.get("bb_pos"))

    if vol_spike is None or vol_spike < float(params["vol_spike_min"]):
        return False
    if vol_z is not None and vol_z > float(params["vol_z_max"]):
        return False

    if row["direction"] == "long":
        if e20_4h is None or e20_4h < float(params["e20_4h_min"]):
            return False
        if params["rsi7_max"] is not None and (rsi7 is None or rsi7 >= float(params["rsi7_max"])):
            return False
        if params["rsi14_4h_max"] is not None and (rsi14_4h is None or rsi14_4h >= float(params["rsi14_4h_max"])):
            return False
        if params["rsi7_slope_min"] is not None and (rsi7_slope is None or rsi7_slope < float(params["rsi7_slope_min"])):
            return False
        return True

    if e20_1h is None or e20_1h >= float(params["e20_1h_max"]):
        return False
    if bb15 is None or bb15 >= float(params["bb15_max"]):
        return False
    if params["rsi7_slope_max"] is not None and (rsi7_slope is None or rsi7_slope > float(params["rsi7_slope_max"])):
        return False
    return True


def score(direction: str, params: dict[str, float | None], rows: list[dict]) -> Candidate | None:
    selected = [r for r in rows if row_passes(r, params)]
    if not selected:
        return None

    pnl = []
    wins = losses = partials = 0
    streak = max_streak = 0
    for row in selected:
        status = row["trade_status"]
        if status in WIN_STATUSES:
            wins += 1
            pnl.append(2.0)
            streak = 0
        elif status in LOSS_STATUSES:
            losses += 1
            pnl.append(-1.0)
            streak += 1
            max_streak = max(max_streak, streak)
        elif status in PARTIAL_STATUSES:
            partials += 1
            pnl.append(0.0)
            streak = 0

    closed = wins + losses
    winrate = (wins / closed * 100) if closed else 0.0
    return Candidate(
        direction=direction,
        params=params,
        trades=len(selected),
        wins=wins,
        losses=losses,
        partials=partials,
        winrate=round(winrate, 2),
        expectancy_r=round(statistics.mean(pnl), 4) if pnl else 0.0,
        total_r=round(sum(pnl), 2),
        max_loss_streak=max_streak,
    )


def candidate_params(direction: str):
    if direction == "long":
        for vals in itertools.product(
            [1.2, 1.5, 2.0, 3.0],
            [10.0, 20.0],
            [1.35, 2.0, 4.0, 6.0],
            [None, 70.0, 75.0],
            [None, 70.0, 80.0],
            [None, 0.0, -2.0],
        ):
            yield {
                "vol_spike_min": vals[0],
                "vol_z_max": vals[1],
                "e20_4h_min": vals[2],
                "rsi7_max": vals[3],
                "rsi14_4h_max": vals[4],
                "rsi7_slope_min": vals[5],
            }
    else:
        for vals in itertools.product(
            [1.2, 1.5, 2.0, 3.0],
            [10.0, 20.0],
            [0.0, -1.0, -2.0],
            [0.3, 0.4, 0.5],
            [None, 0.0, 2.0],
        ):
            yield {
                "vol_spike_min": vals[0],
                "vol_z_max": vals[1],
                "e20_1h_max": vals[2],
                "bb15_max": vals[3],
                "rsi7_slope_max": vals[4],
            }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="UTC decision date, e.g. 2026-05-06")
    parser.add_argument("--direction", choices=["long", "short"], help="Filter direction")
    parser.add_argument("--min-trades", type=int, default=20)
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()

    directions = [args.direction] if args.direction else ["long", "short"]
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        for direction in directions:
            rows = load_rows(conn, date=args.date, direction=direction)
            results = []
            for params in candidate_params(direction):
                cand = score(direction, params, rows)
                if cand and cand.trades >= args.min_trades:
                    results.append(cand)
            results.sort(key=lambda c: (c.expectancy_r, c.total_r, c.trades), reverse=True)
            print(f"\n=== {direction.upper()} candidates | rows={len(rows)} min_trades={args.min_trades} ===")
            for cand in results[: args.top]:
                print(
                    f"trades={cand.trades:3d} WR={cand.winrate:6.2f}% "
                    f"expR={cand.expectancy_r:6.3f} totalR={cand.total_r:6.1f} "
                    f"W/L/P={cand.wins}/{cand.losses}/{cand.partials} "
                    f"maxLS={cand.max_loss_streak} params={cand.params}"
                )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
