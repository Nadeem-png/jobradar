"""Indeed fetcher (best-effort).

NOTE: Indeed removed its public RSS/API years ago and blocks scraping, so this
often returns nothing / a 403. It's kept because users ask for it and it degrades
gracefully (like Reddit): a block is logged and skipped, never fatal. If you have
an Indeed Publisher API key, this is the place to wire it in.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

import feedparser

from .http import get
from .util import html_to_text, parse_dt

log = logging.getLogger("jobradar.fetchers.indeed")

SOURCE = "indeed"
# Legacy RSS-style endpoint; may 403 / return no items.
FEED_URL = "https://www.indeed.com/rss?" + urlencode({"q": "developer", "l": "remote"})


def fetch() -> list[dict]:
    resp = get(FEED_URL)
    feed = feedparser.parse(resp.content)
    if not feed.entries:
        log.info("indeed: no entries (public RSS is likely unavailable)")
        return []

    jobs: list[dict] = []
    for entry in feed.entries:
        url = entry.get("link") or ""
        raw_title = (entry.get("title") or "").strip()
        if not url or not raw_title:
            continue
        # Indeed titles look like "Job Title - Company - Location". Company and
        # location are the site-appended RIGHTMOST parts, so split from the right —
        # a title like "Full Stack Developer - React/Node" keeps its hyphen.
        parts = [p.strip() for p in raw_title.rsplit(" - ", 2)]
        title = parts[0]
        company = parts[1] if len(parts) >= 2 else None
        jobs.append(
            {
                "source": SOURCE,
                "external_id": entry.get("id") or url,
                "url": url,
                "title": title,
                "company": company,
                "description": html_to_text(entry.get("summary")),
                "tags": [],
                "budget": None,
                "location": (parts[2] if len(parts) >= 3 else None),
                "posted_at": parse_dt(entry.get("published_parsed")),
            }
        )
    return jobs
