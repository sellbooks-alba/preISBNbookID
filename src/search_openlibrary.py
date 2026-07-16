"""Open Library / Internet Archive search.

Free, official, no API key. Two-call flow, confirmed against live responses:
  1. /search.json        — keyword search, but returns *work*-level results
     (one row per book regardless of edition — its `publisher` field is every
     publisher across every edition ever, which is useless for identifying
     a specific printing)
  2. /works/{id}/editions.json — real per-edition data (one publisher, one
     publish_date, one set of cover images) for a given work

Not a marketplace — there's no price or "buy" link, just a reference/
identification source. Useful for cross-checking the edition you're holding
against a real bibliographic record rather than for finding somewhere to
buy it.
"""

import requests

_SEARCH_URL = "https://openlibrary.org/search.json"
_EDITIONS_URL = "https://openlibrary.org/works/{}/editions.json"
_COVER_URL = "https://covers.openlibrary.org/b/id/{}-L.jpg"

_WORKS_TO_CHECK = 5
_EDITIONS_PER_WORK = 3


def _work_url(key):
    return f"https://openlibrary.org{key}"


def search(query, limit=15):
    resp = requests.get(_SEARCH_URL, params={"q": query, "limit": _WORKS_TO_CHECK}, timeout=15)
    resp.raise_for_status()
    works = resp.json().get("docs", [])

    results = []
    for work in works:
        key = work.get("key")
        if not key or len(results) >= limit:
            continue

        authors = work.get("author_name") or []
        author = ", ".join(authors) if authors else None

        try:
            ed_resp = requests.get(_EDITIONS_URL.format(key.split("/")[-1]), params={"limit": _EDITIONS_PER_WORK}, timeout=15)
            ed_resp.raise_for_status()
            editions = ed_resp.json().get("entries", [])
        except Exception:
            editions = []

        for ed in editions:
            if len(results) >= limit:
                break
            publishers = ed.get("publishers") or []
            covers = ed.get("covers") or []
            cover_id = next((c for c in covers if c and c > 0), None)

            results.append(
                {
                    "source": "openlibrary",
                    "title": ed.get("title") or work.get("title"),
                    "author": author,
                    "publisher": publishers[0] if publishers else None,
                    "publication_date": ed.get("publish_date"),
                    "price": None,
                    "url": _work_url(ed.get("key")) if ed.get("key") else _work_url(key),
                    "image_url": _COVER_URL.format(cover_id) if cover_id else None,
                }
            )

    return results
