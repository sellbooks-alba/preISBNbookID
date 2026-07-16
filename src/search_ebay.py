"""eBay Browse API search (official, requires a free developer account)."""

import requests

from . import config

_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

_token_cache = {"value": None}


def get_app_token():
    if _token_cache["value"]:
        return _token_cache["value"]

    resp = requests.post(
        _TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
        auth=(config.EBAY_CLIENT_ID, config.EBAY_CLIENT_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    _token_cache["value"] = token
    return token


def search(query, limit=20, category_ids="267"):
    """category_ids 267 = Books; drop it to search all of eBay."""
    token = get_app_token()
    resp = requests.get(
        _SEARCH_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        },
        params={"q": query, "limit": limit, "category_ids": category_ids},
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("itemSummaries", [])

    return [
        {
            "source": "ebay",
            "title": item.get("title"),
            "price": (item.get("price") or {}).get("value"),
            "currency": (item.get("price") or {}).get("currency"),
            "condition": item.get("condition"),
            "url": item.get("itemWebUrl"),
            "image_url": (item.get("image") or {}).get("imageUrl"),
        }
        for item in items
    ]
