"""AbeBooks search.

AbeBooks doesn't offer a public API — their API is only issued to approved
booksellers. This module scrapes the public search-results HTML instead, which
means: (1) it's fragile to markup changes (their `data-cy` attributes changed
to `data-test-id` at some point after this was first written — selectors below
are confirmed against a live response, but may drift again), (2) check
abebooks.com/robots.txt and their terms before relying on this for anything
beyond light personal use, and (3) if they start serving a CAPTCHA/bot-check
page, that's a hard stop — this tool does not attempt to solve or bypass it.

If this becomes a bottleneck, an aggregator like bookfinder.com or vialibri.net
(which both index AbeBooks alongside other dealers) may be a sturdier target.
"""

import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

_SEARCH_URL = "https://www.abebooks.com/servlet/SearchResults"
_HEADERS = {"User-Agent": "book-cover-matcher/0.1 (personal research tool)"}
_NO_IMAGE_MARKER = "no-book-image"

# Listing cards don't have a dedicated year field, but the publisher line
# reads "Published by <name>, <year>" — confirmed against live listings
# (e.g. "Published by Penguin Books, Limited, 2001"; non-greedy + end anchor
# correctly takes the LAST ", YYYY" even when the publisher name itself has
# commas in it).
_PUBLISHED_BY_RE = re.compile(r"^Published by\s+(.*?),\s*(\d{4})$")


def _real_image_url(img_el):
    """Listings without a real photo get a generic .svg placeholder from
    AbeBooks — an SVG, which Pillow can't decode at all (not just badly), so
    passing it through just means every downstream image-similarity check
    fails with 'cannot identify image file'. Treat it as no image instead."""
    if not img_el:
        return None
    src = img_el.get("src")
    if not src or _NO_IMAGE_MARKER in src:
        return None
    return src


def search(query, author=None, limit=20, delay=1.0):
    params = {"kn": query, "sortby": "17"}
    if author:
        params["an"] = author

    resp = requests.get(_SEARCH_URL, headers=_HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    time.sleep(delay)  # be polite between calls if you're looping over queries

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    items = soup.select('[data-test-id^="listing-item-"]')[:limit]
    for i, item in enumerate(items):
        title_el = item.select_one('[data-test-id="listing-title"]')
        author_el = item.select_one('[data-test-id="listing-author"]')
        link_el = item.select_one('a[href]')
        img_el = item.select_one("img")
        price_el = soup.select_one(f'[data-test-id="item-price-{i}"]')
        publisher_el = soup.select_one(f'[data-test-id="publisher-{i}"]')

        price = price_el.get_text(strip=True).replace("\xa0", " ") if price_el else None

        publisher = None
        publication_date = None
        if publisher_el:
            m = _PUBLISHED_BY_RE.match(publisher_el.get_text(strip=True))
            if m:
                publisher, publication_date = m.group(1), m.group(2)

        results.append(
            {
                "source": "abebooks",
                "title": title_el.get_text(strip=True) if title_el else None,
                "author": author_el.get_text(strip=True) if author_el else None,
                "publisher": publisher,
                "publication_date": publication_date,
                "price": price,
                "url": urljoin(_SEARCH_URL, link_el["href"]) if link_el else None,
                "image_url": _real_image_url(img_el),
            }
        )

    return results
