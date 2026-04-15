cat > monitor/capture.py << 'CAPTURE_EOF'
"""
capture.py — screenshot using Selenium + system Chromium.
"""
from __future__ import annotations

import asyncio
import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger("monitor")

SYSTEM_CHROMIUM_PATHS = [
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/snap/bin/chromium",
]

SYSTEM_CHROMEDRIVER_PATHS = [
    "/usr/bin/chromedriver",
    "/usr/lib/chromium-browser/chromedriver",
    "/usr/lib/chromium/chromedriver",
]

_COOKIE_SELECTORS = [
    "button[id*='accept']",
    "button[class*='accept']",
    "button[aria-label*='accept' i]",
    "button[aria-label*='agree' i]",
    "a[id*='accept']",
    "a[class*='accept']",
    "[id*='cookie'] button",
    "[class*='cookie'] button",
    "[id*='consent'] button",
    "[class*='consent'] button",
    "[id*='gdpr'] button",
    "button[class*='CybotCookiebotDialogBodyButton']",
    "#onetrust-accept-btn-handler",
    ".cc-accept",
    "button[data-testid*='accept']",
]


def _find(paths: list[str]) -> Optional[str]:
    for p in paths:
        if Path(p).exists():
            return p
    return None


def _make_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux armv7l) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    opts.add_argument("--ignore-certificate-errors")

    chromium = _find(SYSTEM_CHROMIUM_PATHS)
    if chromium:
        opts.binary_location = chromium
        logger.info(f"Chromium: {chromium}")

    chromedriver = _find(SYSTEM_CHROMEDRIVER_PATHS)
    service = Service(executable_path=chromedriver) if chromedriver else Service()
    if chromedriver:
        logger.info(f"chromedriver: {chromedriver}")

    return webdriver.Chrome(service=service, options=opts)


def _dismiss_cookies(driver) -> None:
    from selenium.webdriver.common.by import By
    for selector in _COOKIE_SELECTORS:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, selector)
            for el in els:
                if el.is_displayed() and el.is_enabled():
                    driver.execute_script("arguments[0].click();", el)
                    logger.info(f"Cookie banner dismissed via: {selector}")
                    time.sleep(0.5)
                    return
        except Exception:
            continue


def _full_page_screenshot(driver) -> bytes:
    total_height = driver.execute_script(
        "return Math.max(document.body.scrollHeight, "
        "document.documentElement.scrollHeight, "
        "document.body.offsetHeight, "
        "document.documentElement.offsetHeight);"
    )
    total_width = driver.execute_script(
        "return Math.max(document.body.scrollWidth, "
        "document.documentElement.scrollWidth, "
        "document.body.offsetWidth, "
        "document.documentElement.offsetWidth);"
    )
    total_height = min(int(total_height), 15000)
    total_width  = min(int(total_width),  1920)
    driver.set_window_size(total_width, total_height)
    time.sleep(0.3)
    png = driver.get_screenshot_as_png()
    driver.set_window_size(1280, 900)
    return png


def _capture(url: str, clip: Optional[dict], js_wait: float) -> Optional[bytes]:
    driver = None
    try:
        driver = _make_driver()
        driver.set_page_load_timeout(30)
        driver.get(url)
        time.sleep(js_wait)
        _dismiss_cookies(driver)
        time.sleep(0.5)
        png = _full_page_screenshot(driver)
        if clip:
            img = Image.open(BytesIO(png)).convert("RGB")
            x, y, w, h = int(clip["x"]), int(clip["y"]), int(clip["width"]), int(clip["height"])
            img = img.crop((x, y, x + w, y + h))
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        return png
    except Exception as e:
        logger.error(f"Screenshot failed for {url}: {e}")
        return None
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


async def take_screenshot(url: str, clip: Optional[dict], js_wait: float = 3.0) -> Optional[bytes]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _capture, url, clip, js_wait)


def take_screenshot_sync(url: str, clip: Optional[dict], js_wait: float = 3.0) -> Optional[bytes]:
    return _capture(url, clip, js_wait)
CAPTURE_EOF
