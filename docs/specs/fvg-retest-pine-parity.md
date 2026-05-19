# FVG Retest Pine Parity Spec

Source of truth: `/Users/joseph/Downloads/fvg retest.txt`.

## Entry
- Timeframe: 15m only.
- LONG is valid only from a bullish FVG retest:
  - prior touch already happened,
  - latest candle touches the zone,
  - `low <= fvg.top`, `low > fvg.bottom`, `close > fvg.top`,
  - mitigation is `> 0` and `<= 0.75`,
  - SuperTrend Recovery is bullish (`stTrend == 1`).
- SHORT is valid only from a bearish FVG retest:
  - prior touch already happened,
  - latest candle touches the zone,
  - `high >= fvg.bottom`, `high < fvg.top`, `close < fvg.bottom`,
  - mitigation is `> 0` and `<= 0.75`,
  - SuperTrend Recovery is bearish (`stTrend == -1`).
- Touch-only, HTF confluence, volume tier, quality score, regime, and TP magnet filters are not entry gates.

## Trade Plan / Exit
- On signal open, Pine sets `slPrice = stBand`.
- Every bar while open, Pine updates `tr.sl := stBand`.
- There is no fixed TP order in the Pine trade tracker. The trade closes when price touches the SuperTrend band:
  - LONG exit when `low <= stBand`.
  - SHORT exit when `high >= stBand`.
- The displayed result is the best favorable move if positive, otherwise the SuperTrend exit percent.

## Implementation Mapping
- `strategy_v2.py` must emit `entry`, `sl`, `tp1`, `tp2`, and metadata from the SuperTrend band, not structural SL / TP magnets.
- `signal_decisions.fvg_data` must include SuperTrend trend/band so live rows are auditable.
- `trade_executor` must place entry and SuperTrend-band stop only. It must not place TP1/TP2 orders for `v2_fvg_retest` Pine-parity signals.
- Runtime trail replaces the stop from `supertrend_state(symbol, 15m)` only. Mark price may only nudge an immediately-invalid Binance trigger; it is not the SuperTrend band source.
- Pine-parity trades are marked `user_trades.exit_mode = supertrend_band` so resume/reconcile/telegram do not infer behavior from decision id strings.
- Runtime trail must not use R-ladder or TP1 breakeven rules for Pine-parity signals.
