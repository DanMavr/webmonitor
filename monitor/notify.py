"""
Notification delivery for WebMonitor.
Currently supports: Telegram.
"""

import logging
import asyncio
import os
from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger("monitor")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

MAX_DIFF_LINES = 20   # lines of diff shown in Telegram message


def _severity(change_pct: float) -> str:
    if change_pct >= 30:
        return "HIGH"
    elif change_pct >= 10:
        return "MEDIUM"
    return "LOW"


async def send_notifications(site: dict, changes: list):
    """
    Dispatch change notifications for all configured channels.
    Called from core.check_site() whenever changes are detected.
    """
    channels = site.get("notify", [])
    for channel in channels:
        if channel == "telegram":
            await _send_telegram(site, changes)
        else:
            logger.warning(f"Unknown notify channel '{channel}' for {site['name']}")


async def _send_telegram(site: dict, changes: list):
    """Send one Telegram message per changed URL."""
    for change in changes:
        url        = change.get("url", "")
        change_pct = float(change.get("change_pct", 0))
        diff_text  = change.get("diff", "")
        severity   = _severity(change_pct)

        diff_lines = diff_text.splitlines()[:MAX_DIFF_LINES]
        diff_preview = "\n".join(diff_lines)
        if len(diff_text.splitlines()) > MAX_DIFF_LINES:
            diff_preview += f"\n... (+{len(diff_text.splitlines()) - MAX_DIFF_LINES} more lines)"

        message = (
            f"[{severity}] {site['name']}\n"
            f"Change: {change_pct:.1f}%\n"
            f"URL: {url}\n"
            f"---\n{diff_preview}"
        )

        await _telegram_send(message)


async def _telegram_send(text: str, retries: int = 3):
    """Send a Telegram message with exponential-backoff retry."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured — skipping")
        return

    bot   = Bot(token=TELEGRAM_BOT_TOKEN)
    delay = 2
    for attempt in range(1, retries + 1):
        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text[:4096])
            return
        except TelegramError as e:
            logger.warning(f"Telegram attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                await asyncio.sleep(delay)
                delay *= 2

    logger.error("All Telegram send attempts failed")
