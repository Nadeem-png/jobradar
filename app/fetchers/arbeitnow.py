"""Arbeitnow job-board fetcher — free public JSON API (no key)."""
from __future__ import annotations

import logging

from .http import get
from .util import clean_tags, html_to_text, parse_dt

log = logging.getLogger("jobradar.fetchers.arbeitnow")

SOURCE = "arbeitnow"
API_URL = "https://www.arbeitnow.com/api/job-board-api"


def _list(v) -> list:
    return v if isinstance(v, list) else ([v] if v else [])


def fetch() -> list[dict]:
    resp = get(API_URL)
    data = resp.json()
    items = data.get("data", []) if isinstance(data, dict) else []

    jobs: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            # Arbeitnow is a general (mostly on-site, mostly German) board — keep
            # only remote listings so the feed stays remote-focused.
            if not it.get("remote"):
                continue
            url = it.get("url") or ""
            title = (it.get("title") or "").strip()
            if not url or not title:
                continue

            tags = clean_tags(_list(it.get("tags")) + _list(it.get("job_types")) + ["remote"])
            jobs.append(
                {
                    "source": SOURCE,
                    "external_id": it.get("slug") or url,
                    "url": url,
                    "title": title,
                    "company": (it.get("company_name") or "").strip() or None,
                    "description": html_to_text(it.get("description")),
                    "tags": tags,
                    "budget": None,
                    "location": (it.get("location") or "").strip() or None,
                    "posted_at": parse_dt(it.get("created_at")),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad item must not kill the batch
            log.warning("arbeitnow: skipping malformed item: %s", exc)
    return jobs
