import time
import logging
import asyncio
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    )
}


# ── Internal sync HTTP helper ─────────────────────────────────────────────────

def _fetch_url(
    url: str,
    timeout: int = 15,
    verify_ssl: bool = True
) -> requests.Response | None:
    """
    Synchronous HTTP GET.
    MUST be called via run_in_executor — never call directly in async context.

    verify_ssl: set to False for sites with self-signed or untrusted certificates
                (configured per-site via ssl_verify: false in sites.yaml).
    """
    try:
        resp = requests.get(
            url,
            headers=HEADERS,
            timeout=timeout,
            verify=verify_ssl
        )
        return resp if resp.status_code == 200 else None
    except Exception as e:
        logger.warning(f"Fetch failed for {url}: {e}")
        return None


# ── Public entry point ────────────────────────────────────────────────────────

async def get_pages_to_monitor(site: dict) -> list[str]:
    """
    Returns the list of URLs to check for a site.

    single_page  → just the main URL
    whole_site   → sitemap or link-crawl discovery
    """
    mode = site.get("mode", "single_page")

    if mode == "single_page":
        return [site["url"]]

    # whole_site
    urls = await crawl_site(site)

    if not urls:
        logger.warning(
            f"No URLs found for {site['name']}, "
            "falling back to root URL"
        )
        return [site["url"]]

    return urls


# ── Sitemap fetching ──────────────────────────────────────────────────────────

async def get_urls_from_sitemap(base_url: str, config: dict) -> list[str]:
    """
    Fetch and parse /sitemap.xml for the given base URL.
    Returns a filtered list of page URLs.
    All HTTP I/O is non-blocking via run_in_executor.
    """
    parsed      = urlparse(base_url)
    sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
    verify_ssl  = config.get("ssl_verify", True)

    loop = asyncio.get_running_loop()
    resp = await loop.run_in_executor(
        None,
        lambda: _fetch_url(sitemap_url, verify_ssl=verify_ssl)
    )

    if resp is None:
        logger.warning(f"Sitemap fetch failed for {base_url}")
        return []

    return _parse_sitemap_xml(resp.text, config)


def _parse_sitemap_xml(xml_text: str, config: dict) -> list[str]:
    """
    Parse sitemap XML and return a filtered, capped list of page URLs.
    Handles XML namespaces automatically.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as e:
        logger.warning(f"Sitemap XML parse error: {e}")
        return []

    # Detect and handle XML namespace (e.g. {http://www.sitemaps.org/...})
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    exclude   = config.get("crawl", {}).get("exclude_patterns", [])
    max_pages = config.get("crawl", {}).get("max_pages", 50)

    urls = []
    for url_elem in root.iter(f"{ns}url"):
        loc = url_elem.find(f"{ns}loc")
        if loc is None or not loc.text:
            continue

        url = loc.text.strip()

        if any(pat.lower() in url.lower() for pat in exclude):
            continue

        urls.append(url)
        if len(urls) >= max_pages:
            break

    return urls


# ── Link crawl fallback ───────────────────────────────────────────────────────

async def crawl_site(site: dict) -> list[str]:
    """
    Discover pages to monitor for a site.
    Tries sitemap first; falls back to recursive link-following.
    All HTTP I/O is non-blocking via run_in_executor.
    """
    base_url       = site["url"]
    crawl_config   = site.get("crawl", {})
    use_sitemap    = crawl_config.get("use_sitemap", True)
    max_pages      = crawl_config.get("max_pages", 50)
    stay_on_domain = crawl_config.get("stay_on_domain", True)
    exclude        = crawl_config.get("exclude_patterns", [])
    verify_ssl     = site.get("ssl_verify", True)

    if use_sitemap:
        urls = await get_urls_from_sitemap(base_url, site)
        if urls:
            logger.info(
                f"Found {len(urls)} URLs from sitemap for {site['name']}"
            )
            return urls
        logger.info(
            f"Sitemap empty/failed for {site['name']}, "
            "falling back to link crawl"
        )

    # Link-crawl fallback — all requests run in executor
    loop     = asyncio.get_running_loop()
    visited  = set()
    to_visit = [base_url]
    found    = []

    while to_visit and len(found) < max_pages:
        url = to_visit.pop(0)

        if url in visited:
            continue
        visited.add(url)

        resp = await loop.run_in_executor(
            None,
            lambda u=url: _fetch_url(u, verify_ssl=verify_ssl)
        )

        if resp is None:
            continue

        soup = BeautifulSoup(resp.text, "lxml")
        found.append(url)

        for a in soup.find_all("a", href=True):
            href   = urljoin(url, a["href"])
            parsed = urlparse(href)

            # Optionally restrict to same domain
            if stay_on_domain:
                base_parsed = urlparse(base_url)
                if parsed.netloc != base_parsed.netloc:
                    continue

            # Strip fragment and query for deduplication
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

            if clean not in visited:
                if not any(pat.lower() in clean.lower() for pat in exclude):
                    to_visit.append(clean)

        # Polite crawl delay — in executor so it does not block event loop
        await loop.run_in_executor(None, lambda: time.sleep(0.5))

    logger.info(
        f"Link crawl complete for {site['name']}: {len(found)} pages found"
    )
    return found


# ── Utility ───────────────────────────────────────────────────────────────────

def filter_urls(urls: list[str], site: dict) -> list[str]:
    """Filter a URL list by the site's configured exclusion patterns."""
    exclude = site.get("crawl", {}).get("exclude_patterns", [])
    if not exclude:
        return urls
    return [
        u for u in urls
        if not any(pat.lower() in u.lower() for pat in exclude)
    ]
