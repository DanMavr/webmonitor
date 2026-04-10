import os
import sqlite3
import logging
import asyncio
from datetime import datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from dotenv import load_dotenv

load_dotenv()
logger    = logging.getLogger(__name__)
DB        = "data/monitor.db"
LOCAL_TZ  = ZoneInfo("Europe/Prague")


# ── DB helper ─────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt_ts(ts_str: str | None) -> str:
    """
    Convert a UTC timestamp string from the DB to local time for display.
    Returns 'Never' if None.
    """
    if not ts_str:
        return "Never"
    try:
        dt = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts_str[:16]


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show monitoring status for all tracked sites."""
    db = get_db()
    try:
        sites = db.execute(
            "SELECT DISTINCT site_name FROM snapshots"
        ).fetchall()

        if not sites:
            await update.message.reply_text("No sites monitored yet.")
            return

        lines = ["📡 WebMonitor Status", ""]

        for site in sites:
            name = site["site_name"]

            last_check = db.execute(
                """
                SELECT timestamp FROM job_log
                WHERE site_name = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (name,)
            ).fetchone()

            changes_count = db.execute(
                "SELECT COUNT(*) AS cnt FROM changes WHERE site_name = ?",
                (name,)
            ).fetchone()

            last_change = db.execute(
                """
                SELECT timestamp FROM changes
                WHERE site_name = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (name,)
            ).fetchone()

            lines.append(f"📊 {name}")
            lines.append(f"   Last check:  {_fmt_ts(last_check['timestamp'] if last_check else None)}")
            lines.append(f"   Changes:     {changes_count['cnt'] if changes_count else 0}")
            lines.append(f"   Last change: {_fmt_ts(last_change['timestamp'] if last_change else None)}")
            lines.append("")

        await update.message.reply_text("\n".join(lines))

    finally:
        db.close()


async def cmd_changes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the last 10 detected changes across all sites."""
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT site_name, url, change_pct, timestamp
            FROM changes
            ORDER BY timestamp DESC
            LIMIT 10
            """
        ).fetchall()

        if not rows:
            await update.message.reply_text("No changes detected yet.")
            return

        lines = ["📋 Recent Changes (last 10)", ""]

        for row in rows:
            pct       = float(row["change_pct"])
            short_url = urlparse(row["url"]).path or row["url"]
            lines.append(
                f"• {row['site_name']}\n"
                f"  {short_url}\n"
                f"  {pct:.1f}% — {_fmt_ts(row['timestamp'])}"
            )
            lines.append("")

        await update.message.reply_text("\n".join(lines))

    finally:
        db.close()


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show the last 5 changes with their diff previews.
    Most useful command for an investor checking what actually changed.
    """
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT site_name, url, change_pct, diff_text, timestamp
            FROM changes
            ORDER BY timestamp DESC
            LIMIT 5
            """
        ).fetchall()

        if not rows:
            await update.message.reply_text("No changes recorded yet.")
            return

        for row in rows:
            pct       = float(row["change_pct"])
            short_url = urlparse(row["url"]).path or row["url"]
            diff      = row["diff_text"] or ""

            # Show first 15 lines of diff
            diff_lines   = diff.split("\n")
            diff_preview = "\n".join(diff_lines[:15])
            if len(diff_lines) > 15:
                diff_preview += f"\n... ({len(diff_lines) - 15} more lines)"

            msg = (
                f"🔍 {row['site_name']}\n"
                f"{short_url}\n"
                f"{pct:.1f}% change — {_fmt_ts(row['timestamp'])}\n\n"
                f"{diff_preview}"
            )
            await update.message.reply_text(msg[:4096])

    finally:
        db.close()


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Trigger an immediate check of all sites.
    Runs checks in executor so the bot event loop stays responsive.
    """
    await update.message.reply_text("⏳ Starting check of all sites...")

    async def _run_checks():
        import yaml
        from monitor.core import check_site

        with open("config/sites.yaml") as f:
            sites = yaml.safe_load(f)["sites"]

        for site in sites:
            await check_site(site, force=True)

    try:
        await _run_checks()
        await update.message.reply_text("✅ Check complete.")
    except Exception as e:
        logger.error(f"Bot /check error: {e}")
        await update.message.reply_text(f"❌ Check failed: {e}")


async def cmd_inspect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show content quality of all baselines — useful for diagnosing thin pages."""
    try:
        import yaml
        from monitor.inspect import get_all_baselines_summary

        with open("config/sites.yaml") as f:
            sites = yaml.safe_load(f)["sites"]

        lines = ["🔬 Baseline Inspection", ""]

        for site in sites:
            summaries = get_all_baselines_summary(site["name"])
            if not summaries:
                lines.append(f"🔍 {site['name']}: no baselines yet")
                lines.append("")
                continue

            issues    = [s for s in summaries if s["status"] != "OK"]
            ok_count  = len(summaries) - len(issues)

            lines.append(f"🔍 {site['name']}")
            lines.append(f"   {ok_count}/{len(summaries)} pages OK")

            for issue in issues[:3]:
                short = urlparse(issue["url"]).path or issue["url"]
                lines.append(
                    f"   ⚠️ {short}: {issue['status']} "
                    f"({issue['word_count']} words)"
                )
            if len(issues) > 3:
                lines.append(f"   ... and {len(issues) - 3} more issues")
            lines.append("")

        await update.message.reply_text("\n".join(lines))

    except Exception as e:
        logger.error(f"Bot /inspect error: {e}")
        await update.message.reply_text(f"❌ Inspect failed: {e}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📡 WebMonitor Bot Commands\n\n"
        "/status   — Monitoring status for all sites\n"
        "/changes  — Last 10 detected changes (summary)\n"
        "/alerts   — Last 5 changes with diff previews\n"
        "/check    — Trigger immediate check of all sites\n"
        "/inspect  — Content quality of all baselines\n"
        "/help     — Show this message"
    )
    await update.message.reply_text(help_text)


# ── Bot startup ───────────────────────────────────────────────────────────────

def start_bot():
    """
    Start the Telegram bot in its own event loop.
    Designed to be called from a daemon thread in run.py.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — bot not started")
        return

    async def run():
        app = Application.builder().token(token).build()

        app.add_handler(CommandHandler("status",  cmd_status))
        app.add_handler(CommandHandler("changes", cmd_changes))
        app.add_handler(CommandHandler("alerts",  cmd_alerts))
        app.add_handler(CommandHandler("check",   cmd_check))
        app.add_handler(CommandHandler("inspect", cmd_inspect))
        app.add_handler(CommandHandler("help",    cmd_help))

        await app.initialize()
        await app.start()
        await app.updater.start_polling()

        try:
            await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

    asyncio.run(run())
