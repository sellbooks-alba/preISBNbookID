"""Amazon search via Keepa's API.

Two-call flow, since Keepa splits "find ASINs" from "get product details":
  1. /search  — keyword search, returns full `products` (asin/title/author/
     binding/publicationDate) but no images or pricing stats
  2. /product — batch lookup for images/price on those ASINs (needs stats=1)

Keepa schema notes, confirmed against a live account (the docs led me wrong
on a couple of these — worth re-checking if Keepa changes their API again):
prices come back in cents with -1 meaning "no data"; images come back as a
list of dicts (`images: [{"l": "<filename>.jpg", "m": "<filename>.jpg", ...}]`),
not a CSV string; `publisher` is frequently null for books — `brand` (e.g.
"SCRIBNER") is the more reliably populated field for that. `stats.current[3]`
is the current Amazon sales rank in the product's root category (confirmed:
for a book, it exactly matches the latest value in
`salesRanks[str(rootCategory)]`'s history — `salesRanks` itself is keyed by
category id and holds a full [timestamp, rank, timestamp, rank, ...] history,
not a single current value, so `stats.current[3]` is the much simpler read),
same -1-means-no-data convention as price.

Defaults to Amazon UK (Keepa domain id 2) — `_DOMAINS` maps each Keepa domain
id to its Amazon TLD and listing currency, used for both the product URL and
the price's currency label.
"""

import requests

from . import config
from .http_utils import raise_for_status_safe

_BASE_URL = "https://api.keepa.com"
_IMAGE_BASE = "https://m.media-amazon.com/images/I/"

# Keepa domain id -> (Amazon TLD, listing currency). Default is 2 (Amazon UK).
_DOMAINS = {
    1: ("com", "USD"),
    2: ("co.uk", "GBP"),
    3: ("de", "EUR"),
    4: ("fr", "EUR"),
    6: ("ca", "CAD"),
    7: ("it", "EUR"),
    8: ("es", "EUR"),
}
_DEFAULT_DOMAIN = 2


def _asin_url(asin, domain=_DEFAULT_DOMAIN):
    tld = _DOMAINS.get(domain, _DOMAINS[_DEFAULT_DOMAIN])[0]
    return f"https://www.amazon.{tld}/dp/{asin}"


def _format_keepa_date(raw):
    """Keepa's publicationDate is a raw YYYYMMDD int (e.g. 20060101), and 0
    means "no data" (confirmed against live responses — some real ASINs come
    back with exactly 0 here). Only the year is wanted for comparing against
    the edition year you confirm, so pull just that out — rather than an
    unreadable 8-digit number or a full date that's more precision than
    anything else in this app tracks."""
    if not raw:
        return None
    s = str(raw)
    if len(s) == 8 and s.isdigit():
        return s[0:4]
    return s


def search_asins(term, domain=_DEFAULT_DOMAIN, limit=15):
    resp = requests.get(
        f"{_BASE_URL}/search",
        params={
            "key": config.KEEPA_API_KEY,
            "domain": domain,
            "type": "product",
            "term": term,
        },
        timeout=20,
    )
    raise_for_status_safe(resp)  # the key is a URL param — don't let it leak via raise_for_status()'s message
    products = resp.json().get("products") or []
    return [p["asin"] for p in products if p.get("asin")][:limit]


def product_lookup(asins, domain=_DEFAULT_DOMAIN):
    if not asins:
        return []

    resp = requests.get(
        f"{_BASE_URL}/product",
        params={
            "key": config.KEEPA_API_KEY,
            "domain": domain,
            "asin": ",".join(asins),
            "stats": 1,
        },
        timeout=20,
    )
    raise_for_status_safe(resp)  # the key is a URL param — don't let it leak via raise_for_status()'s message
    products = resp.json().get("products") or []
    currency = _DOMAINS.get(domain, _DOMAINS[_DEFAULT_DOMAIN])[1]

    results = []
    for p in products:
        images = p.get("images") or []
        first_image = (images[0].get("l") or images[0].get("m")) if images else None

        price_cents = None
        stats = p.get("stats") or {}
        current = stats.get("current") or []
        for idx in (0, 1, 2):  # Amazon price, then New, then Used
            if len(current) > idx and current[idx] not in (None, -1):
                price_cents = current[idx]
                break

        sales_rank = current[3] if len(current) > 3 and current[3] not in (None, -1) else None

        results.append(
            {
                "source": "amazon",
                "asin": p.get("asin"),
                "title": p.get("title"),
                "author": p.get("author"),
                "publisher": p.get("publisher") or p.get("brand"),
                "binding": p.get("binding"),
                "publication_date": _format_keepa_date(p.get("publicationDate")),
                "price": (price_cents / 100) if price_cents is not None else None,
                "currency": currency,
                "sales_rank": sales_rank,
                "url": _asin_url(p.get("asin"), domain=domain),
                "image_url": f"{_IMAGE_BASE}{first_image}" if first_image else None,
            }
        )
    return results


def search(query, domain=_DEFAULT_DOMAIN, limit=15):
    asins = search_asins(query, domain=domain, limit=limit)
    return product_lookup(asins, domain=domain)
