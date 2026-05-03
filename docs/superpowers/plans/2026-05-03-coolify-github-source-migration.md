# Coolify GitHub Source Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move FVG Alpha Caller production deployment from Coolify local-path builds to direct GitHub `main` source while preserving bot uptime.

**Architecture:** Create a GitHub-connected Coolify application/service using `uponly-trades/fvg-alpha-caller` `main`, copy existing Telegram env values, deploy it in parallel with the current local-path service, verify runtime logs, then retire the old local-path container/service. The old service remains rollback until the new service is healthy.

**Tech Stack:** Coolify API, GitHub repository `uponly-trades/fvg-alpha-caller`, Docker/Coolify service runtime, Python bot logs.

---

## Files and Systems

- Existing Coolify service: `n1hxl7f2x39ecqjths0u6446`
  - Current source: `/opt/fvg-alpha-caller`
  - Current env: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID=-1003534109980`
- GitHub repo: `uponly-trades/fvg-alpha-caller`
  - Branch: `main`
- Server: `root@ssh.uponlytrader.xyz`
- Verification container log patterns:
  - `Alpha Caller (Binance WS + REST fallback)`
  - `WS warm-up complete | buffers=300`
  - `WS connected | conn=0 streams=100`
  - `WS connected | conn=1 streams=100`
  - `WS connected | conn=2 streams=100`

---

### Task 1: Capture Current Production Baseline

**Files:**
- No repository files modified.
- External systems: Coolify API, Docker host.

- [ ] **Step 1: Verify current GitHub `main` head**

Run:

```bash
git -C /Users/joseph/Documents/fvg-alpha-caller status --short
git -C /Users/joseph/Documents/fvg-alpha-caller log --oneline -1
git -C /Users/joseph/Documents/fvg-alpha-caller ls-remote origin main | cut -f1
```

Expected:

```text
status clean or only intentional docs changes
latest commit is current local HEAD
origin/main hash matches local HEAD
```

- [ ] **Step 2: Capture current Coolify service config**

Run:

```bash
python3 - <<'PY'
import json
import urllib.request

req = urllib.request.Request(
    'https://ctrl.uponlytrader.xyz/api/v1/services/n1hxl7f2x39ecqjths0u6446',
    headers={
        'Authorization': 'Bearer 8|Fv0tMK2n7kxWa76ZWonFtFtpXPFIxG56mZU9EpYg60f3fe14',
        'Accept': 'application/json',
        'User-Agent': 'fvg-alpha-caller-migration',
    },
)
with urllib.request.urlopen(req, timeout=30) as resp:
    service = json.loads(resp.read().decode())
print(json.dumps({
    'uuid': service.get('uuid'),
    'name': service.get('name'),
    'docker_compose_raw': service.get('docker_compose_raw'),
    'applications': [(app.get('name'), app.get('status')) for app in service.get('applications', [])],
}, indent=2))
PY
```

Expected:

```text
build: /opt/fvg-alpha-caller
TELEGRAM_CHAT_ID=-1003534109980
application status running:unknown
```

- [ ] **Step 3: Capture current production container state**

Run:

```bash
ssh root@ssh.uponlytrader.xyz "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' | grep 'fvg-alpha-caller' || true"
ssh root@ssh.uponlytrader.xyz "docker inspect fvg-alpha-caller-n1hxl7f2x39ecqjths0u6446 --format 'status={{.State.Status}} restartCount={{.RestartCount}} started={{.State.StartedAt}}'"
```

Expected:

```text
fvg-alpha-caller-n1hxl7f2x39ecqjths0u6446 Up
restartCount=0
old manual container Exited or absent
```

- [ ] **Step 4: Verify bot currently works before migration**

Run:

```bash
ssh root@ssh.uponlytrader.xyz "docker logs --since 20m fvg-alpha-caller-n1hxl7f2x39ecqjths0u6446 2>&1 | grep -E 'Alpha Caller|BinanceKlineWS|WS connected|WS warm-up complete|WS bar closed|REST fallback|ERROR|Traceback' | tail -n 120"
```

Expected:

```text
Alpha Caller (Binance WS + REST fallback)
WS connected lines exist
no Traceback
no repeated REST fallback after the fixed endpoint has been deployed
```

---

### Task 2: Create GitHub-Connected Coolify Candidate

**Files:**
- No repository files modified.
- External systems: Coolify API/UI.

- [ ] **Step 1: Inspect available Coolify application/source API options**

Run:

```bash
python3 - <<'PY'
import urllib.request
for path in [
    '/api/v1/applications',
    '/api/v1/projects',
    '/api/v1/sources',
    '/api/v1/servers',
]:
    req = urllib.request.Request(
        'https://ctrl.uponlytrader.xyz' + path,
        headers={
            'Authorization': 'Bearer 8|Fv0tMK2n7kxWa76ZWonFtFtpXPFIxG56mZU9EpYg60f3fe14',
            'Accept': 'application/json',
            'User-Agent': 'fvg-alpha-caller-migration',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            print(path, resp.status, resp.read().decode()[:1000])
    except Exception as e:
        print(path, type(e).__name__, e)
PY
```

Expected:

```text
At least one endpoint exposes projects/environments/sources needed to create a GitHub-connected app, or output proves API creation is not exposed and Coolify UI/manual step is required.
```

- [ ] **Step 2: If Coolify API supports GitHub app creation, create candidate without stopping old service**

Use the API shape discovered in Step 1. Candidate requirements:

```text
name=fvg-alpha-caller-github
repository=https://github.com/uponly-trades/fvg-alpha-caller
branch=main
build pack/docker compose source=repo Dockerfile/docker-compose as Coolify supports
no public fqdn required
restart=unless-stopped
```

Expected:

```text
New candidate UUID captured.
Old service n1hxl7f2x39ecqjths0u6446 still running.
```

- [ ] **Step 3: If API creation is not safely available, use Coolify UI/manual creation and capture UUID**

Manual settings:

```text
Project: uponly-playground
Environment: production
Application name: fvg-alpha-caller-github
Source: GitHub
Repository: uponly-trades/fvg-alpha-caller
Branch: main
Build type: Dockerfile or Docker Compose according to Coolify's detected repo options
No public domain required
```

Expected:

```text
New candidate service/app visible in Coolify.
Candidate UUID recorded before deploy.
Old service still running.
```

---

### Task 3: Configure Candidate Runtime Env

**Files:**
- No repository files modified.
- External systems: Coolify candidate env.

- [ ] **Step 1: Set candidate env values**

Set exactly:

```text
TELEGRAM_BOT_TOKEN=<same existing token currently in old service>
TELEGRAM_CHAT_ID=-1003534109980
```

Expected:

```text
Candidate env contains numeric Telegram channel id, not @campinaz.
```

- [ ] **Step 2: Verify candidate env through API or container inspect after first deploy**

If candidate has a container name, run:

```bash
ssh root@ssh.uponlytrader.xyz "docker inspect <candidate-container-name> --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E 'TELEGRAM_CHAT_ID|TELEGRAM_BOT_TOKEN'"
```

Expected:

```text
TELEGRAM_CHAT_ID=-1003534109980
TELEGRAM_BOT_TOKEN=<set>
```

---

### Task 4: Deploy Candidate and Verify GitHub Source

**Files:**
- No repository files modified.
- External systems: Coolify candidate, Docker host.

- [ ] **Step 1: Deploy candidate**

Trigger deploy from Coolify UI/API for the new candidate UUID.

Expected:

```text
Candidate container starts without stopping old service.
```

- [ ] **Step 2: Verify candidate is built from GitHub `main` commit**

Run:

```bash
git -C /Users/joseph/Documents/fvg-alpha-caller rev-parse --short HEAD
ssh root@ssh.uponlytrader.xyz "docker exec <candidate-container-name> python - <<'PY'
from pathlib import Path
print(Path('/app/websocket_client.py').read_text().split('BASE_URL = ')[1].splitlines()[0])
PY"
```

Expected:

```text
Candidate code contains fstream.binancefuture.com.
Candidate code contains BinanceKlineWS.
```

- [ ] **Step 3: Verify candidate runtime logs**

Run:

```bash
ssh root@ssh.uponlytrader.xyz "docker logs --since 10m <candidate-container-name> 2>&1 | grep -E 'Alpha Caller|BinanceKlineWS|WS warm-up complete|WS connected|ERROR|Traceback' | tail -n 120"
```

Expected:

```text
Alpha Caller (Binance WS + REST fallback)
BinanceKlineWS starting
WS warm-up complete | buffers=300
WS connected | conn=0 streams=100
WS connected | conn=1 streams=100
WS connected | conn=2 streams=100
no Traceback
```

---

### Task 5: Cut Over to GitHub-Connected Candidate

**Files:**
- No repository files modified.
- External systems: Docker/Coolify.

- [ ] **Step 1: Stop old local-path service only after candidate is healthy**

Run:

```bash
ssh root@ssh.uponlytrader.xyz "docker stop fvg-alpha-caller-n1hxl7f2x39ecqjths0u6446"
```

Expected:

```text
Old local-path container stops.
Candidate container remains Up.
```

- [ ] **Step 2: Verify only one bot container remains running**

Run:

```bash
ssh root@ssh.uponlytrader.xyz "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' | grep 'fvg-alpha-caller' || true"
```

Expected:

```text
Candidate GitHub-connected container Up.
Old local-path container Exited.
Old manual duplicate Exited or absent.
```

- [ ] **Step 3: Verify no duplicate Telegram bot source remains active**

Run:

```bash
ssh root@ssh.uponlytrader.xyz "docker ps --format '{{.Names}}' | grep 'fvg-alpha-caller' | wc -l"
```

Expected:

```text
1
```

---

### Task 6: Confirm Future GitHub Push Deployment Path

**Files:**
- No repository files modified unless Coolify requires a deploy marker commit.
- External systems: GitHub, Coolify.

- [ ] **Step 1: Trigger a no-code deploy test from GitHub-connected source**

Use Coolify redeploy for the candidate. Do not manually run `git pull /opt/fvg-alpha-caller`.

Expected:

```text
Coolify deploy succeeds from GitHub-connected source.
No dependency on /opt/fvg-alpha-caller.
```

- [ ] **Step 2: Verify old local path is no longer deploy source**

Run:

```bash
python3 - <<'PY'
import json
import urllib.request

CANDIDATE_UUID = '<candidate-uuid>'
req = urllib.request.Request(
    f'https://ctrl.uponlytrader.xyz/api/v1/applications/{CANDIDATE_UUID}',
    headers={
        'Authorization': 'Bearer 8|Fv0tMK2n7kxWa76ZWonFtFtpXPFIxG56mZU9EpYg60f3fe14',
        'Accept': 'application/json',
        'User-Agent': 'fvg-alpha-caller-migration',
    },
)
with urllib.request.urlopen(req, timeout=30) as resp:
    print(resp.read().decode()[:3000])
PY
```

Expected:

```text
Response references GitHub repo/branch main or equivalent Coolify Git source fields.
Response does not use build: /opt/fvg-alpha-caller as source of truth.
```

- [ ] **Step 3: Record final state in final response**

Report:

```text
GitHub-connected candidate UUID
running container name
old service/container state
verification log lines
whether future pushes auto-deploy or require Coolify redeploy trigger
```

---

## Self-Review

- Spec coverage: Plan covers GitHub source, env preservation, parallel deploy, verification, cutover, rollback path.
- Placeholder scan: Candidate UUID is intentionally unknown until Coolify creates it; all steps explain how to capture and replace it.
- Type consistency: Uses the same service UUID, repo, branch, env names, and log patterns throughout.
- Scope check: Single subsystem: Coolify deployment source migration only.
