"""LinkedIn fetcher via Selenium (headless Chrome).

IMPORTANT — read before enabling:
  * This uses LinkedIn's PUBLIC guest job search (no login, no credentials). It
    never signs in with your account, so it can't get your account restricted the
    way credentialed automation can.
  * Scraping LinkedIn is against their Terms of Service. This is provided for
    personal, low-volume use (one page load per cycle). Use it at your own risk;
    if you get an authwall/redirect, it returns nothing rather than fighting it.
  * Off by default. Requires the `selenium` package + Google Chrome installed.
    Selenium 4.6+ auto-manages the matching chromedriver.

Config (env / .env):
  LINKEDIN_SEARCH_KEYWORDS   default "developer"
  LINKEDIN_SEARCH_LOCATION   default "" (worldwide)
  LINKEDIN_SEARCH_REMOTE     "1" to restrict to remote (f_WT=2)
  LINKEDIN_MAX_JOBS          default 50
  CHROME_BINARY              optional explicit path to chrome.exe
"""
from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .util import parse_dt

log = logging.getLogger("jobradar.fetchers.linkedin_selenium")

SOURCE = "linkedin_web"
# Public guest job search (renders job cards without a login).
SEARCH_URL = "https://www.linkedin.com/jobs/search"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class LinkedInError(RuntimeError):
    """Raised when Selenium/Chrome isn't available; isolated by the pipeline."""


def _search_url() -> str:
    params = {"keywords": os.getenv("LINKEDIN_SEARCH_KEYWORDS", "developer")}
    loc = os.getenv("LINKEDIN_SEARCH_LOCATION", "").strip()
    if loc:
        params["location"] = loc
    if os.getenv("LINKEDIN_SEARCH_REMOTE", "").strip() in ("1", "true", "yes"):
        params["f_WT"] = "2"  # LinkedIn's "Remote" workplace-type filter
    params["f_TPR"] = "r604800"  # posted in the last 7 days
    return SEARCH_URL + "?" + urlencode(params)


def _clean_url(href: str) -> str:
    """Strip LinkedIn tracking query params, keep the canonical /jobs/view/<id>."""
    return (href or "").split("?")[0].strip()


def _parse_jobs(html: str, max_jobs: int = 50) -> list[dict]:
    """Parse guest-search job cards. Pure function so it's unit-testable."""
    soup = BeautifulSoup(html or "", "html.parser")
    cards = soup.select("li div.base-card") or soup.select("div.base-search-card") \
        or soup.select("ul.jobs-search__results-list li")

    jobs: list[dict] = []
    seen: set[str] = set()
    for card in cards:
        try:
            link = card.select_one("a.base-card__full-link") \
                or card.select_one("a.base-search-card__title") \
                or card.select_one("a[href*='/jobs/view/']")
            href = _clean_url(link["href"]) if link and link.get("href") else ""
            if not href or href in seen:
                continue

            title_el = card.select_one("h3.base-search-card__title") \
                or card.select_one(".base-search-card__title")
            title = title_el.get_text(" ", strip=True) if title_el else ""
            if not title:
                continue
            seen.add(href)

            comp_el = card.select_one("h4.base-search-card__subtitle a") \
                or card.select_one("h4.base-search-card__subtitle") \
                or card.select_one(".base-search-card__subtitle")
            company = comp_el.get_text(" ", strip=True) if comp_el else None

            loc_el = card.select_one("span.job-search-card__location") \
                or card.select_one(".job-search-card__location")
            location = loc_el.get_text(" ", strip=True) if loc_el else None

            time_el = card.select_one("time")
            posted_at = parse_dt(time_el.get("datetime")) if time_el else None

            jobs.append(
                {
                    "source": SOURCE,
                    "external_id": href.rstrip("/").rsplit("/", 1)[-1] or href,
                    "url": href,
                    "title": title,
                    "company": company,
                    "description": " · ".join(filter(None, [title, company, location])),
                    "tags": [],
                    "budget": None,
                    "location": location,
                    "posted_at": posted_at,
                }
            )
            if len(jobs) >= max_jobs:
                break
        except Exception as exc:  # noqa: BLE001 - one bad card must not kill the batch
            log.warning("linkedin_web: skipping malformed card: %s", exc)
    if not jobs:
        log.info("linkedin_web: 0 cards parsed (authwall/redirect or markup change)")
    return jobs


def _load_search_html(url: str) -> str:
    """Load the guest search page in headless Chrome and return rendered HTML."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise LinkedInError(
            "selenium is not installed — run: pip install selenium"
        ) from exc

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,2400")
    opts.add_argument(f"--user-agent={USER_AGENT}")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_bin = os.getenv("CHROME_BINARY", "").strip()
    if chrome_bin:
        opts.binary_location = chrome_bin

    try:
        driver = webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001 - driver/browser launch failure
        raise LinkedInError(f"could not start Chrome/chromedriver: {exc}") from exc

    try:
        driver.set_page_load_timeout(30)
        driver.get(url)
        time.sleep(3)  # let the guest job cards render
        # One scroll to pull in a few more lazily-loaded cards (still one page load).
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        return driver.page_source
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass


def fetch() -> list[dict]:
    max_jobs = int(os.getenv("LINKEDIN_MAX_JOBS", "50") or 50)
    html = _load_search_html(_search_url())
    return _parse_jobs(html, max_jobs)
