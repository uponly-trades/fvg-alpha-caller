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


async def set_isolated_and_leverage(ex, symbol: str, leverage: int) -> None:
    """Set leverage and ISOLATED margin. Swallow 'no change needed' (-4046)."""
    await ex.fapiPrivate_post_leverage({"symbol": symbol, "leverage": leverage})
    try:
        await ex.fapiPrivate_post_margintype({"symbol": symbol, "marginType": "ISOLATED"})
    except Exception as e:
        msg = str(e)
        if "4046" in msg or "No need to change" in msg:
            log.debug("margin type already isolated for %s", symbol)
            return
        raise
