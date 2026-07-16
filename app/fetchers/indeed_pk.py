"""Indeed Pakistan (pk.indeed.com) fetcher — best-effort RSS attempt.

Same situation as indeed.com: the public feed is blocked (403) without a paid
Publisher API key. Kept because users ask for it; degrades gracefully (the
pipeline isolates the failure) and is off by default.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

import feedparser

from .http import get
from .util import html_to_text, parse_dt

log = logging.getLogger("jobradar.fetchers.indeed_pk")

SOURCE = "indeed_pk"
FEED_URL = "https://pk.indeed.com/rss?" + urlencode({"q": "developer"})


def fetch() -> list[dict]:
    resp = get(FEED_URL)
    feed = feedparser.parse(resp.content)
    if not feed.entries:
        log.info("indeed_pk: no entries (public RSS is likely unavailable)")
        return []

    jobs: list[dict] = []
    for entry in feed.entries:
        try:
            url = entry.get("link") or ""
            raw_title = (entry.get("title") or "").strip()
            if not url or not raw_title:
                continue
            # Company/location are the RIGHTMOST site-appended parts — split from
            # the right so hyphenated titles survive intact.
            parts = [p.strip() for p in raw_title.rsplit(" - ", 2)]
            jobs.append(
                {
                    "source": SOURCE,
                    "external_id": entry.get("id") or url,
                    "url": url,
                    "title": parts[0],
                    "company": parts[1] if len(parts) >= 2 else None,
                    "description": html_to_text(entry.get("summary")),
                    "tags": [],
                    "budget": None,
                    "location": (parts[2] if len(parts) >= 3 else "Pakistan"),
                    "posted_at": parse_dt(entry.get("published_parsed")),
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("indeed_pk: skipping malformed entry: %s", exc)
    return jobs
