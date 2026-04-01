import os
import smtplib
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path
from urllib.parse import urlparse

import telegram
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _severity(pct: float) -> tuple[str, str]:
    """Returns (label, colour) based on change percentage."""
    if pct < 5:
        return "🟡 Minor Change", "#f59e0b"
    elif pct < 20:
        return "🟠 Moderate Change", "#f97316"
    else:
        return "🔴 Major Change", "#ef4444"


async def send_notifications(site: dict, diff: dict, url: str):
    """Send notifications for a single page change."""
    if "telegram" in site.get("notify", []):
        await _send_telegram_single(site, diff, url)
    if "email" in site.get("notify", []):
        _send_email_single(site, diff, url)


async def notify_site_summary(site: dict, changes: list[dict]):
    """Send a summary notification for whole-site changes."""
    if "telegram" in site.get("notify", []):
        await _send_telegram_summary(site, changes)
    if "email" in site.get("notify", []):
        _send_email_summary(site, changes)


async def _send_telegram_single(site: dict, diff: dict, url: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("Telegram credentials not set")
        return

    pct = diff.get("change_percent", 0)
    severity, _ = _severity(pct)
    now = datetime.now().strftime("%d %b %Y at %H:%M")

    lines = [
        f"*{severity}*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📌 *{site['name']}*",
        f"🔗 {url}",
        f"📊 {pct}% of page changed",
        f"🕐 {now}",
        f"━━━━━━━━━━━━━━━━━━━━",
    ]

    if diff.get("added"):
        lines.append("*What appeared:*")
        for line in diff["added"][:5]:
            lines.append(f"  `+ {line[:80]}`")

    if diff.get("removed"):
        lines.append("*What disappeared:*")
        for line in diff["removed"][:5]:
            lines.append(f"  `- {line[:80]}`")

    total_extra = diff.get("added_count", 0) + diff.get("removed_count", 0) - 10
    if total_extra > 0:
        lines.append(f"\n_...and {total_extra} more changes_")

    await _telegram_send(token, chat_id, "\n".join(lines))


async def _send_telegram_summary(site: dict, changes: list[dict]):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    count = len(changes)
    now = datetime.now().strftime("%d %b %Y at %H:%M")

    lines = [
        f"🌐 *Site-wide changes detected*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"📌 *{site['name']}*",
        f"📊 {count} page{'s' if count > 1 else ''} changed",
        f"🕐 {now}",
        f"━━━━━━━━━━━━━━━━━━━━",
    ]

    for change in changes[:10]:
        path = urlparse(change["url"]).path or "/"
        pct = change["diff"]["change_percent"]
        lines.append(f"  • `{path}` — {pct}% changed")

    if count > 10:
        lines.append(f"\n_...and {count - 10} more pages_")

    lines.append("")
    for change in changes[:3]:
        path = urlparse(change["url"]).path or "/"
        lines.append(f"*{path}*")
        for a in change["diff"].get("added", [])[:3]:
            lines.append(f"  `+ {a[:80]}`")
        for r in change["diff"].get("removed", [])[:3]:
            lines.append(f"  `- {r[:80]}`")
        lines.append("")

    await _telegram_send(token, chat_id, "\n".join(lines))


async def _telegram_send(token: str, chat_id: str, message: str):
    try:
        bot = telegram.Bot(token=token)
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        logger.info("Telegram notification sent")
    except Exception as e:
        logger.error(f"Telegram failed: {e}")


def _send_email_single(site: dict, diff: dict, url: str):
    pct = diff.get("change_percent", 0)
    severity, color = _severity(pct)
    now = datetime.now().strftime("%d %b %Y at %H:%M")

    added_rows = "".join(
        f'<tr><td style="color:#22c55e;font-family:monospace;'
        f'padding:4px 8px">+ {line[:120]}</td></tr>'
        for line in diff.get("added", [])[:15]
    )
    removed_rows = "".join(
        f'<tr><td style="color:#ef4444;font-family:monospace;'
        f'padding:4px 8px">- {line[:120]}</td></tr>'
        for line in diff.get("removed", [])[:15]
    )

    html = _email_template(
        title=f"{severity} Detected",
        site_name=site["name"],
        url=url,
        pct=pct,
        color=color,
        now=now,
        body_rows=added_rows + removed_rows
    )

    _send_email(
        subject=f"[WebMonitor] {site['name']} changed ({pct}%)",
        html=html
    )


def _send_email_summary(site: dict, changes: list[dict]):
    count = len(changes)
    now = datetime.now().strftime("%d %b %Y at %H:%M")
    color = "#6366f1"

    rows = ""
    for change in changes[:20]:
        path = urlparse(change["url"]).path or "/"
        pct = change["diff"]["change_percent"]
        rows += (
            f'<tr>'
            f'<td style="color:#e2e8f0;font-family:monospace;'
            f'padding:6px 8px">{path}</td>'
            f'<td style="color:#f59e0b;padding:6px 8px">{pct}%</td>'
            f'</tr>'
        )

    html = _email_template(
        title=f"🌐 {count} Pages Changed",
        site_name=site["name"],
        url=site["url"],
        pct=None,
        color=color,
        now=now,
        body_rows=rows
    )

    _send_email(
        subject=f"[WebMonitor] {site['name']} — {count} pages changed",
        html=html
    )


def _email_template(title, site_name, url, pct,
                    color, now, body_rows) -> str:
    pct_line = (
        f'<tr><td style="color:#94a3b8;font-size:13px;padding:6px 0;'
        f'width:120px">Change</td>'
        f'<td style="color:{color};font-size:14px;font-weight:700">'
        f'{pct}% of page changed</td></tr>'
    ) if pct is not None else ""

    return f"""
    <!DOCTYPE html><html><body
      style="margin:0;padding:0;background:#0f172a;font-family:sans-serif">
      <div style="max-width:640px;margin:40px auto;background:#1e293b;
                  border-radius:12px;overflow:hidden">
        <div style="background:{color};padding:24px 32px">
          <h1 style="margin:0;color:white;font-size:20px">{title}</h1>
          <p style="margin:4px 0 0;color:rgba(255,255,255,0.85);
                    font-size:14px">{now}</p>
        </div>
        <div style="padding:24px 32px;border-bottom:1px solid #334155">
          <table style="width:100%;border-collapse:collapse">
            <tr>
              <td style="color:#94a3b8;font-size:13px;padding:6px 0;
                         width:120px">Site</td>
              <td style="color:#f1f5f9;font-size:14px;
                         font-weight:600">{site_name}</td>
            </tr>
            <tr>
              <td style="color:#94a3b8;font-size:13px;padding:6px 0">
                URL</td>
              <td><a href="{url}"
                     style="color:#38bdf8;font-size:14px">{url}</a></td>
            </tr>
            {pct_line}
          </table>
        </div>
        <div style="padding:24px 32px">
          <div style="background:#0f172a;border-radius:8px;overflow:hidden">
            <table style="width:100%;border-collapse:collapse">
              {body_rows}
            </table>
          </div>
        </div>
        <div style="padding:16px 32px;background:#0f172a;
                    border-top:1px solid #1e293b">
          <p style="margin:0;color:#475569;font-size:12px;text-align:center">
            WebMonitor • Raspberry Pi
          </p>
        </div>
      </div>
    </body></html>
    """


def _send_email(subject: str, html: str):
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    recipients = [
        r.strip() for r in os.getenv("EMAIL_RECIPIENTS", "").split(",")
        if r.strip()
    ]

    if not sender or not password or not recipients:
        logger.warning("Email credentials not configured")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"WebMonitor <{sender}>"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(
            os.getenv("SMTP_HOST", "smtp.gmail.com"),
            int(os.getenv("SMTP_PORT", 587))
        ) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        logger.info("Email sent")
    except Exception as e:
        logger.error(f"Email failed: {e}")
