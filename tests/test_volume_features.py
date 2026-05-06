import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from feature_extractor import extract_tf_features
from scripts import backfill_features
import trade_combo


@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True


def make_bars(volumes):
    return [
        Bar(
            open_time=i * 60_000,
            open=100 + i * 0.1,
            high=101 + i * 0.1,
            low=99 + i * 0.1,
            close=100 + i * 0.1,
            volume=float(v),
            is_closed=True,
        )
        for i, v in enumerate(volumes)
    ]


def test_volume_spike_pct_uses_prior_20_closed_average_not_previous_candle():
    bars = make_bars([100] * 29 + [30])

    features = extract_tf_features(bars, "15m")

    assert features["vol_change_pct"] == -70.0
    assert features["vol_spike_pct"] == -70.0
    assert features["vol_spike_ratio"] == 0.3


def test_v2_long_uses_stable_volume_spike_not_noisy_previous_candle_change():
    bars_by_tf = {
        "4h": make_bars([1000] * 30),
        "15m": make_bars([100] * 29 + [30]),
    }

    result = trade_combo._v2_long_decision(bars_by_tf)

    assert result["valid"] is False
    assert "15m_vol_spike" in result["reason"]



def test_backfill_fetches_candles_before_decision_time(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    def fake_get(url, params, timeout):
        captured.update(params)
        return Response()

    monkeypatch.setattr(backfill_features.requests, "get", fake_get)

    backfill_features.fetch_klines_at("BTCUSDT", "15m", end_ms=1_778_072_400_000)

    assert captured["endTime"] == 1_778_072_399_999
