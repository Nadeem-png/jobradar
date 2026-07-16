"""Jobicy remote-jobs fetcher — free public JSON API (no key)."""
from __future__ import annotations

import logging

from .http import get
from .util import clean_tags, html_to_text, parse_dt

log = logging.getLogger("jobradar.fetchers.jobicy")

SOURCE = "jobicy"
API_URL = "https://jobicy.com/api/v2/remote-jobs?count=50"


def _list(v) -> list:
    return v if isinstance(v, list) else ([v] if v else [])


def fetch() -> list[dict]:
    resp = get(API_URL)
    data = resp.json()
    items = data.get("jobs", []) if isinstance(data, dict) else []

    jobs: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            url = it.get("url") or ""
            title = (it.get("jobTitle") or "").strip()
            if not url or not title:
                continue

            salary = None
            lo, hi = it.get("annualSalaryMin"), it.get("annualSalaryMax")
            cur = (it.get("salaryCurrency") or "").strip()
            if lo and hi:
                salary = f"{cur} {lo}-{hi}/yr".strip()
            elif lo or hi:
                salary = f"{cur} {lo or hi}/yr".strip()

            tags = clean_tags(_list(it.get("jobIndustry")) + _list(it.get("jobType")))
            jobs.append(
                {
                    "source": SOURCE,
                    "external_id": str(it.get("id") or ""),
                    "url": url,
                    "title": title,
                    "company": (it.get("companyName") or "").strip() or None,
                    "description": html_to_text(it.get("jobDescription") or it.get("jobExcerpt")),
                    "tags": tags,
                    "budget": salary,
                    "location": (it.get("jobGeo") or "").strip() or None,
                    "posted_at": parse_dt(it.get("pubDate")),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad item must not kill the batch
            log.warning("jobicy: skipping malformed item: %s", exc)
    return jobs
