"""
capture.py — screenshot capture using Playwright with system Chromium fallback.

Playwright on Raspberry Pi (ARM64):
  pip install playwright
  playwright install chromium
  playwright install-deps chromium     # run as sudo if needed

If Playwright's bundled Chromium fails on your system, set in .env:
  USE_SYSTEM_CHROMIUM=1
Then install system Chromium:
  sudo apt install chromium-browser    # Raspberry Pi OS / Debian
"""
from __future__ import annotations

import os
import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("monitor")

USE_SYSTEM_CHROMIUM = os.getenv("USE_SYSTEM_CHROMIUM", "0") == "1"

SYSTEM_CHROMIUM_PATHS = [
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/snap/bin/chromium",
]


def _find_system_chromium() -> Optional[str]:
    for path in SYSTEM_CHROMIUM_PATHS:
        if Path(path).exists():
            return path
    return None


async def take_screenshot(url: str, clip: Optional[dict], js_wait: float = 3.0) -> Optional[bytes]:
    """
    Navigate to url, wait for the page to settle, return a PNG screenshot as bytes.
    clip — dict with keys x, y, width, height. If None, full-page screenshot is taken.
    """
    try:
        from playwright.async_api import async_playwright

        launch_kwargs: dict = {
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ]
        }

        if USE_SYSTEM_CHROMIUM:
            chromium_path = _find_system_chromium()
            if chromium_path:
                launch_kwargs["executable_path"] = chromium_path
                logger.info(f"Using system Chromium: {chromium_path}")
            else:
                logger.warning("USE_SYSTEM_CHROMIUM set but no system Chromium found — using bundled")

        async with async_playwright() as p:
            browser = await p.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                ignore_https_errors=True,
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="networkidle", timeout=30_000)
            except Exception:
                try:
                    await page.goto(url, wait_until="load", timeout=30_000)
                    await asyncio.sleep(js_wait)
                except Exception as e:
                    logger.error(f"Navigation failed for {url}: {e}")
                    await browser.close()
                    return None

            await asyncio.sleep(js_wait)

            screenshot_kwargs: dict = {"full_page": clip is None}
            if clip:
                screenshot_kwargs["clip"] = clip
                screenshot_kwargs["full_page"] = False

            png = await page.screenshot(**screenshot_kwargs)
            await browser.close()
            return png

    except Exception as e:
        logger.error(f"Screenshot failed for {url}: {e}")
        return None


def take_screenshot_sync(url: str, clip: Optional[dict], js_wait: float = 3.0) -> Optional[bytes]:
    """Synchronous wrapper — safe to call from Flask routes (non-async context)."""
    return asyncio.run(take_screenshot(url, clip, js_wait))
