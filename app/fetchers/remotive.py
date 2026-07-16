"""Remotive fetcher.

GET https://remotive.com/api/remote-jobs?category=software-dev -> {"jobs": [...]}.
"""
from __future__ import annotations

from .http import get
from .util import clean_tags, html_to_text, parse_dt

SOURCE = "remotive"
API_URL = "https://remotive.com/api/remote-jobs?category=software-dev"


def fetch() -> list[dict]:
    resp = get(API_URL)
    data = resp.json()
    items = data.get("jobs", []) if isinstance(data, dict) else []

    jobs: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        url = item.get("url") or ""
        title = (item.get("title") or "").strip()
        if not url or not title:
            continue

        jobs.append(
            {
                "source": SOURCE,
                "external_id": str(item.get("id") or ""),
                "url": url,
                "title": title,
                "company": (item.get("company_name") or "").strip() or None,
                "description": html_to_text(item.get("description")),
                "tags": clean_tags(item.get("tags")),
                "budget": (item.get("salary") or "").strip() or None,
                "location": (item.get("candidate_required_location") or "").strip()
                or None,
                "posted_at": parse_dt(item.get("publication_date")),
            }
        )
    return jobs
