import os

# Core symbol set (~120 — Tier 1-3 + new high-volume narratives + memes).
# Tier 4-5 + smaller infra moved to EXTENDED_SYMBOLS to drop cold-start REST
# weight burst from ~1025 to ~600 (well under 75% of 2400/min Binance cap).
# Re-enable extended set: EXTENDED_SYMBOLS_ENABLED=1
_CORE_SYMBOLS = [
    # --- Tier 1: BTC / ETH / Major L1 ---
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "TRXUSDT", "AVAXUSDT", "LINKUSDT",
    "DOTUSDT", "NEARUSDT", "UNIUSDT", "LTCUSDT", "BCHUSDT",
    "ETCUSDT", "XLMUSDT", "ATOMUSDT", "ICPUSDT", "APTUSDT",
    "FILUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT",
    "SEIUSDT", "TIAUSDT", "PYTHUSDT", "WLDUSDT", "STRKUSDT",
    # --- Tier 2: DeFi blue chips ---
    "DYDXUSDT", "AAVEUSDT", "PENDLEUSDT", "COMPUSDT", "CRVUSDT",
    "YFIUSDT", "SNXUSDT", "RUNEUSDT", "SUSHIUSDT", "1INCHUSDT",
    "LDOUSDT", "GRTUSDT", "TAOUSDT", "ENSUSDT", "STGUSDT",
    "LQTYUSDT", "SSVUSDT", "JUPUSDT", "WOOUSDT", "APEUSDT",
    # --- Tier 3: Gaming / Metaverse ---
    "SANDUSDT", "MANAUSDT", "AXSUSDT", "GALAUSDT", "CHZUSDT",
    "ENJUSDT", "IMXUSDT", "FLOWUSDT", "ALICEUSDT", "GMTUSDT",
    "SPELLUSDT", "STXUSDT", "JASMYUSDT", "RNDRUSDT", "FETUSDT",
    # --- High-volume narratives (added 2026-05-05) ---
    # Privacy / PoW
    "ZECUSDT", "DASHUSDT", "XMRUSDT",
    # Major new L1/L2
    "TONUSDT", "HYPEUSDT", "POLUSDT", "ALGOUSDT",
    "EGLDUSDT", "XTZUSDT", "KASUSDT", "KSMUSDT", "ZENUSDT",
    # RWA / DeFi new wave
    "ENAUSDT", "ONDOUSDT", "MANTRAUSDT", "MORPHOUSDT", "AEROUSDT",
    "SYRUPUSDT", "PLUMEUSDT", "HUMAUSDT", "SOLVUSDT", "RSRUSDT",
    # Restaking / LRT
    "EIGENUSDT", "ETHFIUSDT", "JTOUSDT", "BBUSDT", "KERNELUSDT",
    # AI / GPU / Data
    "VIRTUALUSDT", "RENDERUSDT", "GRASSUSDT", "ATHUSDT", "AIXBTUSDT",
    "KAITOUSDT", "TRBUSDT", "IOUSDT", "VANAUSDT", "0GUSDT",
    # Ordinals / BTC ecosystem
    "ORDIUSDT", "1000SATSUSDT", "BLURUSDT",
    # Cross-chain / Infra
    "AXLUSDT", "WUSDT", "ZROUSDT", "ZKUSDT", "CFXUSDT",
    "SIGNUSDT", "ZETAUSDT", "DYMUSDT", "MOVEUSDT", "INITUSDT",
    # High-volume memes (liquid, great for FVG)
    "1000PEPEUSDT", "1000SHIBUSDT", "1000BONKUSDT", "1000FLOKIUSDT",
    "WIFUSDT", "PNUTUSDT", "PENGUUSDT", "NOTUSDT", "ACTUSDT",
    "DOGSUSDT", "HMSTRUSDT", "POPCATUSDT",
]

_EXTENDED_SYMBOLS = [
    # --- Tier 4: Infrastructure / Oracles / Storage ---
    "THETAUSDT", "MASKUSDT", "ARUSDT", "LPTUSDT", "RLCUSDT",
    "BANDUSDT", "KNCUSDT", "BATUSDT", "BELUSDT", "CTSIUSDT",
    "API3USDT", "DUSKUSDT", "OGNUSDT", "PEOPLEUSDT", "ROSEUSDT",
    # --- Tier 5: Older altcoins (still liquid) ---
    "HBARUSDT", "VETUSDT", "NEOUSDT", "IOTAUSDT", "ZILUSDT",
    "ONTUSDT", "QTUMUSDT", "CELRUSDT", "HOTUSDT", "ONEUSDT",
    "MTLUSDT", "GTCUSDT", "IOTXUSDT", "ATAUSDT", "C98USDT",
    "SKLUSDT", "COTIUSDT", "CHRUSDT", "ACHUSDT", "IDUSDT",
    # DeFi mid-cap
    "CAKEUSDT", "KAVAUSDT", "QNTUSDT", "SUPERUSDT", "MAVUSDT",
    "DEXEUSDT", "DRIFTUSDT", "ARKMUSDT", "THEUSDT", "EULUSDT",
    "NILUSDT", "JSTUSDT", "AKTUSDT", "LITUSDT", "MINAUSDT",
    # Smaller infra / compute
    "METISUSDT", "SKYUSDT", "MOVRUSDT", "ARPAUSDT", "PHAUSDT",
    "SCRTUSDT", "IRYSUSDT", "TNSRUSDT", "YGGUSDT", "CYBERUSDT",
    "SAGAUSDT", "EDUUSDT", "ALTUSDT", "SXTUSDT", "ANIMEUSDT",
    "HIGHUSDT", "BERAUSDT", "SUSDT",
    # NFT / Gaming new wave
    "MEUSDT", "ZORAUSDT", "SUNUSDT", "LUNA2USDT", "SPXUSDT",
]

EXTENDED_SYMBOLS_ENABLED = os.environ.get("EXTENDED_SYMBOLS_ENABLED", "0") == "1"
SYMBOLS = _CORE_SYMBOLS + (_EXTENDED_SYMBOLS if EXTENDED_SYMBOLS_ENABLED else [])

TIMEFRAMES = ["15m", "30m", "1h", "2h", "4h"]

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

# Regime bands (deviation from EMA)
DOM_NEUTRAL_BAND = 0.0015   # 0.15%
BTC_NEUTRAL_BAND = 0.0020   # 0.20%

# Confirmation filters
VOL_SPIKE_MED = 1.5
VOL_SPIKE_HIGH = 2.0
DISPLACEMENT_BODY_PCT = 65.0
MIN_CONFIRM_SCORE_ALERT = 35

# Invalidation
INVALID_ATR_BUFFER = 0.15
INVALID_LOOKAHEAD_BARS = 6

# Polling
POLL_INTERVAL_SEC = 30
KLINES_LIMIT = 100

# Telegram
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

# Postgres — fallback ke fvg-postgres container (same Coolify network)
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://fvg:f18bbdd5a18785da8d18d8d92965defc@fvg-postgres:5432/fvg",
)

# Binance
BASE_URL = "https://fapi.binance.com"


# =====================================================
# v2 Strategy (Multi-TF FVG Touch Confluence)
# =====================================================
STRATEGY_VERSION = os.environ.get("STRATEGY_VERSION", "v1")  # "v1" or "v2"
KRONOS_ENABLED = os.environ.get("KRONOS_ENABLED", "true").lower() == "true"

# v2 detection params
V2_TRIGGER_TFS = ["15m"]                                    # bullish/bearish FVG touch on these
V2_HTF_TFS = ["30m", "1h", "2h", "4h"]                      # confluence sources
V2_HTF_WEIGHTS = {"30m": 1, "1h": 1, "2h": 1, "4h": 1}      # flat +1 each, max 4
V2_HTF_MIN_SCORE = int(os.environ.get("V2_HTF_MIN_SCORE", "1"))  # threshold ≥1
V2_RR = float(os.environ.get("V2_RR", "2.0"))               # display TP = entry ± R×RR
V2_HTF_TOUCH_LOOKBACK = int(os.environ.get("HTF_TOUCH_LOOKBACK", "1"))  # closed-candle window for "currently touched"
ATR_BUFFER_V2 = float(os.environ.get("ATR_BUFFER_V2", "0.3"))           # SL buffer multiplier

# v2 trail
V2_TRAIL_ATR_BUFFER = ATR_BUFFER_V2  # alias — trail uses same buffer

# v2 throttle (mitigate higher alert volume from no-Kronos)
V2_COOLDOWN_SEC = int(os.environ.get("V2_COOLDOWN_SEC", "1800"))  # 30 minutes

# v2 freshness — drop signals whose triggering bar is older than this many seconds.
# Protects against placing orders on stale price after warmup gaps or lag spikes.
# 0 = disabled.
V2_MAX_SIGNAL_AGE_SEC = int(os.environ.get("V2_MAX_SIGNAL_AGE_SEC", "60"))
