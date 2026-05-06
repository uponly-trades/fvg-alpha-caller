import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional

import psycopg2
import psycopg2.extras

from config import DATABASE_URL

logger = logging.getLogger(__name__)

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS fvg_zones (
    id          TEXT PRIMARY KEY,
    created_at  BIGINT NOT NULL,
    date        DATE NOT NULL,
    symbol      TEXT NOT NULL,
    tf          TEXT NOT NULL,
    direction   SMALLINT NOT NULL,
    zone_top    DOUBLE PRECISION NOT NULL,
    zone_bottom DOUBLE PRECISION NOT NULL,
    price       DOUBLE PRECISION NOT NULL,
    strength    SMALLINT NOT NULL,
    rsi         DOUBLE PRECISION,
    atr         DOUBLE PRECISION,
    chart_path  TEXT
);

CREATE TABLE IF NOT EXISTS sim_trades (
    id          TEXT PRIMARY KEY,
    fvg_id      TEXT NOT NULL REFERENCES fvg_zones(id),
    created_at  BIGINT NOT NULL,
    date        DATE NOT NULL,
    symbol      TEXT NOT NULL,
    tf          TEXT NOT NULL,
    mode        TEXT NOT NULL,
    direction   TEXT NOT NULL,
    entry       DOUBLE PRECISION NOT NULL,
    sl          DOUBLE PRECISION NOT NULL,
    tp1         DOUBLE PRECISION NOT NULL,
    tp2         DOUBLE PRECISION NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    closed_at   BIGINT,
    reason      TEXT
);

CREATE INDEX IF NOT EXISTS idx_sim_trades_symbol ON sim_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_sim_trades_status ON sim_trades(status);
CREATE INDEX IF NOT EXISTS idx_sim_trades_date ON sim_trades(date);
CREATE INDEX IF NOT EXISTS idx_fvg_zones_date ON fvg_zones(date);

CREATE TABLE IF NOT EXISTS kronos_decisions (
    id           TEXT PRIMARY KEY,
    fvg_id       TEXT NOT NULL,
    created_at   BIGINT NOT NULL,
    date         DATE NOT NULL,
    symbol       TEXT NOT NULL,
    tf           TEXT NOT NULL,
    event_type   TEXT NOT NULL,          -- 'new_fvg' | 'approach' | 'touch'
    zone_dir     SMALLINT NOT NULL,      -- 1=long, -1=short
    current_price DOUBLE PRECISION NOT NULL,
    source       TEXT NOT NULL,          -- 'kronos' | 'combo'
    status       TEXT NOT NULL,          -- e.g. 'LONG VALID', 'SKIP: RANGING', ...
    valid        BOOLEAN NOT NULL,
    mode         TEXT,
    reason       TEXT,
    direction    TEXT,                   -- 'long' | 'short' | null
    entry        DOUBLE PRECISION,
    sl           DOUBLE PRECISION,
    tp1          DOUBLE PRECISION,
    tp2          DOUBLE PRECISION,
    kronos_raw   JSONB,                  -- full Kronos response, null if combo fallback
    trade_id     TEXT                    -- FK to sim_trades.id, null if no trade taken
);

CREATE INDEX IF NOT EXISTS idx_kronos_decisions_symbol ON kronos_decisions(symbol);
CREATE INDEX IF NOT EXISTS idx_kronos_decisions_date ON kronos_decisions(date);
CREATE INDEX IF NOT EXISTS idx_kronos_decisions_valid ON kronos_decisions(valid);

CREATE TABLE IF NOT EXISTS sent_recaps (
    key TEXT PRIMARY KEY,
    sent_at BIGINT NOT NULL
);
"""


@contextmanager
def _cursor():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        conn.close()


def _init_db() -> None:
    with _cursor() as cur:
        cur.execute(_CREATE_TABLES)


class SimTradeStore:
    def __init__(self):
        try:
            _init_db()
        except Exception as e:
            logger.error("DB init failed: %s", e)

    def add_fvg(self, zone, chart_path: Optional[str] = None) -> bool:
        fvg_id = f"{zone.symbol}-{zone.tf}-{int(zone.born_time)}"
        created_dt = datetime.fromtimestamp(int(zone.born_time) / 1000, tz=timezone.utc)
        try:
            with _cursor() as cur:
                cur.execute("SELECT id FROM fvg_zones WHERE id = %s", (fvg_id,))
                if cur.fetchone():
                    return False
                cur.execute(
                    """INSERT INTO fvg_zones
                       (id, created_at, date, symbol, tf, direction, zone_top, zone_bottom,
                        price, strength, rsi, atr, chart_path)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        fvg_id,
                        int(zone.born_time),
                        created_dt.date(),
                        zone.symbol,
                        zone.tf,
                        int(zone.direction),
                        float(zone.top),
                        float(zone.bottom),
                        float(getattr(zone, "price", 0.0)),
                        int(getattr(zone, "main_strength", 0)),
                        float(zone.rsi) if getattr(zone, "rsi", None) is not None else None,
                        float(zone.atr) if getattr(zone, "atr", None) is not None else None,
                        chart_path,
                    ),
                )
            return True
        except Exception as e:
            logger.error("add_fvg failed: %s", e)
            return False

    def add_sim_trade(self, zone, setup, created_at: int) -> bool:
        trade = getattr(setup, "trade", None)
        if trade is None or not getattr(setup, "valid", False):
            return False
        fvg_id = f"{zone.symbol}-{zone.tf}-{int(zone.born_time)}"
        trade_id = f"{fvg_id}-{setup.mode}"
        created_dt = datetime.fromtimestamp(int(created_at) / 1000, tz=timezone.utc)
        try:
            with _cursor() as cur:
                cur.execute("SELECT id FROM sim_trades WHERE id = %s", (trade_id,))
                if cur.fetchone():
                    return False
                cur.execute(
                    """INSERT INTO sim_trades
                       (id, fvg_id, created_at, date, symbol, tf, mode, direction,
                        entry, sl, tp1, tp2, status, reason)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', %s)""",
                    (
                        trade_id,
                        fvg_id,
                        int(created_at),
                        created_dt.date(),
                        zone.symbol,
                        zone.tf,
                        setup.mode,
                        trade.direction,
                        float(trade.entry),
                        float(trade.sl),
                        float(trade.tp1),
                        float(trade.tp2),
                        setup.reason,
                    ),
                )
                # Link kronos_decision → sim_trade (match by fvg_id + mode + valid)
                cur.execute(
                    """UPDATE kronos_decisions SET trade_id = %s
                       WHERE fvg_id = %s AND mode = %s AND valid = true AND trade_id IS NULL""",
                    (trade_id, fvg_id, setup.mode),
                )
            return True
        except Exception as e:
            logger.error("add_sim_trade failed: %s", e)
            return False

    def add_kronos_decision(self, zone, setup, current_price: float, event_type: str) -> bool:
        """Log every Kronos/combo decision (valid or skip) for ML training."""
        fvg_id = f"{zone.symbol}-{zone.tf}-{int(zone.born_time)}"
        decision_id = f"{fvg_id}-{event_type}-{int(zone.born_time)}-{int(current_price * 1000)}"
        now = datetime.now(timezone.utc)
        trade = getattr(setup, "trade", None)
        kronos_raw = getattr(setup, "kronos_raw", None)
        try:
            with _cursor() as cur:
                cur.execute("SELECT id FROM kronos_decisions WHERE id = %s", (decision_id,))
                if cur.fetchone():
                    return False
                cur.execute(
                    """INSERT INTO kronos_decisions
                       (id, fvg_id, created_at, date, symbol, tf, event_type, zone_dir,
                        current_price, source, status, valid, mode, reason,
                        direction, entry, sl, tp1, tp2, kronos_raw)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        decision_id,
                        fvg_id,
                        int(zone.born_time),
                        now.date(),
                        zone.symbol,
                        zone.tf,
                        event_type,
                        int(zone.direction),
                        float(current_price),
                        getattr(setup, "source", "combo"),
                        setup.status,
                        bool(setup.valid),
                        setup.mode,
                        setup.reason,
                        trade.direction if trade else None,
                        float(trade.entry) if trade else None,
                        float(trade.sl) if trade else None,
                        float(trade.tp1) if trade else None,
                        float(trade.tp2) if trade else None,
                        json.dumps(kronos_raw) if kronos_raw else None,
                    ),
                )
            return True
        except Exception as e:
            logger.error("add_kronos_decision failed: %s", e)
            return False

    # Legacy compat for existing main.py and tests
    def add_trade(self, zone, setup, created_at: int) -> bool:
        return self.add_sim_trade(zone, setup, created_at)

    def update_open_trades(self, symbol: str, bar) -> int:
        try:
            with _cursor() as cur:
                cur.execute(
                    "SELECT id, direction, sl, tp1, tp2, status, created_at FROM sim_trades "
                    "WHERE symbol = %s AND status IN ('open', 'tp1_hit')",
                    (symbol,),
                )
                rows = cur.fetchall()
                updated = 0
                bar_time = int(bar.open_time)
                for row in rows:
                    if bar_time < int(row["created_at"]):
                        continue
                    new_status = _next_status(dict(row), bar)
                    if new_status and new_status != row["status"]:
                        closed_at = int(bar.open_time) if new_status in {"win", "loss"} else None
                        cur.execute(
                            "UPDATE sim_trades SET status = %s, closed_at = %s WHERE id = %s",
                            (new_status, closed_at, row["id"]),
                        )
                        updated += 1
            return updated
        except Exception as e:
            logger.error("update_open_trades failed: %s", e)
            return 0

    def mark_recap_sent(self, key: str) -> bool:
        """Persist recap key to DB so restarts don't re-send."""
        try:
            with _cursor() as cur:
                cur.execute("SELECT key FROM sent_recaps WHERE key = %s", (key,))
                if cur.fetchone():
                    return False
                cur.execute(
                    "INSERT INTO sent_recaps (key, sent_at) VALUES (%s, %s)",
                    (key, int(datetime.now(timezone.utc).timestamp() * 1000)),
                )
            return True
        except Exception as e:
            logger.error("mark_recap_sent failed: %s", e)
            return False

    def daily_recap(self, date: Optional[str] = None) -> Dict:
        if date is None:
            date = datetime.now(timezone.utc).date().isoformat()
        try:
            with _cursor() as cur:
                cur.execute("SELECT * FROM sim_trades WHERE date = %s", (date,))
                records = [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("daily_recap failed: %s", e)
            records = []

        open_count = sum(1 for r in records if r["status"] == "open")
        tp1_count = sum(1 for r in records if r["status"] == "tp1_hit")
        win_count = sum(1 for r in records if r["status"] == "win")
        loss_count = sum(1 for r in records if r["status"] == "loss")
        closed = win_count + loss_count
        winrate = round((win_count / closed) * 100, 1) if closed else 0.0
        recent = sorted(records, key=lambda r: r.get("created_at", 0), reverse=True)[:5]
        return {
            "date": date,
            "open": open_count,
            "tp1": tp1_count,
            "win": win_count,
            "loss": loss_count,
            "closed_winrate": winrate,
            "recent": recent,
        }


def _next_status(record: Dict, bar) -> Optional[str]:
    direction = record.get("direction")
    high = float(bar.high)
    low = float(bar.low)
    sl = float(record["sl"])
    tp1 = float(record["tp1"])
    tp2 = float(record["tp2"])

    if direction == "long":
        if low <= sl:
            return "loss"
        if high >= tp2:
            return "win"
        if record.get("status") == "open" and high >= tp1:
            return "tp1_hit"
        return None

    if high >= sl:
        return "loss"
    if low <= tp2:
        return "win"
    if record.get("status") == "open" and low <= tp1:
        return "tp1_hit"
    return None
