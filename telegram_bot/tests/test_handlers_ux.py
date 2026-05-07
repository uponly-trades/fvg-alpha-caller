"""Hybrid UX helpers: store prompt msg_id in FSM, edit-or-reply, delete-on-flow."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("INTERNAL_TOKEN", "test-token")
os.environ.setdefault("EXECUTOR_URL", "http://stub")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1:stub")
os.environ.setdefault("DATABASE_URL", "postgresql://x")


@pytest.mark.asyncio
async def test_edit_or_reply_edits_when_msg_id_known():
    """Given a prompt msg_id stored in FSM, helper must edit it (no new bubble)."""
    from telegram_bot.handlers import _edit_or_reply

    bot = AsyncMock()
    bot.edit_message_text = AsyncMock(return_value=True)
    bot.send_message = AsyncMock()

    await _edit_or_reply(
        bot, chat_id=42, prompt_msg_id=99, text="ok", reply_markup=None, parse_mode=None
    )
    bot.edit_message_text.assert_awaited_once()
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_or_reply_falls_back_to_send_when_no_msg_id():
    """Without a stored prompt id, helper sends a new message."""
    from telegram_bot.handlers import _edit_or_reply

    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    bot.send_message = AsyncMock(return_value=True)

    await _edit_or_reply(
        bot, chat_id=42, prompt_msg_id=None, text="ok", reply_markup=None, parse_mode=None
    )
    bot.send_message.assert_awaited_once()
    bot.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_or_reply_falls_back_when_edit_fails():
    """If edit raises (msg deleted/too old), fallback to send_message — never crash."""
    from telegram_bot.handlers import _edit_or_reply

    bot = AsyncMock()
    bot.edit_message_text = AsyncMock(side_effect=Exception("message to edit not found"))
    bot.send_message = AsyncMock(return_value=True)

    await _edit_or_reply(
        bot, chat_id=42, prompt_msg_id=99, text="ok", reply_markup=None, parse_mode=None
    )
    bot.edit_message_text.assert_awaited_once()
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_delete_swallows_errors():
    """Deleting a user's message must never crash if already gone."""
    from telegram_bot.handlers import _safe_delete

    bot = AsyncMock()
    bot.delete_message = AsyncMock(side_effect=Exception("message to delete not found"))

    await _safe_delete(bot, chat_id=1, message_id=2)
    bot.delete_message.assert_awaited_once()
