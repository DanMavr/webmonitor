"""
capture.py — screenshot capture using Playwright with system Chromium fallback.

Playwright on Raspberry Pi (ARM64):
  pip install playwright
  playwright install chromium          # downloads ARM64 Chromium build
  playwright install-deps chromium     # installs system dependencies

If Playwright's bundled Chromium fails on your system, set env var:
  USE_SYSTEM_CHROMIUM=1
This makes Playwright use the system-installed Chromium:
  sudo apt install chromium-browser    # Raspberry Pi OS / Debian
"""

import os
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("monitor")

# Detect whether to force system Chromium (e.g. on Raspberry Pi if bundled fails)
USE_SYSTEM_CHROMIUM = os.getenv("USE_SYSTEM_CHROMIUM", "0") == "1"

# Common system Chromium paths on Raspberry Pi OS / Debian / Ubuntu ARM
SYSTEM_CHROMIUM_PATHS = [
    "/usr/bin/chromium-browser",    # Raspberry Pi OS / Ubuntu
    "/usr/bin/chromium",            # Debian
    "/snap/bin/chromium",           # Snap install
]


def _find_system_chromium() -> str | None:
    for path in SYSTEM_CHROMIUM_PATHS:
        if Path(path).exists():
            return path
    return None


async def take_screenshot(url: str, clip: dict | None, js_wait: float = 3.0) -> bytes | None:
    """
    Navigate to url using a headless Chromium browser, wait for the page to
    settle, then return a PNG screenshot as bytes.

    clip  — optional dict with keys x, y, width, height (page coordinates).
            If None, a full-page screenshot is taken (used for setup/preview).
    """
    try:
        from playwright.async_api import async_playwright

        launch_kwargs = {
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
                # networkidle can time out on heavy SPAs; fall back to load
                try:
                    await page.goto(url, wait_until="load", timeout=30_000)
                    await asyncio.sleep(js_wait)
                except Exception as e:
                    logger.error(f"Navigation failed for {url}: {e}")
                    await browser.close()
                    return None

            # Extra wait for JS-heavy pages (React hydration etc.)
            await asyncio.sleep(js_wait)

            screenshot_kwargs = {"full_page": clip is None}
            if clip:
                screenshot_kwargs["clip"] = clip
                screenshot_kwargs["full_page"] = False

            png = await page.screenshot(**screenshot_kwargs)
            await browser.close()
            return png

    except Exception as e:
        logger.error(f"Screenshot failed for {url}: {e}")
        return None


def take_screenshot_sync(url: str, clip: dict | None, js_wait: float = 3.0) -> bytes | None:
    """Synchronous wrapper for use in Flask routes (non-async context)."""
    return asyncio.run(take_screenshot(url, clip, js_wait))
