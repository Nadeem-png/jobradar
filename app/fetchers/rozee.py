"""Rozee.pk fetcher (Pakistan) — best-effort HTML parse of the search page.

Rozee blocks datacenter IPs (403) but generally serves residential ones. The
parser is deliberately tolerant: it collects anchors that look like Rozee job
links and reads title/context around them, so minor markup changes degrade to
"fewer fields", not crashes. A block is raised and isolated by the pipeline.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from .http import get

log = logging.getLogger("jobradar.fetchers.rozee")

SOURCE = "rozee"
BASE = "https://www.rozee.pk"
SEARCH_URL = f"{BASE}/job/jsearch/q/developer"

# Rozee job-detail URLs end in "-jobs-<id>" with a long numeric id. Requiring
# >=4 digits skips short-id category/navigation links; the old "/job/" prefix
# alternative is gone because it matched search-pagination links too.
_JOB_HREF = re.compile(r"rozee\.pk/.*-jobs?-\d{4,}", re.I)
_REL_JOB_HREF = re.compile(r"^/.*-jobs?-\d{4,}(?:\?|$)", re.I)

# Lines that are clearly not a company name (salary, dates, actions).
_NOT_COMPANY = re.compile(
    r"(?i)(?:pkr|rs\.?\s?\d|\$\s?\d|salary|posted|ago\b|apply|save\b|views?\b|^\d)"
)


def _find_after_title(lines: list[str], title: str) -> list[str]:
    """Lines following the title inside the card.

    The card's text is split per text-node, so a title anchor with nested markup
    ("Senior <b>Laravel</b> Developer") spans SEVERAL lines. Find the contiguous
    run of lines whose space-joined text equals the title, and return what
    follows it. Returns [] when the title can't be located (never guess).
    """
    for i in range(len(lines)):
        acc = ""
        for j in range(i, min(i + 8, len(lines))):
            acc = f"{acc} {lines[j]}".strip()
            if acc == title:
                return lines[j + 1 : j + 4]
            if len(acc) > len(title):
                break
    return []


def fetch() -> list[dict]:
    resp = get(SEARCH_URL)
    soup = BeautifulSoup(resp.text, "html.parser")

    jobs: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        try:
            href = a["href"]
            if not (_JOB_HREF.search(href) or _REL_JOB_HREF.match(href)):
                continue
            url = href if href.startswith("http") else BASE + href
            url = url.split("?")[0]
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 4 or url in seen:
                continue
            seen.add(url)

            # Company / location: best-effort from the enclosing card's text lines.
            company = location = None
            card = a.find_parent(["div", "li", "article"])
            if card is not None:
                lines = [ln.strip() for ln in card.get_text("\n", strip=True).split("\n") if ln.strip()]
                after = _find_after_title(lines, title)
                if after and not _NOT_COMPANY.search(after[0]):
                    company = after[0][:200] or None
                # Scan the whole card for a city/country line (works even when
                # the title run wasn't located).
                for ln in after or lines:
                    if "pakistan" in ln.lower() or re.search(
                        r"\b(karachi|lahore|islamabad|rawalpindi|faisalabad|multan|peshawar)\b", ln, re.I
                    ):
                        location = ln[:200]
                        break

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
                    "location": location or "Pakistan",
                    "posted_at": None,
                }
            )
            if len(jobs) >= 50:
                break
        except Exception as exc:  # noqa: BLE001
            log.warning("rozee: skipping malformed card: %s", exc)
    if not jobs:
        log.info("rozee: no job links parsed (blocked or markup changed)")
    return jobs
