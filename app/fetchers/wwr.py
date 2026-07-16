"""We Work Remotely fetcher.

feedparser on the remote-programming-jobs RSS feed. Entry titles are usually
"Company: Position".
"""
from __future__ import annotations

import feedparser

from .http import get
from .util import clean_tags, html_to_text, parse_dt

SOURCE = "wwr"
FEED_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"


def fetch() -> list[dict]:
    # Fetch via our polite HTTP helper (honest UA + backoff), then parse bytes.
    resp = get(FEED_URL)
    feed = feedparser.parse(resp.content)

    jobs: list[dict] = []
    for entry in feed.entries:
        url = entry.get("link") or ""
        raw_title = (entry.get("title") or "").strip()
        if not url or not raw_title:
            continue

        company, title = _split_title(raw_title)

        # WWR puts categories/regions in tags.
        tags = clean_tags([t.get("term") for t in entry.get("tags", [])])

        jobs.append(
            {
                "source": SOURCE,
                "external_id": entry.get("id") or url,
                "url": url,
                "title": title,
                "company": company,
                "description": html_to_text(
                    entry.get("summary") or entry.get("description")
                ),
                "tags": tags,
                "budget": None,
                "location": None,
                "posted_at": parse_dt(entry.get("published_parsed")),
            }
        )
    return jobs


def _split_title(raw: str) -> tuple[str | None, str]:
    """"Company: Position" -> ("Company", "Position"). Falls back gracefully."""
    if ":" in raw:
        company, _, position = raw.partition(":")
        company = company.strip()
        position = position.strip()
        if company and position:
            return company, position
    return None, raw
