import hashlib
import difflib
import logging
import time
import asyncio
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

MIN_CONTENT_WORDS_DEFAULT = 50


def extract_content(html: str, site: dict) -> str:
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
    Runs Selenium in a thread pool executor so the asyncio event
    loop is never blocked while waiting for JS to render.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    wait = site.get("js_wait_seconds", 5)

    def run_selenium():
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
            time.sleep(wait)
            return driver.page_source
        finally:
            driver.quit()

    loop = asyncio.get_event_loop()
    html = await loop.run_in_executor(None, run_selenium)
    content = extract_content(html, site)
    checksum = hashlib.md5(content.encode()).hexdigest()
    return content, checksum


async def fetch_page(url: str, site: dict) -> tuple[str, str]:
    """
    Fetches a page and returns (content, checksum).
    Tries requests first, falls back to Selenium if content is thin.
    javascript: true in config forces Selenium always.
    """
    if site.get("javascript"):
        logger.debug(f"JS mode forced for {url}")
        return await fetch_page_js(url, site)

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
        return await fetch_page_js(url, site)
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
    try:
        content, checksum = await fetch_page(url, site)
        word_count = len(content.split())
        min_words = site.get("min_content_words", MIN_CONTENT_WORDS_DEFAULT)
        last = get_last_snapshot(site["name"], url)

        if last is None:
            save_snapshot(site["name"], url, content, checksum)
            console.log(
                f"  [green]✓[/green] Baseline saved — "
                f"[dim]{url[:60]}[/dim]"
            )
            return None

        last_word_count = len((last["content"] or "").split())

        if last_word_count < min_words and word_count >= min_words:
            save_snapshot(site["name"], url, content, checksum)
            console.log(
                f"  [green]✓[/green] Baseline replaced "
                f"({last_word_count} → {word_count} words) — "
                f"[dim]{url[:60]}[/dim]"
            )
            return None

        if last_word_count < min_words and word_count < min_words:
            console.log(
                f"  [yellow]⚠[/yellow] Thin content "
                f"({word_count} words) — skipping — "
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


async def check_site(site: dict, force: bool = False):
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

        if errors and not changes_found:
            total_urls = len(urls)
            error_count = len(errors)
            if error_count == total_urls:
                error_msgs = "; ".join(
                    f"{r['url'][:50]}: {r['message']}" for r in errors
                )
                console.log(f"  [red]-> All fetches failed:[/red] {error_msgs}")
                log_job(name, "error", f"All fetches failed: {error_msgs}")
                return
            else:
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
