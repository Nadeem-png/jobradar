"""Upwork saved-search RSS fetcher.

The user pastes one or more Upwork saved-search RSS URLs in Settings
(setting key: upwork_rss_urls). Each is parsed with feedparser. If a feed is
inaccessible (403 / login wall / bad URL), it's recorded in the
'upwork_feed_warnings' setting so the Settings page can show a banner instead of
crashing the cycle.
"""
from __future__ import annotations

import logging

import feedparser
import httpx

from .http import get
from .util import html_to_text, parse_dt

log = logging.getLogger("jobradar.fetchers.upwork_rss")

SOURCE = "upwork_rss"


def fetch() -> list[dict]:
    # Read config and record warnings via our own DB session.
    from ..db import SessionLocal
    from ..settings import get_setting, set_setting

    db = SessionLocal()
    try:
        urls = get_setting(db, "upwork_rss_urls", []) or []
        warnings: dict[str, str] = {}
        jobs: list[dict] = []

        for raw_url in urls:
            url = (raw_url or "").strip()
            if not url:
                continue
            try:
                resp = get(url)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                warnings[url] = (
                    f"HTTP {code}"
                    + (" — login wall / not accessible; re-copy the RSS link from Upwork."
                       if code in (401, 403) else "")
                )
                log.warning("upwork feed %s -> HTTP %s", url, code)
                continue
            except Exception as exc:  # noqa: BLE001 - isolate each feed
                warnings[url] = f"Feed error: {exc}"
                log.warning("upwork feed %s failed: %s", url, exc)
                continue

            feed = feedparser.parse(resp.content)
            if not feed.entries:
                warnings[url] = (
                    "No entries — the feed may require login, or the URL is wrong."
                )
                continue

            for entry in feed.entries:
                link = entry.get("link") or ""
                title = (entry.get("title") or "").strip()
                if not link or not title:
                    continue
                jobs.append(
                    {
                        "source": SOURCE,
                        "external_id": entry.get("id") or link,
                        "url": link,
                        "title": title,
                        "company": None,  # Upwork hides the client name in RSS
                        "description": html_to_text(
                            entry.get("summary") or entry.get("description")
                        ),
                        "tags": [],
                        "budget": None,
                        "location": None,
                        "posted_at": parse_dt(entry.get("published_parsed")),
                    }
                )

        # Persist the current warning state (empty dict clears old warnings).
        set_setting(db, "upwork_feed_warnings", warnings)
        return jobs
    finally:
        db.close()
