import re
import time
import logging
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


def get_pages_to_monitor(site: dict) -> list[str]:
    mode = site.get("mode", "single_page")

    if mode == "single_page":
        return [site["url"]]

    elif mode == "whole_site":
        crawl_cfg = site.get("crawl", {})
        base_url = site["url"]

        if crawl_cfg.get("use_sitemap", True):
            urls = get_urls_from_sitemap(base_url)
            if urls:
                logger.info(f"Found {len(urls)} URLs from sitemap")
                return filter_urls(urls, base_url, crawl_cfg)

        logger.info(f"No sitemap found, crawling {base_url}")
        urls = crawl_site(base_url, crawl_cfg)
        return filter_urls(urls, base_url, crawl_cfg)

    raise ValueError(f"Unknown mode: {mode}")


def get_urls_from_sitemap(base_url: str) -> list[str]:
    domain = urlparse(base_url).netloc
    scheme = urlparse(base_url).scheme
    sitemap_url = f"{scheme}://{domain}/sitemap.xml"

    try:
        resp = requests.get(sitemap_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception:
        return []

    try:
        root = ElementTree.fromstring(resp.content)
    except ElementTree.ParseError:
        return []

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = []

    sitemap_tags = root.findall("sm:sitemap", ns)
    if sitemap_tags:
        for sitemap in sitemap_tags[:10]:
            loc = sitemap.find("sm:loc", ns)
            if loc is not None:
                child_urls = _parse_sitemap_xml(loc.text)
                urls.extend(child_urls)
    else:
        urls = _parse_sitemap_xml(sitemap_url)

    return urls


def _parse_sitemap_xml(url: str) -> list[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        root = ElementTree.fromstring(resp.content)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        return [
            loc.text for loc in root.findall(".//sm:loc", ns)
            if loc.text
        ]
    except Exception:
        return []


def crawl_site(base_url: str, config: dict) -> list[str]:
    domain = urlparse(base_url).netloc
    max_pages = config.get("max_pages", 100)
    delay = config.get("delay_seconds", 2)

    visited = set()
    to_visit = {base_url}
    found = []

    while to_visit and len(found) < max_pages:
        url = to_visit.pop()

        if url in visited:
            continue

        visited.add(url)

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)

            if "text/html" not in resp.headers.get("Content-Type", ""):
                continue

            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            found.append(url)

            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                absolute = urljoin(url, href)
                parsed = urlparse(absolute)

                if parsed.netloc == domain and absolute not in visited:
                    clean = absolute.split("#")[0]
                    to_visit.add(clean)

            time.sleep(delay)

        except Exception as e:
            logger.debug(f"Skipping {url}: {e}")
            continue

    logger.info(f"Crawl complete: {len(found)} pages found")
    return found


def filter_urls(urls: list[str], base_url: str, config: dict) -> list[str]:
    domain = urlparse(base_url).netloc
    exclude_patterns = config.get("exclude_patterns", [])
    max_pages = config.get("max_pages", 100)

    filtered = []

    for url in urls:
        parsed = urlparse(url)

        if config.get("stay_on_domain", True):
            if parsed.netloc and parsed.netloc != domain:
                continue

        skip = False
        for pattern in exclude_patterns:
            if pattern.lower() in url.lower():
                skip = True
                break

        if not skip:
            filtered.append(url)

    seen = set()
    unique = []
    for url in filtered:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    return unique[:max_pages]
