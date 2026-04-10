import hashlib
import difflib
import logging
import time
import asyncio
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from rich.console import Console

from monitor.storage import (
    get_last_snapshot, save_snapshot,
    save_change, log_job
)
from monitor import crawler

console = Console()
logger = logging.getLogger("monitor")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    )
}

CHROMIUM_BINARY  = "/usr/bin/chromium-browser"
CHROMEDRIVER_BIN = "/usr/bin/chromedriver"

MIN_CONTENT_WORDS_DEFAULT = 50

# Diff display limits
DIFF_STORE_LINES  = 80   # lines stored in DB
DIFF_NOTIFY_LINES = 20   # lines sent in Telegram message


def extract_content(html: str, site: dict) -> str:
    """
    Extract clean text from HTML.

    If target_selector is set in site config, only text inside that CSS
    selector is extracted — everything else on the page is ignored.
    This is the cleanest way to monitor a specific section of a page
    (e.g. just the RNS announcements table on LSE, ignoring nav/prices).

    Without target_selector, the full page text is extracted minus any
    ignore_selectors.

    Also captures href links so new document/filing links are not missed.
    """
    soup = BeautifulSoup(html, "lxml")

    # Always strip non-content tags first
    for tag in soup(["script", "style", "meta", "noscript"]):
        tag.decompose()

    # ── Target selector: only watch a specific section ────────────────────
    target_sel = site.get("target_selector", "").strip()
    if target_sel:
        target_el = soup.select_one(target_sel)
        if target_el:
            # Work only within the targeted element
            soup = target_el
            logger.debug(f"target_selector '{target_sel}' matched — extracting section only")
        else:
            logger.warning(
                f"target_selector '{target_sel}' not found in page for "
                f"{site.get('name', '?')} — falling back to full page"
            )

    # ── Ignore selectors: strip noise elements ────────────────────────────
    for selector in site.get("ignore_selectors", []):
        for el in (soup.select(selector) if hasattr(soup, "select") else []):
            el.decompose()

    # Capture visible text
    text_node = soup if hasattr(soup, "get_text") else soup
    text = " ".join(text_node.get_text(separator=" ").split())

    # Append all href links so new document/filing links are detectable
    links = []
    find_fn = soup.find_all if hasattr(soup, "find_all") else lambda *a, **k: []
    for a in find_fn("a", href=True):
        href = a["href"].strip()
        # Only include meaningful links — skip anchors and javascript: hrefs
        if href and not href.startswith("#") and not href.lower().startswith("javascript"):
            links.append(href)

    if links:
        text += " LINKS: " + " ".join(links)

    return text


def fetch_page_js(url: str, site: dict) -> str:
    """
    Fetch a JS-rendered page using Selenium.
    MUST be called via run_in_executor — never call directly in async context.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.by import By

    opts = Options()
    opts.binary_location = CHROMIUM_BINARY
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")

    service = Service(executable_path=CHROMEDRIVER_BIN)

    # driver initialised to None so finally block is always safe
    driver = None
    try:
        driver = webdriver.Chrome(service=service, options=opts)
        driver.get(url)

        wait_seconds = site.get("js_wait_seconds", 3)

        # Wait for body to appear (dynamic wait — exits as soon as ready)
        WebDriverWait(driver, wait_seconds).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )

        # Short fixed stabilisation pause for JS frameworks to finish rendering
        # Using 2s instead of repeating the full wait_seconds
        time.sleep(2)

        return driver.page_source

    except Exception as e:
        logger.error(f"Selenium error for {url}: {e}")
        return ""

    finally:
        if driver:
            driver.quit()


async def fetch_page(url: str, site: dict) -> str:
    """
    Fetch a page. Returns HTML string.
    Auto-falls back to Selenium if content is too thin after requests fetch.
    """
    min_words = site.get("min_content_words", MIN_CONTENT_WORDS_DEFAULT)

    # Force JS path if configured
    if site.get("javascript"):
        loop = asyncio.get_running_loop()
        html = await loop.run_in_executor(None, lambda: fetch_page_js(url, site))
        return html

    # Try requests first (fast)
    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            timeout=20,
            verify=site.get("ssl_verify", True)
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        logger.warning(f"requests failed for {url}: {e}")
        return ""

    # Check word count — fall back to Selenium if too thin
    content = extract_content(html, site)
    if len(content.split()) >= min_words:
        return html

    logger.info(
        f"Thin content ({len(content.split())} words) for {url}, "
        f"trying Selenium"
    )
    loop = asyncio.get_running_loop()
    html = await loop.run_in_executor(None, lambda: fetch_page_js(url, site))
    return html


def compute_text_diff(old: str, new: str) -> tuple[str, float]:
    """
    Compute change percentage and a unified diff between old and new content.
    Returns (diff_text, change_pct).
    change_pct is word-level (sensitive to small changes).
    diff_text is line-level (readable in notifications).
    """
    old_words = old.split()
    new_words = new.split()

    # Word-level similarity for percentage
    matcher = difflib.SequenceMatcher(None, old_words, new_words)
    ratio = matcher.ratio()
    change_pct = (1.0 - ratio) * 100

    # Line-level unified diff for human-readable preview
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile="previous",
        tofile="current",
        lineterm=""
    ))
    diff_text = "\n".join(diff[:DIFF_STORE_LINES])
    return diff_text, change_pct


async def check_single_url(url: str, site: dict) -> dict | None:
    """
    Fetches a URL and compares to previous snapshot.

    Returns:
      None                              — no change
      {"url": ..., "diff": ..., ...}    — change detected
      {"error": True, "url": ..., ...}  — fetch failed
    """
    html = await fetch_page(url, site)

    if not html:
        logger.warning(f"Empty fetch for {url}")
        return {"error": True, "url": url, "message": "Empty fetch"}

    content = extract_content(html, site)
    word_count = len(content.split())
    checksum = hashlib.md5(content.encode()).hexdigest()

    prev = get_last_snapshot(url)

    # First visit — save baseline, no notification
    if prev is None:
        save_snapshot(site["name"], url, content, checksum)
        logger.info(f"New baseline: {url} ({word_count} words)")
        return None

    # No change
    if prev["checksum"] == checksum:
        logger.info(f"No change: {url}")
        return None

    # Content changed — compute diff
    diff_text, change_pct = compute_text_diff(prev["content"], content)

    # Silently replace a previously thin baseline with a proper one
    min_words = site.get("min_content_words", MIN_CONTENT_WORDS_DEFAULT)
    if len(prev["content"].split()) < min_words and word_count >= min_words:
        logger.info(
            f"Replacing thin baseline for {url} "
            f"({len(prev['content'].split())} → {word_count} words)"
        )
        save_snapshot(site["name"], url, content, checksum)
        return None

    # Apply min_change_percent filter
    min_pct = site.get("min_change_percent", 0)
    if change_pct < min_pct:
        logger.info(
            f"Change {change_pct:.1f}% below threshold "
            f"{min_pct}% for {url}, skipping"
        )
        return None

    # Real change — save and return for notification
    save_snapshot(site["name"], url, content, checksum)
    save_change(
        site["name"], url,
        prev["checksum"], checksum,
        change_pct, diff_text
    )

    logger.info(f"Change detected: {url} ({change_pct:.1f}%)")
    return {
        "url": url,
        "diff": diff_text,
        "change_pct": change_pct
    }


async def check_site(site: dict, force: bool = False):
    """
    Main entry point for checking a site.
    Called by the scheduler, dashboard button, and Telegram /check command.
    """
    from monitor.scheduler import is_in_window
    from monitor.notify import send_notifications

    name = site["name"]
    schedule_type = site.get("schedule_type", "interval")

    # Honour time window unless forced (e.g. manual check from dashboard)
    if schedule_type == "time_window" and not force:
        in_window, reason = is_in_window(site)
        if not in_window:
            logger.info(f"Outside window: {name} — {reason}")
            return

    # Discover URLs to check
    urls = await crawler.get_pages_to_monitor(site)

    changes  = []
    errors   = []
    successes = []

    for url in urls:
        result = await check_single_url(url, site)
        if result is None:
            successes.append(url)
        elif result.get("error"):
            errors.append(result)
        else:
            changes.append(result)
            successes.append(url)

    # Send notifications for any changes
    if changes:
        await send_notifications(site, changes)

    # Log job outcome
    total = len(urls)
    if successes or changes:
        log_job(
            name, "success",
            f"Checked {total} URLs, {len(changes)} changes, "
            f"{len(errors)} errors"
        )
    elif errors:
        error_msgs = "; ".join(e.get("message", "unknown") for e in errors)
        log_job(
            name, "error",
            f"All {len(errors)} URLs failed: {error_msgs}"
        )
    else:
        log_job(name, "success", f"Checked {total} URLs")
