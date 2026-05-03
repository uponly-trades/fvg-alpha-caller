# Indicator Context Alerts Design

## Goal

Add Binance-style technical context to FVG alerts so each alert shows StochRSI, MAStochRSI, RSI(7), KDJ, and long/short ratio for `15m`, `1h`, and `4h`.

## Current Context

`fvg-alpha-caller` already receives closed Binance Futures klines through `BinanceKlineWS`, stores per-symbol/timeframe buffers in `FVGTracker`, and sends Telegram alerts with an optional chart image. Current alerts include FVG strength, RSI(14), ATR, BTCDOM/BTC trend, confirmation metrics, 24h change, and TradingView links.

The current chart image only shows candles, EMA20/EMA50, FVG zone, and RSI(14). It does not show the Binance mobile indicators from the screenshot.

## Target User Experience

Every new FVG, approach, and touch alert should include an indicator context block like:

```text
📊 Indicator Context
15m: StochRSI 15.3/6.1 ↑ bull_cross | RSI7 40.8 | KDJ K27 D28 J25 bear | LS L72.2/S27.8
1h : StochRSI ...
4h : StochRSI ...
```

Chart images should include visual panels for the alert timeframe:

1. Candles + EMA20/EMA50 + FVG zone
2. StochRSI + MAStochRSI
3. RSI(7)
4. KDJ K/D/J

Long/short ratio is shown in text only because it is external market-position data, not derived from the local kline bars.

## Indicator Definitions

### StochRSI and MAStochRSI

- RSI length: `14`
- Stochastic length: `14`
- `%K` smoothing: `3`
- `%D` smoothing: `3`
- `STOCHRSI` displayed as smoothed `%K`
- `MASTOCHRSI` displayed as smoothed `%D`

Cross status:

- `bull_cross`: previous `%K <= %D` and current `%K > %D`
- `bear_cross`: previous `%K >= %D` and current `%K < %D`
- `bull`: current `%K > %D` without fresh cross
- `bear`: current `%K < %D` without fresh cross
- `neutral`: missing or equal values

### RSI(7)

Use Wilder-style RSI length `7`, matching common exchange chart behavior closely enough for alert context.

### KDJ

- Lookback: `9`
- K smoothing: `3`
- D smoothing: `3`
- `RSV = (close - lowestLow) / (highestHigh - lowestLow) * 100`
- `K = 2/3 * previousK + 1/3 * RSV`, seeded at `50`
- `D = 2/3 * previousD + 1/3 * K`, seeded at `50`
- `J = 3K - 2D`

Cross status uses K/D with the same rules as StochRSI.

### Long/Short Ratio

Use Binance Futures Top Trader Long/Short Position Ratio endpoint:

```text
GET /futures/data/topLongShortPositionRatio
symbol=<symbol>
period=<15m|1h|4h>
limit=1
```

Display:

- `longAccount` as long percentage
- `shortAccount` as short percentage
- fallback to `n/a` if unavailable

Cache LS results per `(symbol, timeframe)` for at least 60 seconds to avoid extra API pressure.

## Architecture

Create a focused module `indicator_context.py` for indicator calculations and API-backed LS ratio fetching. It should expose:

```python
def build_indicator_context(symbol: str, buffers: dict) -> dict[str, IndicatorContext]
```

`buffers` is `FVGTracker.buffers`, keyed by `(symbol, tf)`. The function returns contexts for `15m`, `1h`, and `4h` only.

`FVGZone` gets a lightweight `indicator_context` field containing a preformatted multiline string. Telegram rendering reads this string and appends it to new FVG, approach, and touch alerts.

`chart_generator.generate_chart()` computes and plots StochRSI, RSI(7), and KDJ for the chart timeframe from the passed `bars`. It does not call external APIs.

## Data Flow

1. WebSocket closes a candle and calls `AlphaCaller._on_bar_close(symbol, tf, bars)`.
2. `FVGTracker.update_buffer(symbol, tf, bars)` stores the latest bars.
3. Before sending a new FVG, approach, or touch alert, `AlphaCaller` builds indicator context for the alert symbol across `15m`, `1h`, and `4h`.
4. The preformatted context is attached to the zone before Telegram send.
5. Telegram alert text includes the context block.
6. Chart generation adds indicator panels for the alert timeframe.

## Failure Behavior

- If a timeframe buffer is missing or too short, show that timeframe as `n/a`.
- If Binance LS API fails or times out, show `LS n/a` and keep sending the alert.
- Indicator calculation must never block an alert permanently.
- Chart generation failure keeps existing behavior: send text-only alert.

## Scope

In scope:

- Indicator calculations for StochRSI, MAStochRSI, RSI(7), KDJ.
- Binance top-trader long/short position ratio.
- Telegram text context for `15m`, `1h`, `4h`.
- Chart panels for current alert timeframe.
- Tests for calculation/cross formatting and missing data behavior.
- Deploy to Coolify GitHub-connected app.

Out of scope:

- Replacing FVG scoring with these indicators.
- Alert filtering based on these indicators.
- Matching Binance chart values pixel-perfectly.
- Rendering LS ratio as chart bars.
- Adding MACD/OI from the screenshot.

## Success Criteria

- Alerts include all requested indicator names and values for `15m`, `1h`, and `4h`.
- StochRSI/MAStochRSI and KDJ cross status is clear.
- RSI shown is RSI(7), not only existing RSI(14).
- LS ratio is present per timeframe when Binance API returns data.
- Bot remains one running Coolify container with `restartCount=0` after deploy.
- Existing WebSocket warmup and connection logs remain healthy.

## Self-Review

- Placeholder scan: no TBD/TODO placeholders.
- Internal consistency: text context covers 3 TFs; chart covers alert TF only.
- Scope check: single subsystem, alert context indicators.
- Ambiguity check: formulas, endpoint, fallback behavior, and display format are explicit.
