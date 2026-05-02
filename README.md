# FVG Alpha Caller

Self-hosted FVG (Fair Value Gap) detector using Binance Futures data.

## What it does

- Polls Binance Futures klines every 60s for 25 symbols × 3 timeframes
- Detects bullish/bearish FVGs on bar close
- Calculates strength score (gap, volume, trend, candle body)
- Tracks mitigation (fill %) and sends alerts to Telegram

## Symbols

25 USDT-M perpetual futures: BTC, ETH, BNB, XRP, SOL, ADA, BCH, LTC, DOGE, TRX, AVAX, XMR, LINK, AAVE, NEAR, APT, ONDO, SUI, TON, UNI, TAO, ARB, OP, 1000SHIB, 1000PEPE

## Timeframes

15m, 1h, 4h

## Alerts

- 🟢 New Bullish FVG
- 🔴 New Bearish FVG
- ⚪ FVG Fully Mitigated

## Env vars

| Variable | Required |
|----------|----------|
| `TELEGRAM_BOT_TOKEN` | Yes |
| `TELEGRAM_CHAT_ID` | Yes |

## Deploy

```bash
docker build -t fvg-alpha-caller .
docker run --env-file .env fvg-alpha-caller
```

Or with docker-compose:
```bash
docker compose up -d
```
