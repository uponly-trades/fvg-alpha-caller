# Coolify GitHub Source Deployment Design

## Goal

Make GitHub `uponly-trades/fvg-alpha-caller` branch `main` the direct deployment source for Coolify, replacing the current local-path build source `/opt/fvg-alpha-caller`.

## Current Problem

The current Coolify service builds from `/opt/fvg-alpha-caller`. GitHub pushes do not automatically update that directory, so Coolify can redeploy stale code unless someone manually runs `git pull` on the server before redeploying.

## Target Model

Coolify should clone/build directly from the GitHub repository and redeploy from `main`. Runtime environment remains in Coolify.

Required env values:

```text
TELEGRAM_BOT_TOKEN=<existing token>
TELEGRAM_CHAT_ID=-1003534109980
```

## Migration Strategy

1. Create or convert a Coolify application/service that uses GitHub repo `uponly-trades/fvg-alpha-caller` on branch `main`.
2. Preserve current runtime env values exactly.
3. Keep the current local-path service running until the GitHub-connected service is verified healthy.
4. Deploy the GitHub-connected service.
5. Verify production logs show:

```text
Alpha Caller (Binance WS + REST fallback)
WS warm-up complete
WS connected | conn=0 streams=100
WS connected | conn=1 streams=100
WS connected | conn=2 streams=100
```

6. After verification, stop/remove the old local-path service/container so only one bot instance remains.

## Rollback

If the GitHub-connected app fails to build or run, keep the existing local-path service as production. Do not stop the old service until the new app is proven healthy.

## Success Criteria

- Coolify deployment source is GitHub `main`, not `/opt/fvg-alpha-caller`.
- Production bot starts from commit on GitHub `main`.
- Telegram env uses numeric channel id `-1003534109980`.
- Only one production bot container remains running.
- WebSocket logs show Binance Futures WS connected after warm-up.
