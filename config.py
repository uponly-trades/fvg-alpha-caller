import os

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT",
    "ADAUSDT", "BCHUSDT", "LTCUSDT", "DOGEUSDT", "TRXUSDT",
    "AVAXUSDT", "XMRUSDT", "LINKUSDT", "AAVEUSDT", "NEARUSDT",
    "APTUSDT", "SEIUSDT", "SUIUSDT", "PYTHUSDT", "UNIUSDT",
    "INJUSDT", "ARBUSDT", "OPUSDT", "1000SHIBUSDT", "1000PEPEUSDT",
]

TIMEFRAMES = ["15m", "1h", "4h"]

# Indicators
VOL_MA_LEN   = 20
TREND_EMA_LEN = 50
ATR_LEN       = 14

# Strength weights (same as Pine Script)
GAP_WEIGHT    = 40
VOL_WEIGHT    = 30
TREND_WEIGHT  = 20
CANDLE_WEIGHT = 10

# Alert thresholds
MIN_STRENGTH_TO_ALERT = 70  # only strong FVGs (>=70)

# Polling
POLL_INTERVAL_SEC = 60
KLINES_LIMIT = 100

# Telegram
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

# Binance
BASE_URL = "https://fapi.binance.com"
