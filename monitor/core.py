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
            f"  JS fallback ({word_count} words): {url[:80]}"
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
                f"  JS fallback after SSL retry ({word_count} words): {url[:80]}"
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
        if l.startswith("+
