from __future__ import annotations

from typing import Any

import aiohttp

from telegram_bot.config import settings


class ExecutorClientError(RuntimeError):
    pass


async def _request(method: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
    if not settings.INTERNAL_TOKEN:
        raise ExecutorClientError("INTERNAL_TOKEN is not configured")
    url = f"{settings.EXECUTOR_URL.rstrip('/')}{path}"
    headers = {"X-Internal-Token": settings.INTERNAL_TOKEN}
    async with aiohttp.ClientSession() as session:
        async with session.request(method, url, json=json, headers=headers, timeout=30) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 400:
                detail = body.get("detail") if isinstance(body, dict) else None
                raise ExecutorClientError(str(detail or f"executor {resp.status}"))
            if not isinstance(body, dict):
                raise ExecutorClientError("executor returned non-object response")
            return body


async def set_keys(telegram_id: int, api_key: str, api_secret: str) -> dict[str, Any]:
    return await _request(
        "POST",
        f"/users/{telegram_id}/keys",
        json={"api_key": api_key, "api_secret": api_secret},
    )


async def account_summary(telegram_id: int) -> dict[str, Any]:
    return await _request("GET", f"/account/summary?telegram_id={telegram_id}")
