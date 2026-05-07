# Scanner Watchdog

Mitigation for pre-existing WS reconnect-stuck bug.

## Bug

After Binance WS sends `1011 keepalive ping timeout`, the WS layer in
`websocket_client.py` reports "WS connected" on reconnect but never resumes
message handling — no resubscribe, no `_handle_message` restart. Symptom:
zero "WS bar closed" log lines despite live WS. Container restart fixes it.

Root-cause fix is pending. Watchdog is a stopgap.

## Mitigation

Every 5 minutes, count "WS bar closed" events in the last 17min log window.
17min covers one 15m TF candle close boundary. Zero events → restart.

## Install (on host)

```bash
# As root on the VPS
cd /opt/fvg/repo
install -m 0755 scripts/watchdog.sh /usr/local/bin/fvg-watchdog.sh
install -m 0644 deploy/systemd/fvg-watchdog.service /etc/systemd/system/
install -m 0644 deploy/systemd/fvg-watchdog.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now fvg-watchdog.timer
```

## Verify

```bash
systemctl list-timers fvg-watchdog.timer
journalctl -t fvg-watchdog -n 20
```

Expected line every 5min:
```
fvg-watchdog: OK: NN bar closes in 17m
```

On stuck event:
```
fvg-watchdog: STUCK: 0 bar closes in 17m — restarting fvg-fvg-alpha-caller-1
fvg-watchdog: restart issued
```

## Config

Override via systemd drop-in if container name differs:

```bash
mkdir -p /etc/systemd/system/fvg-watchdog.service.d
cat > /etc/systemd/system/fvg-watchdog.service.d/env.conf <<EOF
[Service]
Environment=FVG_CONTAINER=my-other-name
Environment=FVG_WATCHDOG_WINDOW=17m
EOF
systemctl daemon-reload
```
