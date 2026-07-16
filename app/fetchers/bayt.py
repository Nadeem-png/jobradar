"""Bayt.com fetcher (Pakistan / Middle East) — best-effort HTML parse.

Bayt sits behind a Cloudflare JS challenge for non-browser clients, so this
usually returns nothing without a browser context; it's kept because users ask
for it and it degrades gracefully (a block is isolated by the pipeline). If Bayt
serves real HTML, job links look like /en/job/... .
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from .http import get

log = logging.getLogger("jobradar.fetchers.bayt")

SOURCE = "bayt"
BASE = "https://www.bayt.com"
LIST_URL = f"{BASE}/en/pakistan/jobs/"

# Job detail URLs carry a long numeric id in the slug. The wildcard excludes '?'
# so pagination/facet links (/en/pakistan/jobs/?page=2) never match, and we
# require >=4 digits so short category ids don't either.
_JOB_HREF = re.compile(
    r"^(?:https?://www\.bayt\.com)?/en/[a-z/-]*jobs?/[^?\s]*\d{4,}[^?\s]*$", re.I
)


def fetch() -> list[dict]:
    resp = get(LIST_URL)
    soup = BeautifulSoup(resp.text, "html.parser")

    jobs: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        try:
            href = a["href"]
            if not _JOB_HREF.match(href):
                continue
            url = href if href.startswith("http") else BASE + href
            url = url.split("?")[0]
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 4 or url in seen:
                continue
            seen.add(url)

            company = None
            card = a.find_parent(["li", "div", "article"])
            if card is not None:
                # Prefer an explicit company-class element; fall back to a <b>
                # that is NOT inside the title anchor itself.
                el = card.find(class_=re.compile(r"company", re.I))
                if el is None:
                    el = next((b for b in card.find_all("b") if a not in b.parents), None)
                if el is not None:
                    text = el.get_text(" ", strip=True)[:200]
                    if text and text != title:
                        company = text

            jobs.append(
                {
                    "source": SOURCE,
                    "external_id": url,
                    "url": url,
                    "title": title[:300],
                    "company": company,
                    "description": title,
                    "tags": [],
                    "budget": None,
                    "location": "Pakistan",
                    "posted_at": None,
                }
            )
            if len(jobs) >= 50:
                break
        except Exception as exc:  # noqa: BLE001
            log.warning("bayt: skipping malformed card: %s", exc)
    if not jobs:
        log.info("bayt: no job links parsed (Cloudflare challenge or markup change)")
    return jobs
