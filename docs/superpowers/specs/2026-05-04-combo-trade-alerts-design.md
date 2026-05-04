# Combo Trade Alerts Design

## Goal

Add alert-only trade setup logic on top of FVG events. The bot should classify each FVG event into scalping, intraday, or swing modes, validate StochRSI combo conditions, produce a simple Telegram trade plan with entry/SL/TP levels, store simulated trade outcomes, and send scheduled recap messages.

## Scope

This feature does not place real orders. It only sends Telegram alerts and stores paper/simulation records for later review.

In scope:
- Trade mode classification: scalping, intraday, swing.
- Combo validation using native timeframe StochRSI values.
- Skip logic for unclear or poor-risk setups.
- Trade plan generation with entry, SL, TP1, TP2.
- Simulated trade storage and status updates.
- Session recap messages: subuh, pagi, siang, sore, malam.
- Reduced Telegram alert text.
- Chart overlays for entry, SL, TP1, and TP2.

Out of scope:
- Auto execution on exchange.
- Pending sniper limit orders.
- Dashboard UI.
- Advanced portfolio/risk sizing.
- Full 250-symbol expansion in the same phase unless logs prove capacity is safe.

## Key Design Decision

FVG remains the trigger. The combo engine does not scan independently. It only evaluates a setup when an FVG event already exists: new FVG, approach, or touch.

The first implementation uses current price as the simulated entry when the setup is valid. It does not wait for a limit order at the FVG edge. This keeps behavior simple and produces enough data to evaluate whether sniper entries should be added later.

## Trade Modes

### Scalping

Purpose: fast setups.

- Setup FVG timeframes: `15m`, `30m`
- Bias timeframe: `1h`
- Combo indicators: `15m`, `30m`, `1h`

### Intraday

Purpose: same-day trades.

- Setup FVG timeframes: `1h`, `2h`
- Bias timeframe: `4h`
- Combo indicators: `30m`, `1h`, `2h`, `4h`

### Swing

Purpose: slower higher-timeframe setups.

- Setup FVG timeframes: `2h`, `4h`
- Bias timeframe: `4h`
- Combo indicators: `1h`, `2h`, `4h`

This is a light swing mode because the bot does not currently track `1d`. Daily timeframe can be added later if needed.

## Combo Validation

### Long Setup

A long setup is valid when:
- FVG direction is bullish.
- Required combo timeframes are mostly oversold or recovering.
- Price is not too far from the FVG zone.
- FVG strength is above the existing alert threshold.

Oversold/recovering means:
- StochRSI K and D are at or below 30, or
- K crosses above D from the lower zone.

### Short Setup

A short setup is valid when:
- FVG direction is bearish.
- Required combo timeframes are mostly overbought or rolling over.
- Price is not too far from the FVG zone.
- FVG strength is above the existing alert threshold.

Overbought/rolling over means:
- StochRSI K and D are at or above 70, or
- K crosses below D from the upper zone.

### Skip Cases

The trade setup should be skipped when:
- Combo states are mixed.
- Price is too far from the FVG zone.
- FVG strength is weak.
- Indicator data is missing for required combo timeframes.

The FVG alert can still be sent, but it should be labeled as a skipped trade setup rather than a valid trade setup.

## Risk Plan

Entry:
- Use current price at the time of the valid alert.

Stop loss:
- Long: below FVG bottom with a small ATR buffer.
- Short: above FVG top with a small ATR buffer.

Take profit:
- TP1 = 1R.
- TP2 = 2R.

Example long:
- Entry = 1.0000
- SL = 0.9800
- Risk = 0.0200
- TP1 = 1.0200
- TP2 = 1.0400

Example short:
- Entry = 1.0000
- SL = 1.0200
- Risk = 0.0200
- TP1 = 0.9800
- TP2 = 0.9600

If risk is zero or invalid, skip the trade setup.

## Trade Status Labels

Valid labels:
- `LONG VALID`
- `SHORT VALID`
- `SKIP: MIXED COMBO`
- `SKIP: FAR FROM FVG`
- `SKIP: WEAK FVG`
- `SKIP: MISSING DATA`
- `SKIP: INVALID RISK`

Telegram title format:

```text
LONG VALID - BULLISH FVG | IMXUSDT | 15m
SHORT VALID - BEARISH FVG | BTCUSDT | 1h
SKIP: MIXED COMBO - BULLISH FVG | SOLUSDT | 30m
```

## Reduced Telegram Alert Content

New/approach/touch trade alerts should focus on the important fields only:

- Title.
- Entry.
- SL.
- TP1.
- TP2.
- RR.
- Mode.
- FVG zone.
- Strength.
- Reason or skip reason.
- TradingView link.

Remove long indicator context text from Telegram messages.

## Chart Overlay

Charts should show:
- FVG zone.
- Entry line/block.
- SL line/block.
- TP1 line/block.
- TP2 line/block.

Use distinct colors:
- Entry: blue.
- SL: red.
- TP1: green.
- TP2: darker green.

These overlays should only render when a trade plan exists. Skipped setups may show FVG zone only, unless useful entry/risk values are still available.

## Simulation Storage

Store simulated trades in:

```text
/app/data/sim_trades.json
```

Each record should include:

```json
{
  "id": "IMXUSDT-15m-1777899600000",
  "date": "2026-05-04",
  "symbol": "IMXUSDT",
  "mode": "scalping",
  "tf": "15m",
  "direction": "long",
  "entry": 1.0,
  "sl": 0.98,
  "tp1": 1.02,
  "tp2": 1.04,
  "status": "open",
  "created_at": 1777899600000,
  "closed_at": null,
  "reason": "bullish FVG with oversold/recovering combo"
}
```

Valid simulation statuses:
- `open`
- `tp1_hit`
- `win`
- `loss`

Status update rules:
- Long loss: candle low touches or breaks SL.
- Long TP1: candle high touches or breaks TP1.
- Long win: candle high touches or breaks TP2.
- Short loss: candle high touches or breaks SL.
- Short TP1: candle low touches or breaks TP1.
- Short win: candle low touches or breaks TP2.

When SL and TP are both touched in the same candle, use conservative ordering: count SL first.

## Recap Messages

Send recap messages five times per day:
- Subuh.
- Pagi.
- Siang.
- Sore.
- Malam.

The recap should include today’s simulation summary:

```text
Trade Recap — Siang

Open: 4
TP1: 2
Win TP2: 1
Loss: 1
Closed Winrate: 50.0%

Recent:
LONG VALID - BTCUSDT 15m
Entry 1.0000 | SL 0.9800 | TP1 1.0200 | TP2 1.0400
Status: TP1
```

## Symbol Expansion

Do not jump straight to 250 symbols in the first implementation if it risks rate limits.

Current system with 100 symbols and 5 timeframes uses 500 streams. 250 symbols with 5 timeframes would use 1250 streams and more REST warm-up calls. The safe approach is:
- Keep current list for this feature.
- Add a configurable symbol cap later.
- Expand gradually to 150, then 200, then 250 only after logs show healthy warm-up and stable WebSocket connections.

## Implementation Units

Suggested new modules:

- `trade_combo.py`
  - Computes mode, combo state, skip reasons, and trade plan.

- `sim_trades.py`
  - Stores simulated trades.
  - Updates open trades from new candles.
  - Builds recap summaries.

Modify existing modules:

- `main.py`
  - Evaluate combo plans on FVG events.
  - Store valid simulation trades.
  - Update open simulations on each closed candle.
  - Trigger recap sending at session times.

- `telegram.py`
  - Reduce alert text.
  - Add recap sender.

- `chart_generator.py`
  - Accept optional trade plan levels.
  - Draw entry/SL/TP blocks.

- `tests/test_trade_combo.py`
  - Validate mode classification, combo logic, skip reasons, and risk math.

- `tests/test_sim_trades.py`
  - Validate persistence and win/loss updates.

- `tests/test_indicator_context.py`
  - Update chart and Telegram assertions.

## Validation

Automated checks:
- Unit tests for combo validation.
- Unit tests for risk plan math.
- Unit tests for simulation status updates.
- Existing chart generation tests must still return PNG.
- Existing Telegram tests must assert reduced message content.

Runtime checks:
- Deploy to Coolify.
- Confirm container starts.
- Confirm WebSocket warm-up completes.
- Confirm no `Chart generation failed`.
- Confirm no Telegram send errors.
- Confirm sim trade file is created after first valid trade.
