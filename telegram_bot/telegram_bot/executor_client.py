from __future__ import annotations

import json as _json
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
    try:
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, json=json, headers=headers, timeout=30) as resp:
                text = await resp.text()
                body: Any
                try:
                    body = _json.loads(text) if text else None
                except _json.JSONDecodeError:
                    body = None

                if resp.status >= 400:
                    detail = body.get("detail") if isinstance(body, dict) else None
                    if not detail:
                        snippet = (text or "").strip().splitlines()[0:1]
                        snippet_str = snippet[0][:200] if snippet else f"executor {resp.status}"
                        detail = f"executor {resp.status}: {snippet_str}"
                    raise ExecutorClientError(str(detail))

                if not isinstance(body, dict):
                    raise ExecutorClientError(
                        f"executor returned non-JSON response (status {resp.status})"
                    )
                return body
    except aiohttp.ClientError as e:
        raise ExecutorClientError(f"executor unreachable: {e}") from e
    except TimeoutError as e:
        raise ExecutorClientError("executor timeout (30s)") from e


async def set_keys(telegram_id: int, api_key: str, api_secret: str) -> dict[str, Any]:
    return await _request(
        "POST",
        f"/users/{telegram_id}/keys",
        json={"api_key": api_key, "api_secret": api_secret},
    )


async def account_summary(telegram_id: int) -> dict[str, Any]:
    return await _request("GET", f"/account/summary?telegram_id={telegram_id}")
