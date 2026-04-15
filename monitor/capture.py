"""
capture.py — full-page screenshot using Selenium + system Chromium.

System requirements:
  sudo apt install chromium-browser chromium-driver
"""
from __future__ import annotations

import logging
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger("monitor")

_CHROMIUM  = ["/usr/bin/chromium-browser", "/usr/bin/chromium", "/snap/bin/chromium"]
_CDRIVERS  = ["/usr/bin/chromedriver", "/usr/lib/chromium-browser/chromedriver",
              "/usr/lib/chromium/chromedriver"]

# Common cookie-banner "accept" selectors tried in order
_COOKIE_SELECTORS = [
    "#onetrust-accept-btn-handler",
    ".cc-accept",
    "button[id*='accept']",
    "button[class*='accept']",
    "button[aria-label*='accept' i]",
    "button[aria-label*='agree' i]",
    "[id*='cookie'] button",
    "[id*='consent'] button",
    "[id*='gdpr'] button",
    "button[class*='CybotCookiebotDialogBodyButton']",
    "button[data-testid*='accept']",
]


def _find(paths: list) -> Optional[str]:
    return next((p for p in paths if Path(p).exists()), None)


def _make_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    opts = Options()
    for arg in ["--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-gpu", "--window-size=1280,900",
                "--ignore-certificate-errors"]:
        opts.add_argument(arg)
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux armv7l) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    chromium = _find(_CHROMIUM)
    if chromium:
        opts.binary_location = chromium
    driver_path = _find(_CDRIVERS)
    service = Service(executable_path=driver_path) if driver_path else Service()
    return webdriver.Chrome(service=service, options=opts)


def _dismiss_cookies(driver) -> None:
    from selenium.webdriver.common.by import By
    for sel in _COOKIE_SELECTORS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                if el.is_displayed() and el.is_enabled():
                    driver.execute_script("arguments[0].click();", el)
                    logger.info(f"Cookie banner dismissed: {sel}")
                    time.sleep(0.5)
                    return
        except Exception:
            continue


def _full_page_png(driver) -> bytes:
    """Resize window to full page height, screenshot, restore."""
    h = driver.execute_script(
        "return Math.max(document.body.scrollHeight,"
        "document.documentElement.scrollHeight,"
        "document.body.offsetHeight,"
        "document.documentElement.offsetHeight);"
    )
    w = driver.execute_script(
        "return Math.max(document.body.scrollWidth,"
        "document.documentElement.scrollWidth,"
        "document.body.offsetWidth,"
        "document.documentElement.offsetWidth);"
    )
    driver.set_window_size(min(int(w), 1920), min(int(h), 15000))
    time.sleep(0.3)
    png = driver.get_screenshot_as_png()
    driver.set_window_size(1280, 900)
    return png


def take_screenshot(url: str, clip: Optional[dict] = None,
                    js_wait: float = 3.0) -> Optional[bytes]:
    """
    Navigate to url, wait js_wait seconds, dismiss cookie banners,
    take a full-page screenshot, optionally crop to clip region.
    Returns PNG bytes or None on failure.
    """
    driver = None
    try:
        driver = _make_driver()
        driver.set_page_load_timeout(30)
        driver.get(url)
        time.sleep(js_wait)
        _dismiss_cookies(driver)
        time.sleep(0.5)
        png = _full_page_png(driver)
        if clip:
            img = Image.open(BytesIO(png)).convert("RGB")
            x, y, w, h = int(clip["x"]), int(clip["y"]), int(clip["width"]), int(clip["height"])
            cropped = img.crop((x, y, x + w, y + h))
            buf = BytesIO()
            cropped.save(buf, format="PNG")
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


# Alias for compatibility — Flask and core both call the same function
take_screenshot_sync = take_screenshot
