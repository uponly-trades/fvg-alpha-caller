import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("TELEGRAM_CHAT_ID", "x")

from sim_trades import SimTradeStore


@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True


def trade(direction="long", created_at=1777899600000):
    levels = SimpleNamespace(
        direction=direction,
        entry=100.0,
        sl=98.0 if direction == "long" else 102.0,
        tp1=102.0 if direction == "long" else 98.0,
        tp2=104.0 if direction == "long" else 96.0,
    )
    return SimpleNamespace(
        status="LONG VALID" if direction == "long" else "SHORT VALID",
        valid=True,
        mode="scalping",
        reason="aligned combo",
        trade=levels,
    )


def zone(direction=1):
    return SimpleNamespace(symbol="BTCUSDT", tf="15m", direction=direction, born_time=1777899600000)


def test_add_valid_trade_persists_json_record(tmp_path):
    store = SimTradeStore(tmp_path / "sim_trades.json")

    added = store.add_trade(zone(), trade("long"), created_at=1777899600000)
    records = store.load()

    assert added is True
    assert len(records) == 1
    assert records[0]["id"] == "BTCUSDT-15m-1777899600000"
    assert records[0]["date"] == "2026-05-04"
    assert records[0]["symbol"] == "BTCUSDT"
    assert records[0]["mode"] == "scalping"
    assert records[0]["direction"] == "long"
    assert records[0]["entry"] == 100.0
    assert records[0]["sl"] == 98.0
    assert records[0]["tp1"] == 102.0
    assert records[0]["tp2"] == 104.0
    assert records[0]["status"] == "open"
    assert records[0]["closed_at"] is None


def test_add_trade_deduplicates_by_id(tmp_path):
    store = SimTradeStore(tmp_path / "sim_trades.json")

    assert store.add_trade(zone(), trade("long"), created_at=1777899600000) is True
    assert store.add_trade(zone(), trade("long"), created_at=1777899600000) is False

    assert len(store.load()) == 1


def test_long_trade_counts_sl_first_when_sl_and_tp_touch_same_candle(tmp_path):
    store = SimTradeStore(tmp_path / "sim_trades.json")
    store.add_trade(zone(), trade("long"), created_at=1777899600000)

    updated = store.update_open_trades("BTCUSDT", Bar(1777899660000, 100, 105, 97, 101, 100))
    record = store.load()[0]

    assert updated == 1
    assert record["status"] == "loss"
    assert record["closed_at"] == 1777899660000


def test_long_trade_updates_to_tp1_then_win(tmp_path):
    store = SimTradeStore(tmp_path / "sim_trades.json")
    store.add_trade(zone(), trade("long"), created_at=1777899600000)

    assert store.update_open_trades("BTCUSDT", Bar(1777899660000, 100, 102.5, 99, 102, 100)) == 1
    assert store.load()[0]["status"] == "tp1_hit"
    assert store.load()[0]["closed_at"] is None

    assert store.update_open_trades("BTCUSDT", Bar(1777899720000, 102, 104.5, 101, 104, 100)) == 1
    record = store.load()[0]
    assert record["status"] == "win"
    assert record["closed_at"] == 1777899720000


def test_short_trade_updates_to_loss_before_tp(tmp_path):
    store = SimTradeStore(tmp_path / "sim_trades.json")
    store.add_trade(zone(direction=-1), trade("short"), created_at=1777899600000)

    updated = store.update_open_trades("BTCUSDT", Bar(1777899660000, 100, 102.5, 95.5, 99, 100))
    record = store.load()[0]

    assert updated == 1
    assert record["status"] == "loss"
    assert record["closed_at"] == 1777899660000


def test_daily_recap_counts_today_statuses_and_recent_trades(tmp_path):
    store = SimTradeStore(tmp_path / "sim_trades.json")
    records = [
        {"id": "a", "date": "2026-05-04", "symbol": "BTCUSDT", "tf": "15m", "direction": "long", "entry": 100, "sl": 98, "tp1": 102, "tp2": 104, "status": "open", "created_at": 1, "closed_at": None, "mode": "scalping", "reason": "x"},
        {"id": "b", "date": "2026-05-04", "symbol": "ETHUSDT", "tf": "1h", "direction": "short", "entry": 100, "sl": 102, "tp1": 98, "tp2": 96, "status": "tp1_hit", "created_at": 2, "closed_at": None, "mode": "intraday", "reason": "x"},
        {"id": "c", "date": "2026-05-04", "symbol": "SOLUSDT", "tf": "4h", "direction": "long", "entry": 100, "sl": 98, "tp1": 102, "tp2": 104, "status": "win", "created_at": 3, "closed_at": 4, "mode": "swing", "reason": "x"},
        {"id": "d", "date": "2026-05-04", "symbol": "XRPUSDT", "tf": "15m", "direction": "short", "entry": 100, "sl": 102, "tp1": 98, "tp2": 96, "status": "loss", "created_at": 5, "closed_at": 6, "mode": "scalping", "reason": "x"},
    ]
    store.save(records)

    recap = store.daily_recap("2026-05-04")

    assert recap["open"] == 1
    assert recap["tp1"] == 1
    assert recap["win"] == 1
    assert recap["loss"] == 1
    assert recap["closed_winrate"] == 50.0
    assert recap["recent"][0]["symbol"] == "XRPUSDT"
