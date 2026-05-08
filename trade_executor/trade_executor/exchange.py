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
    """Binance returns -4046 / -4059 / -4171 when target state matches.
    -4046: leverage/marginType already set
    -4059: positionSide/dual already set
    -4171: multiAssetsMargin already set
    """
    return ("4046" in msg or "4059" in msg or "4171" in msg
            or "no need to" in msg.lower()
            or "does not need to be adjusted" in msg.lower())


async def ensure_account_mode(ex) -> None:
    """Try to force One-Way + Single-Asset mode; detect actual mode after.

    Binance refuses to flip these when user has open positions, so attempts
    can fail silently. After best-effort attempts, query actual mode and
    stash `ex._is_hedge_mode` so callers can thread `positionSide` correctly.
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
    try:
        side_resp = await ex.fapiPrivateGetPositionSideDual({})
        ex._is_hedge_mode = bool(side_resp.get("dualSidePosition"))
    except Exception:
        ex._is_hedge_mode = False
    if getattr(ex, "_is_hedge_mode", False):
        log.warning("account in hedge mode — orders will include positionSide")
    _ACCOUNT_MODE_INITIALIZED.add(key)


async def set_isolated_and_leverage(ex, symbol: str, leverage: int) -> None:
    """Set leverage and ISOLATED margin. Swallow 'no change needed' (-4046).

    If account is in Multi-Assets mode (-4168), per-symbol ISOLATED is impossible —
    fall back to whatever margin mode is current (cross by default). Order still
    works, just shares margin across positions. Logged once per symbol.
    """
    await ensure_account_mode(ex)
    await ex.fapiPrivatePostLeverage({"symbol": symbol, "leverage": leverage})
    try:
        await ex.fapiPrivatePostMarginType({"symbol": symbol, "marginType": "ISOLATED"})
    except Exception as e:
        msg = str(e)
        if _is_no_change_error(msg):
            log.debug("margin type already isolated for %s", symbol)
            return
        if "4168" in msg:
            log.warning("account in Multi-Assets mode — using cross margin for %s", symbol)
            return
        raise
