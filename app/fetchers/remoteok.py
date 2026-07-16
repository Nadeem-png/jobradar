"""RemoteOK fetcher.

GET https://remoteok.com/api -> JSON array. The FIRST array element is legal
metadata (not a job) and must be skipped.
"""
from __future__ import annotations

from .http import get
from .util import clean_tags, html_to_text, parse_dt

SOURCE = "remoteok"
API_URL = "https://remoteok.com/api"


def fetch() -> list[dict]:
    resp = get(API_URL)
    data = resp.json()
    if not isinstance(data, list):
        return []

    jobs: list[dict] = []
    # SKIP the first element - it is legal metadata, not a job.
    for item in data[1:]:
        if not isinstance(item, dict):
            continue

        url = item.get("url") or item.get("apply_url") or ""
        if not url:
            continue

        title = (item.get("position") or item.get("title") or "").strip()
        if not title:
            continue

        jobs.append(
            {
                "source": SOURCE,
                "external_id": str(item.get("id") or item.get("slug") or ""),
                "url": url,
                "title": title,
                "company": (item.get("company") or "").strip() or None,
                "description": html_to_text(item.get("description")),
                "tags": clean_tags(item.get("tags")),
                "budget": _salary(item),
                "location": (item.get("location") or "").strip() or None,
                "posted_at": parse_dt(item.get("date") or item.get("epoch")),
            }
        )
    return jobs


def _salary(item: dict) -> str | None:
    lo = item.get("salary_min")
    hi = item.get("salary_max")
    if lo and hi:
        return f"${lo:,} - ${hi:,}"
    if lo:
        return f"${lo:,}+"
    return None
