"""The Muse fetcher — free public JSON API (no key). Software-engineering roles,
filtered to remote/flexible listings so it fits a remote-jobs feed.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from .http import get
from .util import clean_tags, html_to_text, parse_dt

log = logging.getLogger("jobradar.fetchers.themuse")

SOURCE = "themuse"
# Ask The Muse for remote software-engineering roles directly (location filter),
# so we don't fetch a page of mostly-onsite jobs and then discard them.
API_URL = "https://www.themuse.com/api/public/jobs?" + urlencode(
    {"page": "1", "category": "Software Engineering", "location": "Flexible / Remote"}
)


def _is_remote(locations: list[str]) -> bool:
    joined = " ".join(locations).lower()
    return "remote" in joined or "flexible" in joined


def fetch() -> list[dict]:
    resp = get(API_URL)
    data = resp.json()
    items = data.get("results", []) if isinstance(data, dict) else []

    jobs: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            refs = it.get("refs") or {}
            url = refs.get("landing_page") or "" if isinstance(refs, dict) else ""
            title = (it.get("name") or "").strip()
            if not url or not title:
                continue

            def _names(v):
                return [x.get("name") for x in v if isinstance(x, dict) and x.get("name")] \
                    if isinstance(v, list) else []

            locations = _names(it.get("locations"))
            if locations and not _is_remote(locations):
                continue  # keep the feed remote-focused

            company = it.get("company")
            company_name = (company.get("name") if isinstance(company, dict) else None) or None
            jobs.append(
                {
                    "source": SOURCE,
                    "external_id": str(it.get("id") or ""),
                    "url": url,
                    "title": title,
                    "company": (company_name or "").strip() or None,
                    "description": html_to_text(it.get("contents")),
                    "tags": clean_tags(_names(it.get("levels")) + _names(it.get("categories"))),
                    "budget": None,
                    "location": ", ".join(locations) or None,
                    "posted_at": parse_dt(it.get("publication_date")),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad item must not kill the batch
            log.warning("themuse: skipping malformed item: %s", exc)
    return jobs
