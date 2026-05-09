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
    """Smoke: helper bootstraps account mode then sequences leverage+marginType,
    swallows code 4046 on no-op responses."""
    from trade_executor import exchange as exmod
    exmod._ACCOUNT_MODE_INITIALIZED.clear()
    calls = []

    class FakeEx:
        async def fapiPrivatePostMultiAssetsMargin(self, params):
            calls.append(("multiAssets", params))
            from ccxt.base.errors import ExchangeError
            raise ExchangeError("-4046 No need to change")

        async def fapiPrivatePostPositionSideDual(self, params):
            calls.append(("posSide", params))
            from ccxt.base.errors import ExchangeError
            raise ExchangeError("-4059 No need to change position side")

        async def fapiPrivatePostLeverage(self, params):
            calls.append(("leverage", params))
            return {"leverage": params["leverage"]}

        async def fapiPrivatePostMarginType(self, params):
            calls.append(("marginType", params))
            from ccxt.base.errors import ExchangeError
            raise ExchangeError("-4046 No need to change margin type")

    import asyncio
    asyncio.run(exmod.set_isolated_and_leverage(FakeEx(), "BTCUSDT", 5))
    stages = [c[0] for c in calls]
    assert stages == ["multiAssets", "posSide", "leverage", "marginType"]
    assert calls[0][1] == {"multiAssetsMargin": "false"}
    assert calls[1][1] == {"dualSidePosition": "false"}
    assert calls[3][1] == {"symbol": "BTCUSDT", "marginType": "ISOLATED"}


def test_set_crossed_and_leverage_calls_crossed_margin_type(monkeypatch):
    from trade_executor import exchange as exmod
    exmod._ACCOUNT_MODE_INITIALIZED.clear()
    calls = []

    class FakeEx:
        async def fapiPrivatePostMultiAssetsMargin(self, params):
            calls.append(("multiAssets", params))

        async def fapiPrivatePostPositionSideDual(self, params):
            calls.append(("posSide", params))

        async def fapiPrivatePostLeverage(self, params):
            calls.append(("leverage", params))
            return {"leverage": params["leverage"]}

        async def fapiPrivatePostMarginType(self, params):
            calls.append(("marginType", params))
            return {}

    import asyncio
    asyncio.run(exmod.set_isolated_and_leverage(FakeEx(), "BTCUSDT", 10, "CROSSED"))
    assert calls[-1] == ("marginType", {"symbol": "BTCUSDT", "marginType": "CROSSED"})


def test_ensure_account_mode_idempotent_per_exchange():
    """Bootstrap runs once per exchange instance — second call is a no-op."""
    from trade_executor import exchange as exmod
    exmod._ACCOUNT_MODE_INITIALIZED.clear()
    calls = []

    class FakeEx:
        async def fapiPrivatePostMultiAssetsMargin(self, params):
            calls.append("multiAssets")

        async def fapiPrivatePostPositionSideDual(self, params):
            calls.append("posSide")

    import asyncio
    ex = FakeEx()
    asyncio.run(exmod.ensure_account_mode(ex))
    asyncio.run(exmod.ensure_account_mode(ex))
    assert calls == ["multiAssets", "posSide"]
