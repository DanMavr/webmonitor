import os
import logging

logger = logging.getLogger("monitor")

# Match the constant defined in core.py
DIFF_NOTIFY_LINES = 20


def _severity(change_pct: float) -> str:
    """Returns severity label based on change percentage."""
    if change_pct >= 30:
        return "HIGH"
    elif change_pct >= 10:
        return "MEDIUM"
    else:
        return "LOW"


async def send_notifications(site: dict, changes: list):
    """
    Dispatch change notifications to all configured channels.
    Called from core.check_site() whenever changes are detected.
    """
    channels = site.get("notify", [])

    for channel in channels:
        if channel == "telegram":
            await _send_telegram(site, changes)
        elif channel == "email":
            await _send_email_single(site, changes)
        else:
            logger.warning(f"Unknown notify channel: {channel}")


async def _send_telegram(site: dict, changes: list):
    """
    Send Telegram notifications for detected changes.
    One message per change, with diff preview.
    Bot object created once and reused for all changes in this batch.
    """
    import telegram

    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.warning("Telegram credentials not set — skipping notification")
        return

    bot = telegram.Bot(token=token)

    for change in changes:
        url        = change.get("url", "unknown")
        change_pct = change.get("change_pct", 0)
        diff       = change.get("diff", "")
        severity   = _severity(change_pct)

        # Truncate diff for Telegram message
        diff_lines   = diff.split("\n")
        diff_preview = "\n".join(diff_lines[:DIFF_NOTIFY_LINES])
        if len(diff_lines) > DIFF_NOTIFY_LINES:
            diff_preview += (
                f"\n... ({len(diff_lines) - DIFF_NOTIFY_LINES} more lines)"
            )

        message = (
            f"🔔 WebMonitor Alert\n\n"
            f"Site: {site['name']}\n"
            f"Severity: {severity}\n"
            f"Change: {change_pct:.1f}%\n"
            f"URL: {url}\n\n"
            f"Preview:\n{diff_preview}"
        )

        await _telegram_send(bot, chat_id, message)


async def _telegram_send(bot, chat_id: str, message: str):
    """Send a Telegram message with exponential-backoff retry."""
    import asyncio

    max_retries = 3
    for attempt in range(max_retries):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=message[:4096]   # Telegram hard limit
            )
            return
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning(
                    f"Telegram send failed (attempt {attempt + 1}/"
                    f"{max_retries}), retrying in {wait}s: {e}"
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    f"Telegram send failed after {max_retries} attempts: {e}"
                )


async def _send_email_single(site: dict, changes: list):
    """Placeholder — email notifications not yet implemented."""
    logger.info("Email notifications not yet implemented")


def _email_template(site: dict, changes: list) -> str:
    """Generate HTML email body (for future use)."""
    lines = [
        "<h2>WebMonitor Change Alert</h2>",
        f"<p>Site: <strong>{site['name']}</strong></p>",
        f"<p>Changes detected: <strong>{len(changes)}</strong></p>",
        "<hr>",
    ]
    for c in changes:
        lines.append(f"<h3>{c.get('url', '')}</h3>")
        lines.append(f"<p>Change: {c.get('change_pct', 0):.1f}%</p>")
        diff = c.get("diff", "")
        if diff:
            lines.append(f"<pre>{diff[:500]}</pre>")
    return "\n".join(lines)
