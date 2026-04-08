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


def extract_content(html: str, site: dict) -> str:
    """
    Extracts meaningful text from a page.
    Respects watch_selectors and ignore_selectors from config.
    """
    soup = BeautifulSoup(html, "lxml")

    # Always strip these
    for tag in soup(["script", "style", "meta", "noscript"]):
        tag.decompose()

    # Remove ignored elements
    for selector in site.get("ignore_selectors", []):
        for el in soup.select(selector):
            el.decompose()

    # Watch specific selectors only (if defined)
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


def fetch_page(url: str, site: dict) -> tuple[str, str]:
    """
    Fetches a page and returns (content, checksum).
    Automatically retries without SSL verification if certificate fails.
    """
    headers = HEADERS

    try:
        response = requests.get(url, headers=headers, timeout=30, verify=True)
        response.raise_for_status()
        content = extract_content(response.text, site)
        checksum = hashlib.md5(content.encode()).hexdigest()
        return content, checksum

    except requests.exceptions.SSLError:
        logger.warning(
            f"SSL verification failed for {url}, retrying without verification"
        )
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            response = requests.get(url, headers=headers, timeout=30, verify=False)
            response.raise_for_status()
            content = extract_content(response.text, site)
            checksum = hashlib.md5(content.encode()).hexdigest()
            return content, checksum
        except Exception as e:
            raise Exception(f"Failed even without SSL verification: {e}")

    except requests.exceptions.ConnectionError as e:
        raise Exception(f"Connection failed: {e}")

    except requests.exceptions.Timeout:
        raise Exception(f"Timed out connecting to {url}")

    except requests.exceptions.HTTPError as e:
        raise Exception(f"HTTP error {e.response.status_code}: {url}")


def fetch_page_js(url: str, site: dict) -> tuple[str, str]:
    """
    Fetches a JS-rendered page using Selenium + system Chromium.
    Used when a site has javascript: true in config.
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
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.get(url)
        # Wait for JS to render — use js_wait_seconds from config or default 5
        wait = site.get("js_wait_seconds", 5)
        time.sleep(wait)
        html = driver.page_source
    finally:
        driver.quit()

    content = extract_content(html, site)
    checksum = hashlib.md5(content.encode()).hexdigest()
    return content, checksum


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
        if site.get("javascript"):
            content, checksum = fetch_page_js(url, site)
        else:
            content, checksum = fetch_page(url, site)

        last = get_last_snapshot(site["name"], url)

        if last is None:
            save_snapshot(site["name"], url, content, checksum)
            console.log(
                f"  [green]✓[/green] Baseline saved — "
                f"[dim]{url[:60]}[/dim]"
            )
            return None

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


async def check_site(site: dict):
    """
    Main function called by the scheduler for each site.
    Respects time windows before checking, and correctly
    distinguishes fetch errors from clean no-change runs.
    """
    from monitor.scheduler import is_in_window

    name = site["name"]
    mode = site.get("mode", "single_page")

    should_check, interval = is_in_window(site)

    if not should_check:
        logger.debug(f"Outside window, skipping: {name}")
        return

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

        # All URLs failed — log as error
        if errors and not changes_found:
            error_msgs = "; ".join(
                f"{r['url'][:50]}: {r['message']}" for r in errors
            )
            console.log(f"  [red]-> Fetch failed:[/red] {error_msgs}")
            log_job(name, "error", f"Fetch failed: {error_msgs}")
            return

        # Some URLs failed but others were clean — warn and continue
        if errors:
            error_msgs = "; ".join(
                f"{r['url'][:50]}: {r['message']}" for r in errors
            )
            logger.warning(f"Partial fetch errors for {name}: {error_msgs}")

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
