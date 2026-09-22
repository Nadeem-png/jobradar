"""Built In fetcher — their public GraphQL API.

Off by default, and here is the honest state of play (all verified live):
  * builtin.com/jobs* is hard-blocked by Cloudflare ("Attention Required", a WAF
    rule) for plain HTTP *and* for real Chrome, headless or headed. HTML scraping
    is not an option, and getting round a deliberate block is not something this
    project does.
  * api.builtin.com/graphql is public, unauthenticated, and has introspection on.
    Its ``customFilteredJobs`` query is the job-list surface and returns proper
    Job rows (id/title/location/url/company/remoteStatus/bodySummary).
  * That resolver currently has a server-side bug: pages whose rows include a
    company with NULL ats_details fail with "Scan error on column index 2, name
    ats_details". Roughly three quarters of pages hit it, so a cycle often
    returns nothing. Their WAF blocks the alternative, so this is the only
    surface available.

So: this fetcher asks politely (one small POST per attempt, a couple of attempts
per cycle), keeps whatever clean pages it gets, and logs a single line when the
upstream bug swallows a page. It starts working properly the moment Built In
fixes the resolver — no code change needed.

Config (env / .env):
  BUILTIN_MAX_JOBS      default 40
  BUILTIN_PAGE_SIZE     default 20
  BUILTIN_MAX_ATTEMPTS  page requests per cycle, default 3
"""
from __future__ import annotations

import html
import logging
import os
import re

from .http import BROWSER_HEADERS, post_json
from .util import clean_tags, html_to_text

log = logging.getLogger("jobradar.fetchers.builtin")

SOURCE = "builtin"
BASE = "https://builtin.com"
GRAPHQL_URL = "https://api.builtin.com/graphql"

# "PaginatonInput" is Built In's own spelling in the published schema.
_JOBS_QUERY = """
query JobRadarJobs($pagination: PaginatonInput, $filters: JobsFiltersInput) {
  customFilteredJobs(pagination: $pagination, filters: $filters) {
    jobCount
    customFilteredJobs {
      id
      title
      url
      location
      originalLocation
      remoteStatus
      experienceLevel
      isHybrid
      bodySummary
      company { name }
    }
  }
}
""".strip()

# The known upstream defect, so it's logged as one line instead of a traceback.
_UPSTREAM_BUG = "ats_details"

_HEADERS = {
    **BROWSER_HEADERS,
    "Accept": "application/json, */*",
    "Origin": BASE,
    "Referer": BASE + "/",
}


def fetch() -> list[dict]:
    max_jobs = _env_int("BUILTIN_MAX_JOBS", 40, lo=1, hi=200)
    page_size = _env_int("BUILTIN_PAGE_SIZE", 20, lo=1, hi=50)
    attempts = _env_int("BUILTIN_MAX_ATTEMPTS", 3, lo=1, hi=10)

    jobs: list[dict] = []
    seen: set[str] = set()
    upstream_failures = 0

    for page in range(1, attempts + 1):
        rows, failed = _request_page(page, page_size)
        if failed:
            upstream_failures += 1
            continue
        if not rows:
            break
        for raw in rows:
            try:
                job = _normalize(raw)
            except Exception as exc:  # noqa: BLE001 - one bad row can't kill the batch
                log.warning("builtin: skipping malformed job: %s", exc)
                continue
            if job and job["url"] not in seen:
                seen.add(job["url"])
                jobs.append(job)
        if len(jobs) >= max_jobs:
            break

    if upstream_failures:
        log.info(
            "builtin: %d/%d page(s) unavailable — upstream resolver bug "
            "(ats_details); kept %d job(s)", upstream_failures, attempts, len(jobs),
        )
    elif not jobs:
        log.info("builtin: 0 jobs returned (API shape change?)")
    return jobs[:max_jobs]


def _request_page(page: int, page_size: int) -> tuple[list[dict], bool]:
    """Fetch one page. Returns (rows, hit_upstream_bug)."""
    payload = {
        "query": _JOBS_QUERY,
        "variables": {
            "pagination": {"page": page, "perPage": page_size},
            "filters": {},
        },
    }
    resp = post_json(GRAPHQL_URL, payload, headers=_HEADERS)
    try:
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("builtin: page %d was not JSON (%s)", page, exc)
        return [], False

    errors = body.get("errors") or []
    if errors:
        message = str(errors[0].get("message", ""))
        if _UPSTREAM_BUG in message:
            return [], True
        log.warning("builtin: GraphQL error on page %d: %s", page, message[:160])
        return [], False

    container = ((body.get("data") or {}).get("customFilteredJobs") or {})
    return container.get("customFilteredJobs") or [], False


def _normalize(raw: dict) -> dict | None:
    title = _plain(raw.get("title"))
    path = (raw.get("url") or "").strip()
    if not title or not path:
        return None
    url = path if path.startswith("http") else BASE + path

    company = raw.get("company") or {}
    location = (
        _plain(raw.get("location")) or _plain(raw.get("originalLocation")) or None
    )
    remote = _remote_label(raw.get("remoteStatus"), raw.get("isHybrid"))
    experience = _plain(raw.get("experienceLevel")) or None
    summary = html_to_text(raw.get("bodySummary"))

    description = "\n".join(
        filter(None, [summary, " · ".join(filter(None, [remote, experience]))])
    )

    return {
        "source": SOURCE,
        "external_id": str(raw.get("id") or url),
        "url": url,
        "title": title,
        "company": _plain(company.get("name")) or None,
        "description": description or title,
        "tags": clean_tags([remote, experience]),
        "budget": None,  # not exposed by this query
        "location": location,
        "posted_at": None,  # the Job type carries no post date
    }


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        return default
    return max(lo, min(hi, value))


def _plain(text) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()


def _remote_label(status, is_hybrid) -> str | None:
    """"FULLY_REMOTE" -> "Fully Remote"; hybrid wins when the flag is set."""
    if is_hybrid:
        return "Hybrid"
    text = _plain(status)
    if not text or text.upper() == "NOT_REMOTE":
        return None
    return text.replace("_", " ").title()
