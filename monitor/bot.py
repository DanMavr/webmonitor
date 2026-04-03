import os
import sqlite3
import logging
from datetime import datetime
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes
)
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
DB = "data/monitor.db"


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = get_db()

    sites = db.execute(
        "SELECT DISTINCT site_name FROM snapshots"
    ).fetchall()

    if not sites:
        await update.message.reply_text("No sites monitored yet.")
        return

    lines = ["WebMonitor Status", ""]

    for site in sites:
        name = site["site_name"]

        last_check = db.execute("""
            SELECT timestamp FROM job_log
            WHERE site_name = ?
            ORDER BY timestamp DESC LIMIT 1
        """, (name,)).fetchone()

        total_changes = db.execute(
            "SELECT COUNT(*) FROM changes WHERE site_name = ?",
            (name,)
        ).fetchone()[0]

        last_status = db.execute("""
            SELECT status FROM job_log
            WHERE site_name = ?
            ORDER BY timestamp DESC LIMIT 1
        """, (name,)).fetchone()

        status = "OK" if last_status and last_status[0] == "success" else "Error"
        check_time = last_check[0][:16] if last_check else "Never"

        lines.append(f"Site: {name}")
        lines.append(f"Last check: {check_time}")
        lines.append(f"Total changes: {total_changes}")
        lines.append(f"Status: {status}")
        lines.append("")

    db.close()
    await update.message.reply_text("\n".join(lines))


async def cmd_changes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = get_db()

    changes = db.execute("""
        SELECT site_name, url, change_pct, timestamp
        FROM changes
        ORDER BY timestamp DESC
        LIMIT 10
    """).fetchall()

    if not changes:
        await update.message.reply_text("No changes detected yet.")
        return

    lines = ["Last 10 Changes", ""]

    for c in changes:
        path = urlparse(c["url"]).path or "/"
        lines.append(f"Site: {c['site_name']}")
        lines.append(f"Page: {path}")
        lines.append(f"Change: {c['change_pct']}%")
        lines.append(f"When: {c['timestamp'][:16]}")
        lines.append("")

    db.close()
    await update.message.reply_text("\n".join(lines))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "WebMonitor Commands\n\n"
        "/status - Show all monitored sites\n"
        "/changes - Show last 10 changes\n"
        "/help - Show this message"
    )
    await update.message.reply_text(text)


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Running check now, please wait...")

    import yaml
    from monitor.core import check_site

    with open("config/sites.yaml") as f:
        sites = yaml.safe_load(f)["sites"]

    for site in sites:
        await check_site(site)

    await update.message.reply_text("Check complete.")


def start_bot():
    import asyncio

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("Telegram token not set, bot commands disabled")
        return

    async def run():
        app = Application.builder().token(token).build()
        app.add_handler(CommandHandler("status", cmd_status))
        app.add_handler(CommandHandler("changes", cmd_changes))
        app.add_handler(CommandHandler("help", cmd_help))
        app.add_handler(CommandHandler("check", cmd_check))

        await app.initialize()
        await app.start()
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        # Keep running until stopped
        while True:
            await asyncio.sleep(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(run())
    except Exception as e:
        logger.error(f"Bot error: {e}")
    finally:
        loop.close()
