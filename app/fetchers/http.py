"""Shared HTTP helpers for fetchers: honest User-Agent + exponential backoff.

Hard rule: be a polite scraper. One request per source per cycle, exponential
backoff on errors, honest User-Agent.
"""
from __future__ import annotations

import os
import time

import httpx

CONTACT = os.getenv("JOBRADAR_CONTACT", "nadeemmkhan441@gmail.com")
USER_AGENT = f"JobRadar personal aggregator (contact: {CONTACT})"

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/html, application/xhtml+xml, */*",
}

# Some boards (Jobgether, Built In) sit behind a WAF that 403s any User-Agent
# that doesn't look like a browser. For those we send a browser UA with the
# JobRadar identity + contact appended, so we stay identifiable and reachable
# rather than pretending to be an anonymous Chrome.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 JobRadar/1.0 (+contact: {CONTACT})"
)
BROWSER_HEADERS = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # Deliberately no "br": httpx only decodes brotli with the [brotli] extra,
    # and asking for it otherwise yields undecodable bytes.
    "Accept-Encoding": "gzip, deflate",
}

TIMEOUT = httpx.Timeout(30.0)


def get(url: str, *, headers: dict | None = None, retries: int = 3) -> httpx.Response:
    """GET with exponential backoff (1s, 2s, 4s). Raises on final failure."""
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = httpx.get(
                url, headers=merged, timeout=TIMEOUT, follow_redirects=True
            )
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            # 4xx (403 blocked, 404, …) won't succeed on retry — fail fast.
            if 400 <= exc.response.status_code < 500:
                raise
            last_exc = exc  # 5xx: retry with backoff
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
        except Exception as exc:  # noqa: BLE001 - network errors: back off and retry
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    assert last_exc is not None
    raise last_exc


def post_json(
    url: str, payload: dict, *, headers: dict | None = None, retries: int = 3
) -> httpx.Response:
    """POST a JSON body with the same backoff policy as :func:`get`.

    Needed by sources whose only public read surface is a JSON/GraphQL POST.
    """
    merged = dict(DEFAULT_HEADERS)
    merged["Content-Type"] = "application/json"
    if headers:
        merged.update(headers)

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            resp = httpx.post(
                url, json=payload, headers=merged, timeout=TIMEOUT, follow_redirects=True
            )
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if 400 <= exc.response.status_code < 500:
                raise
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
        except Exception as exc:  # noqa: BLE001 - network errors: back off and retry
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    assert last_exc is not None
    raise last_exc
