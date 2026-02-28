"""Operator notification utilities — send alerts to the operator's Telegram chat."""
from __future__ import annotations

import logging

from telegram import Bot

from src.config import OPERATOR_CHAT_ID, TELEGRAM_BOT_TOKEN

log = logging.getLogger(__name__)


async def notify_operator(text: str) -> None:
    """Send a plain-text Telegram message to the operator.

    Silently no-ops if OPERATOR_CHAT_ID or TELEGRAM_BOT_TOKEN are not configured.
    """
    if not TELEGRAM_BOT_TOKEN or not OPERATOR_CHAT_ID:
        return
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=int(OPERATOR_CHAT_ID), text=text)
    except Exception as e:
        log.warning("Failed to notify operator: %s", e)
