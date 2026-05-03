import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import websocket_client


def test_binance_futures_websocket_uses_working_futures_host(monkeypatch):
    monkeypatch.setattr(websocket_client, "SYMBOLS", ["BTCUSDT"])
    monkeypatch.setattr(websocket_client, "TIMEFRAMES", ["1m"])

    client = websocket_client.BinanceKlineWS(lambda *_: None)
    url = client._build_url(client._stream_chunks()[0])

    assert url == "wss://fstream.binancefuture.com/stream?streams=btcusdt@kline_1m"
