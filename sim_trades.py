import json
import logging
import time
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

DO $$
BEGIN
    IF to_regclass('public.signal_decisions') IS NULL
       AND to_regclass('public.kronos_decisions') IS NOT NULL THEN
        ALTER TABLE kronos_decisions RENAME TO signal_decisions;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'signal_decisions'
          AND column_name = 'kronos_raw'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'signal_decisions'
          AND column_name = 'model_raw'
    ) THEN
        ALTER TABLE signal_decisions RENAME COLUMN kronos_raw TO model_raw;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS signal_decisions (
    id           TEXT PRIMARY KEY,
    fvg_id       TEXT NOT NULL,
    created_at   BIGINT NOT NULL,
    date         DATE NOT NULL,
    symbol       TEXT NOT NULL,
    tf           TEXT NOT NULL,
    event_type   TEXT NOT NULL,          -- 'new_fvg' | 'approach' | 'touch'
    zone_dir     SMALLINT NOT NULL,      -- 1=long, -1=short
    current_price DOUBLE PRECISION NOT NULL,
    source       TEXT NOT NULL,          -- 'model' | 'combo'
    status       TEXT NOT NULL,          -- e.g. 'LONG VALID', 'SKIP: RANGING', ...
    valid        BOOLEAN NOT NULL,
    mode         TEXT,
    reason       TEXT,
    direction    TEXT,                   -- 'long' | 'short' | null
    entry        DOUBLE PRECISION,
    sl           DOUBLE PRECISION,
    tp1          DOUBLE PRECISION,
    tp2          DOUBLE PRECISION,
    model_raw    JSONB,                  -- full model response, null if combo fallback
    trade_id     TEXT,                   -- FK to sim_trades.id, null if no trade taken
    v2_valid     BOOLEAN,                -- shadow filter v2 decision (compare vs v1)
    v2_status    TEXT,                   -- v2 status string
    v2_reason    TEXT                    -- v2 skip/take reason
);

ALTER TABLE signal_decisions ADD COLUMN IF NOT EXISTS v2_valid  BOOLEAN;
ALTER TABLE signal_decisions ADD COLUMN IF NOT EXISTS v2_status TEXT;
ALTER TABLE signal_decisions ADD COLUMN IF NOT EXISTS v2_reason TEXT;
ALTER TABLE signal_decisions ADD COLUMN IF NOT EXISTS confluence_score SMALLINT;
ALTER TABLE signal_decisions ADD COLUMN IF NOT EXISTS fvg_data JSONB;
ALTER TABLE signal_decisions ADD COLUMN IF NOT EXISTS model_raw JSONB;
CREATE INDEX IF NOT EXISTS idx_signal_decisions_v2_valid ON signal_decisions(v2_valid);
CREATE INDEX IF NOT EXISTS idx_signal_decisions_confluence ON signal_decisions(confluence_score);

CREATE INDEX IF NOT EXISTS idx_signal_decisions_symbol ON signal_decisions(symbol);
CREATE INDEX IF NOT EXISTS idx_signal_decisions_date ON signal_decisions(date);
CREATE INDEX IF NOT EXISTS idx_signal_decisions_valid ON signal_decisions(valid);

CREATE TABLE IF NOT EXISTS sent_recaps (
    key TEXT PRIMARY KEY,
    sent_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_features (
    decision_id  TEXT PRIMARY KEY REFERENCES signal_decisions(id) ON DELETE CASCADE,
    fvg_id       TEXT NOT NULL,
    created_at   BIGINT NOT NULL,
    date         DATE NOT NULL,
    symbol       TEXT NOT NULL,
    tf           TEXT NOT NULL,
    features     JSONB NOT NULL,        -- per-TF indicator snapshot
    btc_context  JSONB,                 -- BTC regime at decision time
    outcome      TEXT,                  -- backfilled later: 'win'|'loss'|null
    pnl_pct      DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_signal_features_date ON signal_features(date);
CREATE INDEX IF NOT EXISTS idx_signal_features_symbol ON signal_features(symbol);
CREATE INDEX IF NOT EXISTS idx_signal_features_outcome ON signal_features(outcome);
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
                # Link model_decision → sim_trade (match by fvg_id + mode + valid)
                cur.execute(
                    """UPDATE signal_decisions SET trade_id = %s
                       WHERE fvg_id = %s AND mode = %s AND valid = true AND trade_id IS NULL""",
                    (trade_id, fvg_id, setup.mode),
                )
            return True
        except Exception as e:
            logger.error("add_sim_trade failed: %s", e)
            return False

    @staticmethod
    def make_decision_id(zone, current_price: float, event_type: str) -> str:
        fvg_id = f"{zone.symbol}-{zone.tf}-{int(zone.born_time)}"
        return f"{fvg_id}-{event_type}-{int(zone.born_time)}-{int(current_price * 1000)}"

    def add_signal_decision(self, zone, setup, current_price: float, event_type: str) -> bool:
        """Log every model/combo decision (valid or skip) for ML training."""
        fvg_id = f"{zone.symbol}-{zone.tf}-{int(zone.born_time)}"
        decision_id = self.make_decision_id(zone, current_price, event_type)
        now = datetime.now(timezone.utc)
        trade = getattr(setup, "trade", None)
        model_raw = getattr(setup, "model_raw", None)
        try:
            with _cursor() as cur:
                cur.execute("SELECT id FROM signal_decisions WHERE id = %s", (decision_id,))
                if cur.fetchone():
                    return False
                v2 = getattr(setup, "v2_decision", None)
                # direction: trade direction if valid, else model direction from raw
                # (LONG/SHORT/RANGING from model, or long/short from trade)
                k_direction = (model_raw or {}).get("direction") if model_raw else None
                direction_val = (trade.direction if trade else None) or k_direction
                cur.execute(
                    """INSERT INTO signal_decisions
                       (id, fvg_id, created_at, date, symbol, tf, event_type, zone_dir,
                        current_price, source, status, valid, mode, reason,
                        direction, entry, sl, tp1, tp2, model_raw,
                        v2_valid, v2_status, v2_reason)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
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
                        direction_val,
                        float(trade.entry) if trade else None,
                        float(trade.sl) if trade else None,
                        float(trade.tp1) if trade else None,
                        float(trade.tp2) if trade else None,
                        json.dumps(model_raw) if model_raw else None,
                        bool(v2["valid"]) if v2 else None,
                        v2["status"] if v2 else None,
                        v2["reason"] if v2 else None,
                    ),
                )
            return True
        except Exception as e:
            logger.error("add_signal_decision failed: %s", e)
            return False

    def add_v2_decision(self, signal, signal_id: str) -> bool:
        """Persist a v2 signal as a signal_decisions row so trade_executor.signal_poller picks it up.

        valid=true is what poller filters on. The same signal is also written to
        sim_trades so public channel recaps remain fully simulation-based while
        the per-user bot/executor can process real trades separately.
        tp1=entry+1R (initial executor TP), tp2=entry+2R.
        Idempotent on (id) — duplicate signal_id returns False.
        """
        fvg_id = f"{signal.symbol}-{signal.trigger_tf}-{int(signal.zone_born_time)}"
        trade_id = f"{signal_id}-sim"
        now = datetime.now(timezone.utc)
        r = abs(float(signal.entry) - float(signal.sl))
        if signal.direction == 1:
            tp1 = float(signal.entry) + r
            tp2 = float(signal.entry) + r * 2
        else:
            tp1 = float(signal.entry) - r
            tp2 = float(signal.entry) - r * 2
        try:
            with _cursor() as cur:
                cur.execute("SELECT id FROM signal_decisions WHERE id = %s", (signal_id,))
                if cur.fetchone():
                    return False
                fvg_data = {
                    "zone_top": float(signal.zone_top),
                    "zone_bottom": float(signal.zone_bottom),
                    "zone_born_time": int(signal.zone_born_time),
                    "htf_touches": signal.htf_touches,
                    "fvg_buy_volume": float(signal.fvg_buy_volume),
                    "fvg_sell_volume": float(signal.fvg_sell_volume),
                    "atr": float(signal.atr),
                    "rsi": float(signal.indicators.get("rsi", 50.0)),
                    "volume_score": float(signal.indicators.get("volume_score", 0.0)),
                    "trend_score": float(signal.indicators.get("trend_score", 0.0)),
                    "quality_score": float(signal.indicators.get("quality_score", 0.0)),
                    "quality_score_formula_live": signal.indicators.get("quality_score_formula_live", "zeiierman_gap_atr"),
                    "main_strength": int(signal.indicators.get("main_strength", 0)),
                    "bull_strength": int(signal.indicators.get("bull_strength", 0)),
                    "bear_strength": int(signal.indicators.get("bear_strength", 0)),
                    "fvg_strength_tier": signal.indicators.get("fvg_strength_tier", "weak"),
                    "fvg_volume_imbalance": float(signal.indicators.get("fvg_volume_imbalance", 0.0) or 0.0),
                    "fvg_volume_aligned": bool(signal.indicators.get("fvg_volume_aligned", False)),
                    "touch_depth": float(signal.indicators.get("touch_depth", 0.0) or 0.0),
                    "entry_mode": signal.indicators.get("entry_mode", "close"),
                    "retest_enabled": bool(signal.indicators.get("retest_enabled", 0.0)),
                    "retest_score": float(signal.indicators.get("retest_score", 0.0) or 0.0),
                    "retest_reason": signal.indicators.get("retest_reason", ""),
                    "retest_touch_depth": float(signal.indicators.get("retest_touch_depth", 0.0) or 0.0),
                    "retest_rejection_ratio": float(signal.indicators.get("retest_rejection_ratio", 0.0) or 0.0),
                    "retest_body_ratio": float(signal.indicators.get("retest_body_ratio", 0.0) or 0.0),
                    "retest_confirmation_time": int(signal.indicators.get("retest_confirmation_time", 0) or 0),
                    "htf_obstacle_blocked": bool(signal.indicators.get("htf_obstacle_blocked", 0.0)),
                    "htf_obstacle_reason": signal.indicators.get("htf_obstacle_reason", "clear"),
                    "htf_obstacle_tf": signal.indicators.get("htf_obstacle_tf", ""),
                }
                cur.execute(
                    """INSERT INTO signal_decisions
                       (id, fvg_id, created_at, date, symbol, tf, event_type, zone_dir,
                        current_price, source, status, valid, mode, reason,
                        direction, entry, sl, tp1, tp2, confluence_score, fvg_data)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        signal_id,
                        fvg_id,
                        int(time.time() * 1000),
                        now.date(),
                        signal.symbol,
                        signal.trigger_tf,
                        "v2_fvg_retest",
                        int(signal.direction),
                        float(signal.entry),
                        "v2",
                        f"v2 {signal.direction_str.upper()} score={signal.confluence_score}",
                        True,
                        signal.direction_str,
                        f"v2 FVG retest confluence (score={signal.confluence_score})",
                        signal.direction_str,
                        float(signal.entry),
                        float(signal.sl),
                        tp1,
                        tp2,
                        int(signal.confluence_score),
                        psycopg2.extras.Json(fvg_data),
                    ),
                )
                cur.execute(
                    """INSERT INTO fvg_zones
                       (id, created_at, date, symbol, tf, direction, zone_top, zone_bottom,
                        price, strength, rsi, atr)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (id) DO NOTHING""",
                    (
                        fvg_id,
                        int(signal.zone_born_time),
                        now.date(),
                        signal.symbol,
                        signal.trigger_tf,
                        int(signal.direction),
                        float(signal.zone_top),
                        float(signal.zone_bottom),
                        float(signal.entry),
                        int(signal.indicators.get("main_strength", 0) or 0),
                        float(signal.indicators.get("rsi", 50.0) or 50.0),
                        float(signal.atr),
                    ),
                )
                cur.execute(
                    """INSERT INTO sim_trades
                       (id, fvg_id, created_at, date, symbol, tf, mode, direction,
                        entry, sl, tp1, tp2, status, reason)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',%s)
                       ON CONFLICT (id) DO NOTHING""",
                    (
                        trade_id,
                        fvg_id,
                        int(time.time() * 1000),
                        now.date(),
                        signal.symbol,
                        signal.trigger_tf,
                        "v2_retest",
                        signal.direction_str,
                        float(signal.entry),
                        float(signal.sl),
                        tp1,
                        tp2,
                        f"v2 FVG retest confluence (score={signal.confluence_score})",
                    ),
                )
                cur.execute(
                    "UPDATE signal_decisions SET trade_id = %s WHERE id = %s",
                    (trade_id, signal_id),
                )
            return True
        except Exception as e:
            logger.error("add_v2_decision failed: %s", e)
            return False

    def add_signal_features(
        self,
        decision_id: str,
        zone,
        features: Dict,
        btc_context: Optional[Dict] = None,
    ) -> bool:
        """Persist per-TF feature snapshot for a decision (read-only ML logger)."""
        fvg_id = f"{zone.symbol}-{zone.tf}-{int(zone.born_time)}"
        now = datetime.now(timezone.utc)
        try:
            with _cursor() as cur:
                cur.execute("SELECT decision_id FROM signal_features WHERE decision_id = %s", (decision_id,))
                if cur.fetchone():
                    return False
                cur.execute(
                    """INSERT INTO signal_features
                       (decision_id, fvg_id, created_at, date, symbol, tf, features, btc_context)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        decision_id,
                        fvg_id,
                        int(zone.born_time),
                        now.date(),
                        zone.symbol,
                        zone.tf,
                        json.dumps(features),
                        json.dumps(btc_context) if btc_context else None,
                    ),
                )
            return True
        except Exception as e:
            logger.error("add_signal_features failed: %s", e)
            return False

    # Legacy compat for existing main.py and tests
    def add_trade(self, zone, setup, created_at: int) -> bool:
        return self.add_sim_trade(zone, setup, created_at)

    def add_sim_trade_raw(
        self,
        *,
        symbol: str,
        tf: str,
        mode: str,
        direction: str,
        entry: float,
        sl: float,
        tp1: float,
        tp2: float,
        reason: str,
        born_time: int,
    ) -> bool:
        """
        Insert a sim_trade without requiring a full FVG zone + TradeSetupResult pair.
        Used by snipe modes that compute trade levels independently.
        fvg_id is derived from born_time for FK consistency; inserts a minimal fvg_zones
        row if none exists yet (snipe can fire before add_fvg for retest shorts).
        """
        now_ms = int(__import__("time").time() * 1000)
        fvg_id = f"{symbol}-{tf}-{born_time}"
        trade_id = f"{fvg_id}-{mode}-{now_ms}"
        created_dt = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
        try:
            with _cursor() as cur:
                # Ensure fvg_zones FK target exists
                cur.execute("SELECT id FROM fvg_zones WHERE id = %s", (fvg_id,))
                if not cur.fetchone():
                    born_dt = datetime.fromtimestamp(born_time / 1000, tz=timezone.utc)
                    cur.execute(
                        """INSERT INTO fvg_zones
                           (id, created_at, date, symbol, tf, direction, zone_top, zone_bottom,
                            price, strength)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (id) DO NOTHING""",
                        (fvg_id, born_time, born_dt.date(), symbol, tf, 1, entry, entry, entry, 0),
                    )
                cur.execute("SELECT id FROM sim_trades WHERE id = %s", (trade_id,))
                if cur.fetchone():
                    return False
                cur.execute(
                    """INSERT INTO sim_trades
                       (id, fvg_id, created_at, date, symbol, tf, mode, direction,
                        entry, sl, tp1, tp2, status, reason)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', %s)""",
                    (
                        trade_id, fvg_id, now_ms, created_dt.date(),
                        symbol, tf, mode, direction,
                        float(entry), float(sl), float(tp1), float(tp2), reason,
                    ),
                )
            return True
        except Exception as e:
            logger.error("add_sim_trade_raw failed: %s", e)
            return False

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

        # Per-trigger (mode) breakdown
        by_trigger: Dict[str, Dict] = {}
        for r in records:
            mode = r.get("mode") or "unknown"
            if mode not in by_trigger:
                by_trigger[mode] = {"win": 0, "loss": 0, "tp1": 0, "open": 0}
            s = r["status"]
            if s == "win":
                by_trigger[mode]["win"] += 1
            elif s == "loss":
                by_trigger[mode]["loss"] += 1
            elif s == "tp1_hit":
                by_trigger[mode]["tp1"] += 1
            else:
                by_trigger[mode]["open"] += 1

        # Per-symbol breakdown
        by_symbol: Dict[str, Dict] = {}
        for r in records:
            sym = r.get("symbol", "?")
            if sym not in by_symbol:
                by_symbol[sym] = {"win": 0, "loss": 0, "tp1": 0, "open": 0}
            s = r["status"]
            if s == "win":
                by_symbol[sym]["win"] += 1
            elif s == "loss":
                by_symbol[sym]["loss"] += 1
            elif s == "tp1_hit":
                by_symbol[sym]["tp1"] += 1
            else:
                by_symbol[sym]["open"] += 1

        recent = sorted(records, key=lambda r: r.get("created_at", 0), reverse=True)[:5]
        return {
            "date": date,
            "source": "sim",
            "open": open_count,
            "tp1": tp1_count,
            "win": win_count,
            "loss": loss_count,
            "closed_winrate": winrate,
            "by_trigger": by_trigger,
            "by_symbol": by_symbol,
            "recent": recent,
        }

    def _live_daily_recap(self, date: str) -> Optional[Dict]:
        """Build production recap from live execution tables.

        v2 live trading no longer writes sim_trades, so the old recap was empty.
        Prefer user_trades/user_daily_pnl when those tables exist; return None only
        for legacy/test databases that have not run multi-user migrations.
        """
        try:
            with _cursor() as cur:
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = 'user_trades'
                    ) AS has_live
                    """
                )
                row = cur.fetchone()
                if not row or not row.get("has_live"):
                    return None

                cur.execute(
                    """
                    SELECT *
                    FROM user_trades
                    WHERE opened_at >= (EXTRACT(EPOCH FROM %s::date) * 1000)::bigint
                      AND opened_at <  (EXTRACT(EPOCH FROM (%s::date + INTERVAL '1 day')) * 1000)::bigint
                    ORDER BY opened_at DESC
                    """,
                    (date, date),
                )
                records = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    """
                    SELECT
                      COALESCE(SUM(realized_pnl_usdt), 0) AS pnl_usdt,
                      COALESCE(SUM(trades_count), 0) AS trades_count,
                      COALESCE(SUM(wins_count), 0) AS wins_count,
                      CASE
                        WHEN SUM(trades_count) > 0
                        THEN SUM(wins_count)::float / SUM(trades_count)::float * 100
                        ELSE 0
                      END AS closed_winrate
                    FROM user_daily_pnl
                    WHERE day = %s::date
                    """,
                    (date,),
                )
                day = dict(cur.fetchone() or {})
        except Exception as e:
            logger.error("live_daily_recap failed: %s", e)
            return None

        open_statuses = {"opening", "open", "tp1_trailed"}
        win_statuses = {"closed_tp2", "closed_breakeven"}
        loss_statuses = {"closed_sl"}
        open_count = sum(1 for r in records if r.get("status") in open_statuses)
        tp1_count = sum(1 for r in records if r.get("status") == "tp1_trailed")
        win_count = int(day.get("wins_count") or sum(1 for r in records if r.get("status") in win_statuses))
        closed_count = int(day.get("trades_count") or sum(1 for r in records if r.get("status") in win_statuses | loss_statuses | {"manual_close"}))
        loss_count = max(0, closed_count - win_count)
        error_count = sum(1 for r in records if str(r.get("status", "")).startswith("error_"))
        winrate = round(float(day.get("closed_winrate") or 0), 1) if closed_count else 0.0

        by_symbol: Dict[str, Dict] = {}
        for r in records:
            sym = r.get("symbol", "?")
            if sym not in by_symbol:
                by_symbol[sym] = {"win": 0, "loss": 0, "tp1": 0, "open": 0, "error": 0, "pnl_usdt": 0.0}
            status = r.get("status")
            pnl = float(r.get("pnl_usdt") or 0.0)
            by_symbol[sym]["pnl_usdt"] += pnl
            if status in win_statuses:
                by_symbol[sym]["win"] += 1
            elif status in loss_statuses or status == "manual_close":
                by_symbol[sym]["loss"] += 1
            elif status == "tp1_trailed":
                by_symbol[sym]["tp1"] += 1
            elif status in open_statuses:
                by_symbol[sym]["open"] += 1
            elif str(status).startswith("error_"):
                by_symbol[sym]["error"] += 1

        recent = records[:5]
        return {
            "date": date,
            "source": "live",
            "open": open_count,
            "tp1": tp1_count,
            "win": win_count,
            "loss": loss_count,
            "error": error_count,
            "closed_winrate": winrate,
            "pnl_usdt": float(day.get("pnl_usdt") or 0.0),
            "by_trigger": {},
            "by_symbol": by_symbol,
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
