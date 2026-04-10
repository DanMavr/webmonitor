import hashlib
import difflib
import logging
import time
from pathlib import Path
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from rich.console import Console

from monitor.storage import (
    get_last_snapshot, save_snapshot,
    save_change, log_job
)
from monitor import crawler

console = Console()
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    )
}

CHROMIUM_BINARY  = "/usr/bin/chromium-browser"
CHROMEDRIVER_BIN = "/usr/bin/chromedriver"

# If requests returns fewer words than this, retry with Selenium
MIN_CONTENT_WORDS_DEFAULT = 50


def extract_content(html: str, site: dict) -> str:
    """
    Extracts meaningful text from a page.
    Respects watch_selectors and ignore_selectors from config.
    """
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "meta", "noscript"]):
        tag.decompose()

    for selector in site.get("ignore_selectors", []):
        for el in soup.select(selector):
            el.decompose()

    watch = site.get("watch_selectors", [])
    if watch:
        parts = []
        for selector in watch:
            for el in soup.select(selector):
                text = el.get_text(separator="\n", strip=True)
                if text:
                    parts.append(text)
        content = "\n\n".join(parts)
    else:
        body = soup.find("body")
        content = body.get_text(separator="\n", strip=True) if body else ""

    lines = [l.strip() for l in content.splitlines() if l.strip()]
    return "\n".join(lines)


async def fetch_page_js(url: str, site: dict) -> tuple[str, str]:
    """
    Fetches a JS-rendered page using Selenium + system Chromium.
    Uses asyncio.sleep instead of time.sleep to avoid blocking
    the event loop while waiting for JS to render.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.binary_location = CHROMIUM_BINARY

    service = Service(CHROMEDRIVER_BIN)

    # Run the blocking Selenium calls in a thread pool
    # so the asyncio event loop is never blocked
    loop = asyncio.get_event_loop()

    def run_selenium():
        driver = webdriver.Chrome(service=service, options=options)
        try:
            driver.get(url)
            time.sleep(site.get("js_wait_seconds", 5))
            return driver.page_source
        finally:
            driver.quit()

    html = await loop.run_in_executor(None, run_selenium)
    content = extract_content(html, site)
    checksum = hashlib.md5(content.encode()).hexdigest()
    return content, checksum


def fetch_page(url: str, site: dict) -> tuple[str, str]:
    """
    Fetches a page and returns (content, checksum).

    Strategy:
      1. Always try requests first (fast, lightweight)
      2. If content is below min_content_words threshold,
         automatically retry with Selenium (JS rendering)
      3. javascript: true in config forces Selenium always,
         skipping the requests attempt entirely
    """
    if site.get("javascript"):
        logger.debug(f"JS mode forced for {url}")
        return fetch_page_js(url, site)

    min_words = site.get("min_content_words", MIN_CONTENT_WORDS_DEFAULT)

    try:
        response = requests.get(url, headers=HEADERS, timeout=30, verify=True)
        response.raise_for_status()
        content = extract_content(response.text, site)
        word_count = len(content.split())

        if word_count >= min_words:
            checksum = hashlib.md5(content.encode()).hexdigest()
            return content, checksum

        logger.info(
            f"Thin content ({word_count} words) from requests for {url} "
            f"— retrying with Selenium"
        )
        console.log(
            f"  [yellow]⚡ JS fallback[/yellow] — "
            f"only {word_count} words via requests, retrying with Selenium"
        )

    except requests.exceptions.SSLError:
        logger.warning(
            f"SSL verification failed for {url}, retrying without verification"
        )
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            response = requests.get(
                url, headers=HEADERS, timeout=30, verify=False
            )
            response.raise_for_status()
            content = extract_content(response.text, site)
            word_count = len(content.split())

            if word_count >= min_words:
                checksum = hashlib.md5(content.encode()).hexdigest()
                return content, checksum

            logger.info(
                f"Thin content ({word_count} words) after SSL retry for {url} "
                f"— retrying with Selenium"
            )

        except Exception as e:
            raise Exception(f"Failed even without SSL verification: {e}")

    except requests.exceptions.ConnectionError as e:
        raise Exception(f"Connection failed: {e}")

    except requests.exceptions.Timeout:
        raise Exception(f"Timed out connecting to {url}")

    except requests.exceptions.HTTPError as e:
        raise Exception(f"HTTP error {e.response.status_code}: {url}")

    try:
        return fetch_page_js(url, site)
    except Exception as e:
        raise Exception(f"JS fallback also failed for {url}: {e}")


def compute_text_diff(old_text: str, new_text: str) -> dict:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    diff = list(difflib.unified_diff(
        old_lines, new_lines, lineterm="", n=2
    ))

    added = [
        l[1:] for l in diff
        if l.startswith("+") and not l.startswith("+++")
    ]
    removed = [
        l[1:] for l in diff
        if l.startswith("-") and not l.startswith("---")
    ]

    ratio = difflib.SequenceMatcher(None, old_text, new_text).ratio()

    return {
        "type": "text",
        "added": added,
        "removed": removed,
        "added_count": len(added),
        "removed_count": len(removed),
        "change_percent": round((1 - ratio) * 100, 2),
        "unified_diff": "\n".join(diff[:80]),
    }


async def check_single_url(url: str, site: dict) -> dict | None:
    """
    Checks one URL for changes.

    Returns:
      None                        — fetched cleanly, no change
      {"url": ..., "diff": ...}   — fetched cleanly, change detected
      {"error": True, "url": ..., "message": ...}  — fetch failed
    """
    try:
        content, checksum = fetch_page(url, site)
        word_count = len(content.split())
        min_words = site.get("min_content_words", MIN_CONTENT_WORDS_DEFAULT)
        last = get_last_snapshot(site["name"], url)

        # No existing snapshot — save as fresh baseline
        if last is None:
            save_snapshot(site["name"], url, content, checksum)
            console.log(
                f"  [green]✓[/green] Baseline saved — "
                f"[dim]{url[:60]}[/dim]"
            )
            return None

        last_word_count = len((last["content"] or "").split())

        # Existing snapshot is thin/empty — replace silently as new baseline
        if last_word_count < min_words and word_count >= min_words:
            save_snapshot(site["name"], url, content, checksum)
            console.log(
                f"  [green]✓[/green] Baseline replaced "
                f"({last_word_count} → {word_count} words) — "
                f"[dim]{url[:60]}[/dim]"
            )
            return None

        # Both old and new are thin — log as warning but don't error
        # the whole site just because one page has low content
        if last_word_count < min_words and word_count < min_words:
            console.log(
                f"  [yellow]⚠[/yellow] Thin content "
                f"({word_count} words) — skipping — "
                f"[dim]{url[:60]}[/dim]"
            )
            return None

        # Normal comparison
        if last["checksum"] == checksum:
            return None

        diff = compute_text_diff(last["content"], content)

        if diff["change_percent"] < site.get("min_change_percent", 1):
            save_snapshot(site["name"], url, content, checksum)
            return None

        save_snapshot(site["name"], url, content, checksum)
        save_change(
            site["name"], url,
            last["checksum"], checksum,
            str(diff["change_percent"]),
            diff["unified_diff"]
        )
        return {"url": url, "diff": diff}

    except Exception as e:
        logger.warning(f"Failed to check {url}: {e}")
        return {"error": True, "url": url, "message": str(e)}


async def check_site(site: dict, force: bool = False):
    """
    Main function called by the scheduler for each site.
    Respects time windows before checking, and correctly
    distinguishes fetch errors from clean no-change runs.

    force=True bypasses the time window check — used by
    the dashboard "Check All Now" button.
    """
    from monitor.scheduler import is_in_window

    name = site["name"]
    mode = site.get("mode", "single_page")

    if not force:
        should_check, interval = is_in_window(site)
        if not should_check:
            logger.debug(f"Outside window, skipping: {name}")
            return
    else:
        console.log(f"  [dim](forced — bypassing time window)[/dim]")

    console.log(f"[cyan]Checking:[/cyan] {name}")

    try:
        urls = crawler.get_pages_to_monitor(site)

        if mode == "whole_site":
            console.log(f"  [dim]Found {len(urls)} pages[/dim]")

        errors = []
        changes_found = []

        for url in urls:
            result = await check_single_url(url, site)
            if result is None:
                pass
            elif result.get("error"):
                errors.append(result)
            else:
                changes_found.append(result)

        # Only error if ALL urls failed — partial failures are warnings
        if errors and not changes_found:
            # Check if there were also successful (None) results
            # If so, some pages were fine — don't mark as error
            total_urls = len(urls)
            error_count = len(errors)
            if error_count == total_urls:
                # Every single URL failed
                error_msgs = "; ".join(
                    f"{r['url'][:50]}: {r['message']}" for r in errors
                )
                console.log(f"  [red]-> All fetches failed:[/red] {error_msgs}")
                log_job(name, "error", f"All fetches failed: {error_msgs}")
                return
            else:
                # Some failed, some were just thin/skipped — still log success
                logger.warning(
                    f"{error_count}/{total_urls} pages had errors for {name}"
                )

        if not changes_found:
            console.log(f"  [dim]-> No changes[/dim]")
            log_job(name, "success", "No change")
            return

        from monitor import notify

        if mode == "whole_site":
            await notify.notify_site_summary(site, changes_found)
        else:
            await notify.send_notifications(
                site, changes_found[0]["diff"],
                changes_found[0]["url"]
            )

        log_job(name, "success", f"{len(changes_found)} page(s) changed")

    except Exception as e:
        console.log(f"[red]Error:[/red] {name} - {e}")
        log_job(name, "error", str(e))
