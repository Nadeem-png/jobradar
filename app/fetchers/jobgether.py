"""Jobgether fetcher — the server-rendered remote-job search page.

Jobgether has no public API and no RSS, but /search-offers renders its offer
cards server-side, so one request yields ~50 jobs. Their WAF 403s any
non-browser User-Agent, so this uses the shared BROWSER_HEADERS (a browser UA
with JobRadar's identity + contact appended).

Card anatomy (verified live): the title is an ``a[href^="/offer/"]`` carrying a
``title`` attribute, the company is an ``a[href^="/remote-jobs/company-"]`` in
the same card, freshness is a sibling "Today"/"3 days ago" label, and skills are
``div.line-clamp-1`` chips. Cards carry no description text, so the description
is assembled from company/location/type/salary/skills — enough for the
pipeline's keyword gate to judge relevance.
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .http import BROWSER_HEADERS, get
from .util import clean_tags, parse_relative_dt

log = logging.getLogger("jobradar.fetchers.jobgether")

SOURCE = "jobgether"
BASE = "https://jobgether.com"
SEARCH_URL = f"{BASE}/search-offers"

_OFFER_HREF = re.compile(r"^/offer/")
_COMPANY_HREF = re.compile(r"^/remote-jobs/company-")
# "57 - 72 K", "$120k", "€60,000" — the card's compensation chip.
_SALARY_RE = re.compile(
    r"(?:[$€£]\s?[\d,.]+|\d[\d,.]*)\s*(?:[-–—]\s*(?:[$€£]\s?)?[\d,.]+\s*)?[kK]\b"
    r"|[$€£]\s?[\d,.]{3,}"
)
_JOB_TYPES = {
    "full-time", "full time", "part-time", "part time", "contract",
    "internship", "freelance", "temporary",
}
# The card links skills as /remote-jobs/<skill>; these paths are not skills.
_NON_TAG_SLUGS = {"full-time", "part-time", "contract", "internship", "freelance"}


def fetch() -> list[dict]:
    keyword = os.getenv("JOBGETHER_KEYWORD", "developer").strip()
    max_jobs = _env_int("JOBGETHER_MAX_JOBS", 60, lo=1, hi=200)

    params = {}
    if keyword:
        params["keyword"] = keyword
    url = SEARCH_URL + ("?" + urlencode(params) if params else "")

    resp = get(url, headers=BROWSER_HEADERS)
    return _parse_jobs(resp.text, max_jobs)


def _parse_jobs(html: str, max_jobs: int = 60) -> list[dict]:
    """Parse the search page's offer cards. Pure so it's unit-testable."""
    soup = BeautifulSoup(html or "", "html.parser")
    anchors = [
        a for a in soup.find_all("a", href=_OFFER_HREF)
        if (a.get("title") or a.get_text(strip=True))
    ]

    jobs: list[dict] = []
    seen: set[str] = set()
    for anchor in anchors:
        if len(jobs) >= max_jobs:
            break
        try:
            href = (anchor.get("href") or "").split("?")[0]
            if not href or href in seen:
                continue
            title = (anchor.get("title") or anchor.get_text(" ", strip=True)).strip()
            if not title:
                continue
            seen.add(href)

            card = _card_root(anchor)
            company_el = card.find("a", href=_COMPANY_HREF) if card else None
            company = company_el.get_text(" ", strip=True) if company_el else None

            card_text = card.get_text(" ", strip=True) if card else ""
            location = _location(card)
            job_type = _job_type(card_text)
            salary = _salary(card_text)
            tags = clean_tags(_tags(card) + ([job_type] if job_type else []))

            description = " · ".join(
                filter(None, [title, company, location, job_type, salary, ", ".join(tags)])
            )

            jobs.append(
                {
                    "source": SOURCE,
                    "external_id": _offer_id(href),
                    "url": BASE + href,
                    "title": title,
                    "company": company,
                    "description": description,
                    "tags": tags[:8],
                    "budget": salary,
                    "location": location,
                    "posted_at": parse_relative_dt(_age_label(anchor)),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad card must not kill the batch
            log.warning("jobgether: skipping malformed card: %s", exc)

    if not jobs:
        log.info("jobgether: 0 cards parsed (markup change or WAF block?)")
    return jobs


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        return default
    return max(lo, min(hi, value))


def _card_root(anchor):
    """Widest ancestor that still describes only this offer.

    The company link sits a couple of levels above the title, but location,
    salary and skill chips live higher still, so stopping at the first ancestor
    containing the company would miss them. Instead take the largest ancestor
    that holds exactly one offer link — that is the card, and it stops short of
    the list container holding sibling cards.
    """
    node = anchor
    best = anchor.parent or anchor
    for _ in range(10):
        parent = node.parent
        if parent is None:
            break
        if len(parent.find_all("a", href=_OFFER_HREF)) != 1:
            break
        node = best = parent
    return best


def _offer_id(href: str) -> str:
    """/offer/<24-hex id>-<slug> -> the id (falls back to the whole slug)."""
    slug = href.rsplit("/", 1)[-1]
    return slug.split("-", 1)[0] or slug


def _age_label(anchor) -> str | None:
    """The "Today" / "3 days ago" chip sits next to the title link."""
    sibling = anchor.find_next_sibling("div")
    if sibling is not None:
        text = sibling.get_text(" ", strip=True)
        if text and len(text) <= 30:
            return text
    return None


def _location(card) -> str | None:
    """Cards render location as "Remote from <place>"."""
    if card is None:
        return None
    text = card.get_text(" ", strip=True)
    m = re.search(r"Remote from\s+(.{2,80}?)(?:\s{2,}|$|Full time|Part time|Contract)", text)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip(" ·,")
    return "Remote" if "Remote" in text else None


def _job_type(card_text: str) -> str | None:
    lowered = (card_text or "").lower()
    for label in ("full time", "part time", "contract", "internship", "freelance"):
        if label in lowered:
            return label.title()
    return None


def _salary(card_text: str) -> str | None:
    m = _SALARY_RE.search(card_text or "")
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else None


def _tags(card) -> list[str]:
    """Skill chips: /remote-jobs/<skill> links rendered as line-clamp-1 divs."""
    if card is None:
        return []
    out: list[str] = []
    for a in card.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("/remote-jobs/") or _COMPANY_HREF.match(href):
            continue
        slug = href.rsplit("/", 1)[-1]
        if slug in _NON_TAG_SLUGS:
            continue
        chip = a.find("div", class_="line-clamp-1")
        if chip is None:
            continue
        text = chip.get_text(" ", strip=True)
        if text and len(text) <= 40 and text.lower() not in _JOB_TYPES:
            out.append(text)
    return out
