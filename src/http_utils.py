"""Shared HTTP helpers."""


def raise_for_status_safe(resp):
    """Like resp.raise_for_status(), but the message never includes the
    request URL — requests' default HTTPError message is "{status} for url:
    {url}", which leaks API keys passed as query params (e.g. Keepa) straight
    into logs/tracebacks/error banners. Use this instead wherever the key is
    in the URL rather than a header.
    """
    if resp.ok:
        return
    raise RuntimeError(f"HTTP {resp.status_code} {resp.reason}: {resp.text[:300]}")
