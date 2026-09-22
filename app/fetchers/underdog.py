"""Underdog.io fetcher — the public JSON API behind their startup job board.

underdog.io itself is a Webflow marketing site; the board at /startup-job-board
is rendered client-side from https://jobs-api.underdog.io/api/jobs/search, which
needs no key and answers the honest User-Agent. The API caps perPage at 20, so a
small bounded page walk (UNDERDOG_MAX_PAGES) covers the board's ~85 listings.

Underdog anonymises employers ("our hiring partner"), so company is always None —
the description carries the company profile instead.
"""
from __future__ import annotations

import html
import logging
import os
import re

from .http import get
from .util import clean_tags, html_to_text, parse_dt

log = logging.getLogger("jobradar.fetchers.underdog")

SOURCE = "underdog"
API_URL = "https://jobs-api.underdog.io/api/jobs/search"
JOB_URL = "https://underdog.io/jobs/{slug}"


def fetch() -> list[dict]:
    max_pages = _env_int("UNDERDOG_MAX_PAGES", 3, lo=1, hi=10)

    jobs: list[dict] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        resp = get(f"{API_URL}?page={page}&perPage=20")
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("underdog: page %d was not JSON (%s)", page, exc)
            break
        if payload.get("error"):
            log.warning("underdog: API error: %s", payload.get("msg"))
            break

        batch = payload.get("objJobs") or []
        for raw in batch:
            try:
                job = _normalize(raw)
            except Exception as exc:  # noqa: BLE001 - one bad row can't kill the batch
                log.warning("underdog: skipping malformed job: %s", exc)
                continue
            if job and job["url"] not in seen:
                seen.add(job["url"])
                jobs.append(job)

        pagination = payload.get("pagination") or {}
        total_pages = pagination.get("pages") or 0
        if not batch or page >= total_pages:
            break

    if not jobs:
        log.info("underdog: 0 jobs parsed (API shape change?)")
    return jobs


def _normalize(raw: dict) -> dict | None:
    if raw.get("is_expired"):
        return None

    title = _plain(raw.get("title"))
    slug = (raw.get("webflow_slug") or raw.get("slug") or "").strip()
    if not title or not slug:
        return None

    cities = [
        c for c in (
            _city(city) for city in (raw.get("objCities") or [])
        ) if c
    ]
    categories = clean_tags(
        (c or {}).get("name") for c in (raw.get("objCategories") or [])
    )

    return {
        "source": SOURCE,
        "external_id": str(raw.get("id") or slug),
        "url": JOB_URL.format(slug=slug),
        "title": title,
        # Underdog lists roles on behalf of unnamed hiring partners.
        "company": None,
        "description": html_to_text(raw.get("description")),
        "tags": categories,
        "budget": _salary(raw.get("min_salary"), raw.get("max_salary")),
        "location": ", ".join(cities) or None,
        "posted_at": parse_dt(raw.get("created_at")),
    }


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        return default
    return max(lo, min(hi, value))


def _plain(text) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()


def _city(city) -> str | None:
    if not isinstance(city, dict):
        return None
    name = (city.get("name") or "").strip()
    if not name:
        return None
    state = (city.get("state_abbreviation") or "").strip()
    # "Remote" already reads as a location; a state code would be noise.
    if state and state.upper() != "REMOTE" and state.lower() != name.lower():
        return f"{name}, {state}"
    return name


def _salary(low, high) -> str | None:
    """min/max_salary are plain integers, e.g. 180000 -> "$180k – $280k"."""
    parts = [_money(v) for v in (low, high)]
    low_s, high_s = parts[0], parts[1]
    if low_s and high_s and low_s != high_s:
        return f"{low_s} – {high_s}"
    return low_s or high_s


def _money(value) -> str | None:
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}k"
    return f"${amount}"
