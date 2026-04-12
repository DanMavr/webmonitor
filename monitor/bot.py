"""
Telegram bot — alert delivery only.
Commands have been removed. Monitoring status is available via the dashboard.
"""

import logging
import asyncio
import os
from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger("monitor")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


async def send_message(text: str) -> bool:
    """Send a plain-text message to the configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not set — skipping notification")
        return False
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
        return True
    except TelegramError as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def start_bot():
    """No-op — bot commands removed. Kept for import compatibility."""
    pass
