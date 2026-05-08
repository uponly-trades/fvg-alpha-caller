from __future__ import annotations

import logging

import ccxt.async_support as ccxt

import binance_limit

log = logging.getLogger("exchange")


def _wrap_request(ex):
    """Patch ex.request so every fapi call records X-MBX-USED-WEIGHT-1m and
    triggers the shared circuit breaker on 418/429."""
    orig_fetch = ex.fetch

    async def fetch_wrapped(url, method='GET', headers=None, body=None):
        try:
            result = await orig_fetch(url, method, headers, body)
        except ccxt.DDoSProtection as e:
            # ccxt throws DDoSProtection on 418
            msg = str(e)
            retry = 120
            try:
                # ccxt sometimes embeds Retry-After in message; default 120
                import re
                m = re.search(r"Retry-After[^0-9]*(\d+)", msg)
                if m:
                    retry = int(m.group(1))
            except Exception:
                pass
            await binance_limit.mark_banned_async(retry + 1)
            raise
        except ccxt.RateLimitExceeded as e:
            await binance_limit.mark_banned_async(60)
            raise
        finally:
            try:
                hdrs = getattr(ex, 'last_response_headers', None)
                if hdrs:
                    binance_limit.record_headers(dict(hdrs))
            except Exception:
                pass
        return result

    ex.fetch = fetch_wrapped


def build_exchange(api_key: str, api_secret: str, *, proxy_url: str | None) -> ccxt.binanceusdm:
    options: dict = {
        "apiKey": api_key,
        "secret": api_secret,
        "options": {"defaultType": "future"},
        "enableRateLimit": True,
        "timeout": 30000,  # 30s — SOCKS5 proxy adds latency
    }
    ex = ccxt.binanceusdm(options)
    if proxy_url:
        if proxy_url.startswith("socks"):
            # ccxt 4.3+ supports socksProxy for SOCKS4/5 URIs
            ex.socksProxy = proxy_url
        else:
            ex.aiohttp_proxy = proxy_url
    _wrap_request(ex)
    return ex


_ACCOUNT_MODE_INITIALIZED: set = set()


def _is_no_change_error(msg: str) -> bool:
    """Binance returns -4046 / -4059 / 'No need to change' when target state matches."""
    return ("4046" in msg or "4059" in msg
            or "No need to change" in msg or "no need to change" in msg.lower())


async def ensure_account_mode(ex) -> None:
    """Force account into One-Way + Single-Asset mode so per-symbol ISOLATED works.

    Idempotent + cached per exchange instance. Binance docs:
      - POST /fapi/v1/multiAssetsMargin (multiAssetsMargin=false) → Single-Asset
      - POST /fapi/v1/positionSide/dual (dualSidePosition=false) → One-Way
    Both return -4046 when already in target state — swallow that.
    """
    key = id(ex)
    if key in _ACCOUNT_MODE_INITIALIZED:
        return
    try:
        await ex.fapiPrivatePostMultiAssetsMargin({"multiAssetsMargin": "false"})
    except Exception as e:
        if not _is_no_change_error(str(e)):
            log.warning("multiAssetsMargin off failed (continuing): %s", e)
    try:
        await ex.fapiPrivatePostPositionSideDual({"dualSidePosition": "false"})
    except Exception as e:
        if not _is_no_change_error(str(e)):
            log.warning("positionSide/dual off failed (continuing): %s", e)
    _ACCOUNT_MODE_INITIALIZED.add(key)


async def set_isolated_and_leverage(ex, symbol: str, leverage: int) -> None:
    """Set leverage and ISOLATED margin. Swallow 'no change needed' (-4046)."""
    await ensure_account_mode(ex)
    await ex.fapiPrivatePostLeverage({"symbol": symbol, "leverage": leverage})
    try:
        await ex.fapiPrivatePostMarginType({"symbol": symbol, "marginType": "ISOLATED"})
    except Exception as e:
        msg = str(e)
        if _is_no_change_error(msg):
            log.debug("margin type already isolated for %s", symbol)
            return
        raise
