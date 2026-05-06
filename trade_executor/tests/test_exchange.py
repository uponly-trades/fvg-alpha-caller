from trade_executor.exchange import build_exchange


def test_build_exchange_has_proxy_when_url_set():
    ex = build_exchange("k", "s", proxy_url="http://proxy:8080")
    assert ex.aiohttp_proxy == "http://proxy:8080"
    assert ex.options.get("defaultType") == "future"


def test_build_exchange_no_proxy_when_none():
    ex = build_exchange("k", "s", proxy_url=None)
    assert getattr(ex, "aiohttp_proxy", None) in (None, "")
    assert getattr(ex, "socksProxy", None) in (None, "")


def test_build_exchange_socks5_proxy_uses_socks_attr():
    ex = build_exchange("k", "s", proxy_url="socks5://u:p@host:1080")
    assert ex.socksProxy == "socks5://u:p@host:1080"
    assert getattr(ex, "aiohttp_proxy", None) in (None, "")


def test_set_isolated_and_leverage_calls_chain(monkeypatch):
    """Smoke: helper sequences leverage + marginType, swallows code 4046."""
    from trade_executor import exchange as exmod
    calls = []

    class FakeEx:
        async def fapiPrivate_post_leverage(self, params):
            calls.append(("leverage", params))
            return {"leverage": params["leverage"]}

        async def fapiPrivate_post_margintype(self, params):
            calls.append(("marginType", params))
            from ccxt.base.errors import ExchangeError
            raise ExchangeError("-4046 No need to change margin type")

    import asyncio
    asyncio.run(exmod.set_isolated_and_leverage(FakeEx(), "BTCUSDT", 5))
    assert calls[0][0] == "leverage"
    assert calls[1][0] == "marginType"
