"""Mustakbil.com fetcher (Pakistan) — parses the public jobs listing page.

Card structure (verified live): <article> with a.jl-title__link (title + href),
span.jl-company__name, p.jl-location, span.jl-salary, .jl-chip spans (type /
shift / years of experience), p.jl-desc snippet. Material-icon ligature text
("place", "payments", …) is stripped before reading text.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from .http import get

log = logging.getLogger("jobradar.fetchers.mustakbil")

SOURCE = "mustakbil"
BASE = "https://www.mustakbil.com"
LIST_URL = f"{BASE}/jobs/pakistan"

_YEARS_CHIP = re.compile(r"^\d{1,2}\+?\s*Years?$", re.I)


def _text(el) -> str:
    """Element text with material-icon ligatures (<i class=icon>) removed."""
    if el is None:
        return ""
    for icon in el.find_all("i", class_="icon"):
        icon.extract()
    return el.get_text(" ", strip=True)


def fetch() -> list[dict]:
    resp = get(LIST_URL)
    soup = BeautifulSoup(resp.text, "html.parser")

    jobs: list[dict] = []
    for card in soup.find_all("article"):
        try:
            link = card.find("a", class_="jl-title__link")
            if link is None or not link.get("href"):
                continue
            href = link["href"]
            url = href if href.startswith("http") else BASE + href
            title = _text(link)
            if not title:
                continue

            company = _text(card.find("span", class_="jl-company__name")) or None
            location = _text(card.find("p", class_="jl-location")) or "Pakistan"
            salary = _text(card.find("span", class_="jl-salary")) or None
            desc = _text(card.find("p", class_="jl-desc"))

            chips = [_text(c) for c in card.find_all("span", class_="jl-chip")]
            chips = [c for c in chips if c]
            extras = []
            for chip in chips:
                if _YEARS_CHIP.match(chip):
                    extras.append(f"Experience: {chip}")  # years-slider can parse this
                else:
                    extras.append(chip)
            description = " · ".join(filter(None, [desc] + extras))

            jobs.append(
                {
                    "source": SOURCE,
                    "external_id": url.rstrip("/").rsplit("/", 1)[-1],
                    "url": url,
                    "title": title,
                    "company": company,
                    "description": description,
                    "tags": chips[:6],
                    "budget": salary,
                    "location": location,
                    "posted_at": None,  # listing shows "Recently posted" only
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad card must not kill the batch
            log.warning("mustakbil: skipping malformed card: %s", exc)
    if not jobs:
        log.info("mustakbil: 0 cards parsed (markup change?)")
    return jobs
