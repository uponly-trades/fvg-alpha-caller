import os

# Core symbol set (~97 — Tier 1-2 + new high-volume narratives + top memes).
# Tier 3 gaming/metaverse + low-liquidity tier-2 + lesser memes moved to
# EXTENDED to lower cold-start REST weight + reduce ongoing rate-limit pressure
# (was 127 → 97, ~24% drop).
# Re-enable extended set: EXTENDED_SYMBOLS_ENABLED=1
_CORE_SYMBOLS = [
    # --- Tier 1: BTC / ETH / Major L1 ---
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "TRXUSDT", "AVAXUSDT", "LINKUSDT",
    "DOTUSDT", "NEARUSDT", "UNIUSDT", "LTCUSDT", "BCHUSDT",
    "ETCUSDT", "XLMUSDT", "ATOMUSDT", "ICPUSDT", "APTUSDT",
    "FILUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT",
    "SEIUSDT", "TIAUSDT", "PYTHUSDT", "WLDUSDT", "STRKUSDT",
    # --- Tier 2: DeFi blue chips (liquid only) ---
    "DYDXUSDT", "AAVEUSDT", "PENDLEUSDT", "COMPUSDT", "CRVUSDT",
    "YFIUSDT", "SNXUSDT", "RUNEUSDT",
    "LDOUSDT", "GRTUSDT", "TAOUSDT", "ENSUSDT",
    "JUPUSDT",
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
    # Cross-chain / Infra (top liquidity only)
    "AXLUSDT", "WUSDT", "ZROUSDT", "ZKUSDT",
    "ZETAUSDT", "DYMUSDT", "MOVEUSDT",
    # High-volume memes (liquid, great for FVG)
    "1000PEPEUSDT", "1000SHIBUSDT", "1000BONKUSDT", "1000FLOKIUSDT",
    "WIFUSDT", "NOTUSDT",
]

_EXTENDED_SYMBOLS = [
    # --- Tier 2 mid (less liquid) ---
    "SUSHIUSDT", "1INCHUSDT", "STGUSDT", "LQTYUSDT", "SSVUSDT",
    "WOOUSDT", "APEUSDT",
    # --- Tier 3: Gaming / Metaverse (declining liquidity 2026) ---
    "SANDUSDT", "MANAUSDT", "AXSUSDT", "GALAUSDT", "CHZUSDT",
    "ENJUSDT", "IMXUSDT", "FLOWUSDT", "ALICEUSDT", "GMTUSDT",
    "SPELLUSDT", "STXUSDT", "JASMYUSDT", "RNDRUSDT", "FETUSDT",
    # --- Lesser memes ---
    "PNUTUSDT", "PENGUUSDT", "ACTUSDT", "DOGSUSDT", "HMSTRUSDT",
    "POPCATUSDT",
    # --- Lesser cross-chain / infra ---
    "CFXUSDT", "SIGNUSDT", "INITUSDT",
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

# Postgres — fallback to the local `postgres` service in docker-compose.
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
MODEL_ENABLED = os.environ.get("MODEL_ENABLED", "true").lower() == "true"

# v2 detection params
V2_TRIGGER_TFS = ["15m"]                                    # bullish/bearish FVG touch on these
V2_HTF_TFS = ["30m", "1h", "2h", "4h"]                      # confluence sources
V2_HTF_WEIGHTS = {"30m": 1, "1h": 1, "2h": 2, "4h": 3}      # weighted: HTF lebih lama = score lebih besar; max 7
V2_HTF_MIN_SCORE = int(os.environ.get("V2_HTF_MIN_SCORE", "2"))  # require ≥2 same-direction HTF matches (weighted)
V2_RR = float(os.environ.get("V2_RR", "2.0"))               # display TP = entry ± R×RR
V2_HTF_TOUCH_LOOKBACK = int(os.environ.get("HTF_TOUCH_LOOKBACK", "1"))  # closed-candle window for HTF "fresh touch"
ATR_BUFFER_V2 = float(os.environ.get("ATR_BUFFER_V2", "0.3"))           # SL buffer multiplier
V2_MIN_QUALITY_SCORE = float(os.environ.get("V2_MIN_QUALITY_SCORE", "0.0"))  # 0 = disabled; rely on Zeiierman top-N ranking only

# v2 volume confirmation. Directional imbalance is abs(buy-sell)/(buy+sell),
# and must align with signal direction (long: buy > sell, short: sell > buy).
V2_MIN_VOLUME_SCORE = float(os.environ.get("V2_MIN_VOLUME_SCORE", "1.0"))
V2_MIN_VOLUME_IMBALANCE = float(os.environ.get("V2_MIN_VOLUME_IMBALANCE", "0.10"))
V2_REQUIRE_DIRECTIONAL_VOLUME = os.environ.get("V2_REQUIRE_DIRECTIONAL_VOLUME", "1") == "1"

# v2 HTF opposite FVG obstacle filter.
# Protects FVG-triggered LONGs from entering into nearby bearish HTF supply,
# and SHORTs from entering into nearby bullish HTF demand.
V2_HTF_OBSTACLE_FILTER_ENABLED = os.environ.get("V2_HTF_OBSTACLE_FILTER_ENABLED", "1") == "1"
V2_HTF_OBSTACLE_TFS = [
    tf.strip()
    for tf in os.environ.get("V2_HTF_OBSTACLE_TFS", "1h,2h,4h").split(",")
    if tf.strip()
]
V2_HTF_OBSTACLE_ATR_BUFFER = float(os.environ.get("V2_HTF_OBSTACLE_ATR_BUFFER", "0.25"))

# v2 entry timing mode. "close" preserves current bar-close behavior; "touch"
# is reserved for live-price wiring after safety filters are proven.
V2_ENTRY_MODE = os.environ.get("V2_ENTRY_MODE", "close").lower()
V2_MIN_TOUCH_DEPTH = float(os.environ.get("V2_MIN_TOUCH_DEPTH", "0.25"))

# v2 retest confirmation. Signals are emitted only after the trigger candle
# enters the FVG and rejects back out in the signal direction.
V2_RETEST_ENABLED = os.environ.get("V2_RETEST_ENABLED", "1") == "1"
V2_RETEST_MIN_DEPTH = float(os.environ.get("V2_RETEST_MIN_DEPTH", str(V2_MIN_TOUCH_DEPTH)))
V2_RETEST_MAX_DEPTH = float(os.environ.get("V2_RETEST_MAX_DEPTH", "0.95"))
V2_RETEST_MIN_SCORE = float(os.environ.get("V2_RETEST_MIN_SCORE", "60"))

# v2 Zeiierman-style FVG strength tiers from formation volume and main strength.
V2_MIN_FVG_TIER = os.environ.get("V2_MIN_FVG_TIER", "normal").lower()
V2_NORMAL_VOLUME_SCORE = float(os.environ.get("V2_NORMAL_VOLUME_SCORE", "1.1"))
V2_NORMAL_VOLUME_IMBALANCE = float(os.environ.get("V2_NORMAL_VOLUME_IMBALANCE", "0.10"))
V2_NORMAL_MAIN_STRENGTH = int(os.environ.get("V2_NORMAL_MAIN_STRENGTH", "50"))
V2_STRONG_VOLUME_SCORE = float(os.environ.get("V2_STRONG_VOLUME_SCORE", "1.5"))
V2_STRONG_VOLUME_IMBALANCE = float(os.environ.get("V2_STRONG_VOLUME_IMBALANCE", "0.20"))
V2_STRONG_MAIN_STRENGTH = int(os.environ.get("V2_STRONG_MAIN_STRENGTH", "70"))

# Executor preflight: skip trades whose required margin would consume too much free/equity.
TRADE_MARGIN_USAGE_CAP = float(os.environ.get("TRADE_MARGIN_USAGE_CAP", "0.70"))

# v2 trail
V2_TRAIL_ATR_BUFFER = ATR_BUFFER_V2  # alias — trail uses same buffer

# v2 throttle (mitigate higher alert volume without the optional model gate)
V2_COOLDOWN_SEC = int(os.environ.get("V2_COOLDOWN_SEC", "1800"))  # 30 minutes

# v2 freshness — drop signals whose triggering bar is older than this many seconds.
# Protects against placing orders on stale price after warmup gaps or lag spikes.
# 0 = disabled.
V2_MAX_SIGNAL_AGE_SEC = int(os.environ.get("V2_MAX_SIGNAL_AGE_SEC", "60"))
