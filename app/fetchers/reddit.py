"""Reddit fetcher for r/forhire and r/remotejs.

Reads the public /new.json feeds with our honest User-Agent and keeps only posts
whose title contains "[Hiring]". Each subreddit is fetched independently so one
failing feed doesn't drop the other.
"""
from __future__ import annotations

import logging

from .http import get
from .util import clean_tags, html_to_text, parse_dt

log = logging.getLogger("jobradar.fetchers.reddit")

SOURCE = "reddit"
SUBREDDITS = ["forhire", "remotejs"]


def fetch() -> list[dict]:
    jobs: list[dict] = []
    for sub in SUBREDDITS:
        url = f"https://www.reddit.com/r/{sub}/new.json?limit=50"
        try:
            resp = get(url)
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 - isolate each subreddit
            log.warning("reddit r/%s failed: %s", sub, exc)
            continue

        for child in data.get("data", {}).get("children", []):
            d = child.get("data", {}) or {}
            title = (d.get("title") or "").strip()
            if "[hiring]" not in title.lower():
                continue

            permalink = d.get("permalink") or ""
            url_full = (
                f"https://www.reddit.com{permalink}" if permalink else d.get("url") or ""
            )
            if not url_full:
                continue

            author = d.get("author")
            tags = clean_tags([sub, d.get("link_flair_text")])

            jobs.append(
                {
                    "source": SOURCE,
                    "external_id": d.get("id") or url_full,
                    "url": url_full,
                    "title": title,
                    "company": (f"u/{author}" if author else None),
                    "description": html_to_text(d.get("selftext_html"))
                    or (d.get("selftext") or "").strip()
                    or title,
                    "tags": tags,
                    "budget": None,
                    "location": None,
                    "posted_at": parse_dt(d.get("created_utc")),
                }
            )
    return jobs
