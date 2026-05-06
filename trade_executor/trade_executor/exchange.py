from __future__ import annotations

import logging

import ccxt.async_support as ccxt

log = logging.getLogger("exchange")


def build_exchange(api_key: str, api_secret: str, *, proxy_url: str | None) -> ccxt.binanceusdm:
    options: dict = {
        "apiKey": api_key,
        "secret": api_secret,
        "options": {"defaultType": "future"},
        "enableRateLimit": True,
    }
    ex = ccxt.binanceusdm(options)
    if proxy_url:
        if proxy_url.startswith("socks"):
            # ccxt 4.3+ supports socksProxy for SOCKS4/5 URIs
            ex.socksProxy = proxy_url
        else:
            ex.aiohttp_proxy = proxy_url
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
