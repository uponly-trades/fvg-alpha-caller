import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_SIM_TRADES_PATH = Path("/app/data/sim_trades.json")


class SimTradeStore:
    def __init__(self, path: Path = DEFAULT_SIM_TRADES_PATH):
        self.path = Path(path)

    def load(self) -> List[Dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data

    def save(self, records: List[Dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, sort_keys=True)
        tmp_path.replace(self.path)

    def add_trade(self, zone, setup, created_at: int) -> bool:
        trade = getattr(setup, "trade", None)
        if trade is None or not getattr(setup, "valid", False):
            return False

        trade_id = f"{zone.symbol}-{zone.tf}-{int(created_at)}"
        records = self.load()
        if any(record.get("id") == trade_id for record in records):
            return False

        created_dt = datetime.fromtimestamp(int(created_at) / 1000, tz=timezone.utc)
        records.append({
            "id": trade_id,
            "date": created_dt.date().isoformat(),
            "symbol": zone.symbol,
            "mode": setup.mode,
            "tf": zone.tf,
            "direction": trade.direction,
            "entry": trade.entry,
            "sl": trade.sl,
            "tp1": trade.tp1,
            "tp2": trade.tp2,
            "status": "open",
            "created_at": int(created_at),
            "closed_at": None,
            "reason": setup.reason,
        })
        self.save(records)
        return True

    def update_open_trades(self, symbol: str, bar) -> int:
        records = self.load()
        updated = 0
        for record in records:
            if record.get("symbol") != symbol:
                continue
            if record.get("status") not in {"open", "tp1_hit"}:
                continue
            new_status = _next_status(record, bar)
            if new_status and new_status != record["status"]:
                record["status"] = new_status
                if new_status in {"win", "loss"}:
                    record["closed_at"] = int(bar.open_time)
                updated += 1
        if updated:
            self.save(records)
        return updated

    def daily_recap(self, date: Optional[str] = None) -> Dict:
        if date is None:
            date = datetime.now(timezone.utc).date().isoformat()
        records = [record for record in self.load() if record.get("date") == date]
        open_count = sum(1 for record in records if record.get("status") == "open")
        tp1_count = sum(1 for record in records if record.get("status") == "tp1_hit")
        win_count = sum(1 for record in records if record.get("status") == "win")
        loss_count = sum(1 for record in records if record.get("status") == "loss")
        closed = win_count + loss_count
        winrate = round((win_count / closed) * 100, 1) if closed else 0.0
        recent = sorted(records, key=lambda record: record.get("created_at", 0), reverse=True)[:5]
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
