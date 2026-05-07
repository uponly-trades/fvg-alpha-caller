#!/bin/bash
# fvg-alpha-caller scanner watchdog
#
# Mitigates a pre-existing reconnect-stuck bug where the WS layer reports
# "WS connected" after a 1011 keepalive timeout but never resumes message
# handling. Symptom: zero "WS bar closed" log lines despite live WS.
#
# Strategy: every N minutes (driven by systemd timer), count "WS bar closed"
# events in the last 17min log window. 17min covers one 15m TF candle close
# boundary — if zero events, the scanner is stuck and the container is
# restarted.
#
# Override the container name with FVG_CONTAINER env var if needed.
set -e

CONTAINER="${FVG_CONTAINER:-fvg-fvg-alpha-caller-1}"
WINDOW="${FVG_WATCHDOG_WINDOW:-17m}"
LOG_TAG="fvg-watchdog"

COUNT=$(docker logs --since "$WINDOW" "$CONTAINER" 2>&1 | grep -c "WS bar closed" || true)

if [ "$COUNT" -eq 0 ]; then
    logger -t "$LOG_TAG" "STUCK: 0 bar closes in $WINDOW — restarting $CONTAINER"
    docker restart "$CONTAINER" >/dev/null
    logger -t "$LOG_TAG" "restart issued"
else
    logger -t "$LOG_TAG" "OK: $COUNT bar closes in $WINDOW"
fi
