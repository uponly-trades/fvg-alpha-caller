import importlib.util

import pytest


@pytest.mark.skipif(
    importlib.util.find_spec("aiogram") is None,
    reason="aiogram not installed",
)
def test_import_main_module():
    import telegram_bot.main  # noqa: F401
