"""
notify.py — Telegram notifications with optional screenshot attachment.
"""

import os
import logging
import asyncio
from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger("monitor")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")


def _check_creds() -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured — skipping notification")
        return False
    return True


async def _send_text(text: str) -> bool:
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="HTML")
        return True
    except TelegramError as e:
        logger.error(f"Telegram message failed: {e}")
        return False


async def _send_photo(png_bytes: bytes, caption: str) -> bool:
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_photo(
            chat_id=TELEGRAM_CHAT_ID,
            photo=png_bytes,
            caption=caption,
            parse_mode="HTML",
        )
        return True
    except TelegramError as e:
        logger.error(f"Telegram photo failed: {e}")
        return False


def send_change_alert(site_name: str, diff: dict, png_bytes: bytes | None = None):
    """
    Send a change-detected alert. Attaches the new screenshot if available.
    """
    if not _check_creds():
        return

    added_block   = "\n".join(f"  + {l}" for l in diff["added"][:15])
    removed_block = "\n".join(f"  - {l}" for l in diff["removed"][:5])

    lines = [f"\u26a0\ufe0f <b>Change detected: {site_name}</b>"]
    if added_block:
        lines.append(f"\n<b>Added:</b>\n<code>{added_block}</code>")
    if removed_block:
        lines.append(f"\n<b>Removed:</b>\n<code>{removed_block}</code>")
    if len(diff["added"]) > 15:
        lines.append(f"\n<i>...and {len(diff['added']) - 15} more added lines</i>")

    message = "\n".join(lines)

    async def _send():
        if png_bytes:
            # Try photo with caption; fall back to text-only if image too large
            try:
                await _send_photo(png_bytes, caption=message[:1024])
                return
            except Exception:
                pass
        await _send_text(message)

    asyncio.run(_send())


def send_error_alert(site_name: str, error: str):
    if not _check_creds():
        return
    msg = f"\u274c <b>{site_name}</b> — check failed\n<code>{error}</code>"
    asyncio.run(_send_text(msg))
