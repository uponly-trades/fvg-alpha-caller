"""
Historical backfill: fetch klines → detect FVGs → evaluate all combos → simulate outcomes.
Run once: python backfill.py
Writes to Postgres (DATABASE_URL env var required).
"""
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests
import psycopg2
import psycopg2.extras

from config import BASE_URL, DATABASE_URL, SYMBOLS, TIMEFRAMES, MIN_STRENGTH_TO_ALERT
from fvg_engine import detect_fvg, calc_strength, FVGZone
from trade_combo import evaluate_for_mode, COMBO_TIMEFRAMES, _build_trade_levels

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("backfill")

# How many bars to fetch per TF (max 1500 per Binance request, paginate for more)
BARS_PER_TF = 1500
# How many forward bars to scan for TP/SL outcome
LOOKAHEAD = 100
# Concurrency delay between symbols to avoid rate limits
SYMBOL_DELAY = 0.12  # seconds between REST calls


@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True


def fetch_klines(symbol: str, interval: str, limit: int = 1500) -> List[Bar]:
    """Fetch up to `limit` closed klines. Paginates if needed (max 1500/req)."""
    url = f"{BASE_URL}/fapi/v1/klines"
    all_bars: List[Bar] = []
    end_time = None
    remaining = limit

    while remaining > 0:
        fetch_n = min(remaining, 1500)
        params = {"symbol": symbol, "interval": interval, "limit": fetch_n + 1}
        if end_time:
            params["endTime"] = end_time

        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:
            logger.warning("fetch_klines %s %s failed: %s", symbol, interval, e)
            break

        if not raw:
            break

        # drop last (potentially open) candle
        closed = raw[:-1]
        bars = [
            Bar(
                open_time=int(k[0]),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
            )
            for k in closed
        ]

        if not bars:
            break

        # Prepend (we paginate backwards)
        all_bars = bars + all_bars
        remaining -= len(bars)

        if len(bars) < fetch_n:
            break  # reached start of exchange history

        end_time = bars[0].open_time - 1
        time.sleep(0.05)

    return all_bars


def simulate_outcome(zone_direction: int, entry: float, sl: float, tp1: float, tp2: float,
                     forward_bars: List[Bar]) -> str:
    """Walk forward bars, return first outcome hit."""
    status = "open"
    for bar in forward_bars:
        high = bar.high
        low = bar.low
        if zone_direction == 1:  # long
            if low <= sl:
                return "loss"
            if high >= tp2:
                return "win"
            if status == "open" and high >= tp1:
                status = "tp1_hit"
        else:  # short
            if high >= sl:
                return "loss"
            if low <= tp2:
                return "win"
            if status == "open" and low <= tp1:
                status = "tp1_hit"
    return status


def _get_conn():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    return conn


def _ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fvg_zones (
                id TEXT PRIMARY KEY,
                created_at BIGINT NOT NULL,
                date DATE NOT NULL,
                symbol TEXT NOT NULL,
                tf TEXT NOT NULL,
                direction SMALLINT NOT NULL,
                zone_top DOUBLE PRECISION NOT NULL,
                zone_bottom DOUBLE PRECISION NOT NULL,
                price DOUBLE PRECISION NOT NULL,
                strength SMALLINT NOT NULL,
                rsi DOUBLE PRECISION,
                atr DOUBLE PRECISION,
                chart_path TEXT
            );
            CREATE TABLE IF NOT EXISTS sim_trades (
                id TEXT PRIMARY KEY,
                fvg_id TEXT NOT NULL REFERENCES fvg_zones(id),
                created_at BIGINT NOT NULL,
                date DATE NOT NULL,
                symbol TEXT NOT NULL,
                tf TEXT NOT NULL,
                mode TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry DOUBLE PRECISION NOT NULL,
                sl DOUBLE PRECISION NOT NULL,
                tp1 DOUBLE PRECISION NOT NULL,
                tp2 DOUBLE PRECISION NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                closed_at BIGINT,
                reason TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sim_trades_symbol ON sim_trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_sim_trades_status ON sim_trades(status);
            CREATE INDEX IF NOT EXISTS idx_sim_trades_date ON sim_trades(date);
            CREATE INDEX IF NOT EXISTS idx_fvg_zones_date ON fvg_zones(date);
        """)
    conn.commit()


def _zone_exists(cur, fvg_id: str) -> bool:
    cur.execute("SELECT 1 FROM fvg_zones WHERE id = %s", (fvg_id,))
    return cur.fetchone() is not None


def _insert_fvg(cur, fvg_id: str, zone: FVGZone, strength: dict):
    created_dt = datetime.fromtimestamp(zone.born_time / 1000, tz=timezone.utc)
    cur.execute(
        """INSERT INTO fvg_zones
           (id, created_at, date, symbol, tf, direction, zone_top, zone_bottom, price, strength, rsi, atr, chart_path)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL)
           ON CONFLICT (id) DO NOTHING""",
        (
            fvg_id,
            zone.born_time,
            created_dt.date(),
            zone.symbol,
            zone.tf,
            zone.direction,
            zone.top,
            zone.bottom,
            strength.get("price", 0.0),
            strength.get("main_strength", 0),
            strength.get("rsi"),
            strength.get("atr"),
        ),
    )


def _insert_sim_trade(cur, fvg_id: str, zone: FVGZone, mode: str,
                      trade_direction: str, entry: float, sl: float,
                      tp1: float, tp2: float, status: str, reason: str):
    trade_id = f"{fvg_id}-{mode}"
    created_dt = datetime.fromtimestamp(zone.born_time / 1000, tz=timezone.utc)
    cur.execute(
        """INSERT INTO sim_trades
           (id, fvg_id, created_at, date, symbol, tf, mode, direction, entry, sl, tp1, tp2, status, reason)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (id) DO NOTHING""",
        (
            trade_id,
            fvg_id,
            zone.born_time,
            created_dt.date(),
            zone.symbol,
            zone.tf,
            mode,
            trade_direction,
            entry,
            sl,
            tp1,
            tp2,
            status,
            reason,
        ),
    )


def process_symbol_tf(symbol: str, tf: str, bars: List[Bar],
                      bars_by_tf: Dict[str, List[Bar]], conn) -> int:
    """Scan all bars for FVGs, evaluate combos, simulate outcomes, persist."""
    saved = 0
    with conn.cursor() as cur:
        for i in range(3, len(bars)):
            window = bars[:i + 1]
            fvg = detect_fvg(window, symbol)
            if fvg is None:
                continue

            strength = calc_strength(window, fvg, symbol, {})
            if strength.get("main_strength", 0) < MIN_STRENGTH_TO_ALERT:
                continue

            born_time = fvg["born_time"]
            fvg_id = f"{symbol}-{tf}-{born_time}"

            if _zone_exists(cur, fvg_id):
                continue

            # Build a minimal zone object for evaluate_for_mode
            zone = FVGZone(
                symbol=symbol,
                tf=tf,
                direction=fvg["direction"],
                top=fvg["top"],
                bottom=fvg["bottom"],
                size=fvg["size"],
                born_time=born_time,
                main_strength=strength.get("main_strength", 0),
                atr=strength.get("atr", 0.0),
                rsi=strength.get("rsi", 50.0),
                price=strength.get("price", 0.0),
            )

            _insert_fvg(cur, fvg_id, zone, strength)

            entry_price = strength.get("price", window[-1].close)
            forward = bars[i + 1: i + 1 + LOOKAHEAD]

            for mode in COMBO_TIMEFRAMES:
                # Combo eval — use bars up to born_time for each TF
                mode_bars_by_tf = {}
                for mode_tf, mode_tf_bars in bars_by_tf.items():
                    # Only bars that existed at born_time
                    mode_bars_by_tf[mode_tf] = [b for b in mode_tf_bars if b.open_time <= born_time]

                setup = evaluate_for_mode(zone, mode, entry_price, mode_bars_by_tf)
                if not setup.valid or setup.trade is None:
                    continue

                trade = setup.trade
                outcome = simulate_outcome(
                    fvg["direction"], trade.entry, trade.sl, trade.tp1, trade.tp2, forward
                )
                _insert_sim_trade(
                    cur, fvg_id, zone, mode,
                    trade.direction, trade.entry, trade.sl, trade.tp1, trade.tp2,
                    outcome, setup.reason,
                )
                saved += 1

        conn.commit()
    return saved


def main():
    logger.info("Backfill start | symbols=%d tfs=%s bars_per_tf=%d", len(SYMBOLS), TIMEFRAMES, BARS_PER_TF)
    conn = _get_conn()
    _ensure_tables(conn)

    total_fvg = 0
    total_trades = 0

    for sym_idx, symbol in enumerate(SYMBOLS):
        logger.info("[%d/%d] %s — fetching klines...", sym_idx + 1, len(SYMBOLS), symbol)

        bars_by_tf: Dict[str, List[Bar]] = {}
        for tf in TIMEFRAMES:
            bars = fetch_klines(symbol, tf, BARS_PER_TF)
            bars_by_tf[tf] = bars
            logger.info("  %s %s: %d bars", symbol, tf, len(bars))
            time.sleep(SYMBOL_DELAY)

        for tf in TIMEFRAMES:
            bars = bars_by_tf.get(tf, [])
            if len(bars) < 10:
                continue
            n = process_symbol_tf(symbol, tf, bars, bars_by_tf, conn)
            total_trades += n
            if n:
                logger.info("  %s %s: %d sim_trades saved", symbol, tf, n)

        # Count FVG zones after this symbol
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as c FROM fvg_zones WHERE symbol = %s", (symbol,))
            row = cur.fetchone()
            fvg_count = row["c"] if row else 0
        total_fvg += fvg_count
        logger.info("  %s done | zones=%d cumulative_trades=%d", symbol, fvg_count, total_trades)

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) as c FROM fvg_zones")
        total_zones = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) as c FROM sim_trades")
        total_sim = cur.fetchone()["c"]

    logger.info("Backfill complete | fvg_zones=%d sim_trades=%d", total_zones, total_sim)
    conn.close()


if __name__ == "__main__":
    main()
