"""Wellfound (formerly AngelList Talent) fetcher via Selenium.

IMPORTANT — read before enabling:
  * Wellfound sits behind a DataDome device check that hard-403s plain HTTP
    clients, so this needs headless Chrome. Loading the homepage first clears the
    check for the session; the role page then renders normally (verified live).
  * Uses the PUBLIC role search (no login, no credentials).
  * Scraping Wellfound is against their Terms of Service. This is for personal,
    low-volume use (two page loads per cycle). If the device check does not clear,
    it returns nothing rather than fighting it.
  * Off by default. Requires the `selenium` package + Google Chrome installed.
    Selenium 4.6+ auto-manages the matching chromedriver.

Config (env / .env):
  WELLFOUND_ROLE        role slug, default "software-engineer"
  WELLFOUND_REMOTE      "0" to search all locations (default remote-only)
  WELLFOUND_MAX_JOBS    default 50
  CHROME_BINARY         optional explicit path to chrome.exe
"""
from __future__ import annotations

import logging
import os
import re
import time

from bs4 import BeautifulSoup

from .util import clean_tags, parse_relative_dt

log = logging.getLogger("jobradar.fetchers.wellfound")

SOURCE = "wellfound"
BASE = "https://wellfound.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# /jobs/<id>-<slug> is a listing; /jobs/signup and friends are not.
_JOB_HREF = re.compile(r"^/jobs/\d+")
_COMPANY_HREF = re.compile(r"^/company/")
# DataDome / Cloudflare interstitials, so a block reads as "empty" not "broken".
_BLOCK_MARKERS = ("captcha-delivery.com", "Just a moment", "Attention Required")


class WellfoundError(RuntimeError):
    """Raised when Selenium/Chrome isn't available; isolated by the pipeline."""


def _search_url() -> str:
    role = (os.getenv("WELLFOUND_ROLE", "software-engineer").strip()
            or "software-engineer")
    remote = os.getenv("WELLFOUND_REMOTE", "1").strip() not in ("0", "false", "no")
    # /role/r/<slug> is the remote-only variant; /role/<slug> is all locations.
    return f"{BASE}/role/r/{role}" if remote else f"{BASE}/role/{role}"


def fetch() -> list[dict]:
    max_jobs = _env_int("WELLFOUND_MAX_JOBS", 50, lo=1, hi=200)
    html = _load_search_html(_search_url())
    return _parse_jobs(html, max_jobs)


def _parse_jobs(html: str, max_jobs: int = 50) -> list[dict]:
    """Parse role-search startup cards. Pure function so it's unit-testable."""
    if not html:
        return []
    if any(marker in html for marker in _BLOCK_MARKERS):
        log.info("wellfound: device check did not clear; skipping this cycle")
        return []

    soup = BeautifulSoup(html, "html.parser")
    headers = soup.select('div[data-testid="startup-header"]')

    jobs: list[dict] = []
    seen: set[str] = set()
    for header in headers:
        if len(jobs) >= max_jobs:
            break
        try:
            card = _card_root(header)
            if card is None:
                continue
            company = _company(header)
            tagline = _tagline(header)

            for anchor in card.find_all("a", href=_JOB_HREF):
                href = (anchor.get("href") or "").split("?")[0]
                title = anchor.get_text(" ", strip=True)
                if not href or not title or href in seen:
                    continue
                seen.add(href)

                row = _row_root(anchor)
                meta = _meta(row)
                description = " · ".join(
                    filter(None, [title, company, tagline, meta.get("location"),
                                  meta.get("job_type"), meta.get("salary"),
                                  meta.get("experience")])
                )

                jobs.append(
                    {
                        "source": SOURCE,
                        "external_id": _job_id(href),
                        "url": BASE + href,
                        "title": title,
                        "company": company,
                        "description": description,
                        "tags": clean_tags(
                            [meta.get("job_type"), meta.get("experience")]
                        ),
                        "budget": meta.get("salary"),
                        "location": meta.get("location"),
                        "posted_at": parse_relative_dt(meta.get("age")),
                    }
                )
                if len(jobs) >= max_jobs:
                    return jobs
        except Exception as exc:  # noqa: BLE001 - one bad card must not kill the batch
            log.warning("wellfound: skipping malformed card: %s", exc)

    if not jobs:
        log.info("wellfound: 0 cards parsed (authwall or markup change)")
    return jobs


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        return default
    return max(lo, min(hi, value))


def _card_root(header):
    """Nearest ancestor of the company header that also holds its job rows."""
    node = header
    for _ in range(6):
        parent = node.parent
        if parent is None:
            return None
        node = parent
        if node.find("a", href=_JOB_HREF):
            return node
    return None


def _row_root(anchor):
    """Nearest ancestor holding one job's metadata but not a second job link."""
    node = anchor
    for _ in range(4):
        parent = node.parent
        if parent is None:
            break
        if len(parent.find_all("a", href=_JOB_HREF)) > 1:
            break  # would swallow a sibling listing
        node = parent
    return node


def _company(header) -> str | None:
    link = header.find("a", href=_COMPANY_HREF)
    if link is not None:
        text = link.get_text(" ", strip=True)
        if text:
            return text
    heading = header.find(["h2", "h3"])
    return heading.get_text(" ", strip=True) if heading else None


def _tagline(header) -> str | None:
    """The one-line company pitch under its name."""
    for span in header.find_all("span"):
        text = span.get_text(" ", strip=True)
        if text and 15 < len(text) <= 180 and "Employees" not in text:
            return text
    return None


def _job_id(href: str) -> str:
    slug = href.rstrip("/").rsplit("/", 1)[-1]
    return slug.split("-", 1)[0] or slug


def _meta(row) -> dict:
    """Classify a row's metadata chips by content, since order varies."""
    out: dict[str, str | None] = {
        "salary": None, "location": None, "experience": None,
        "job_type": None, "age": None,
    }
    if row is None:
        return out

    # Reversed document order visits the innermost chips first, so a slot is
    # filled by the specific "$100k – $137k" span rather than by an outer
    # container whose text happens to contain all of the chips at once.
    for el in reversed(row.find_all(["span", "div"])):
        text = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if not text or len(text) > 120:
            continue
        lowered = text.lower()

        if out["age"] is None and ("ago" in lowered or lowered in ("today", "just now")):
            out["age"] = text
        elif out["salary"] is None and ("$" in text or "€" in text or "£" in text):
            out["salary"] = text
        elif out["experience"] is None and re.search(r"\byears?\b.*\bexp", lowered):
            out["experience"] = text
        elif out["job_type"] is None and lowered in (
            "full-time", "part-time", "contract", "internship", "cofounder"
        ):
            out["job_type"] = text
        elif out["location"] is None and (
            "remote" in lowered or "on-site" in lowered or "hybrid" in lowered
        ):
            out["location"] = text
    return out


def _load_search_html(url: str) -> str:
    """Warm the homepage (clears the device check), then load the role search."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise WellfoundError(
            "selenium is not installed — run: pip install selenium"
        ) from exc

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,3000")
    opts.add_argument(f"--user-agent={USER_AGENT}")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--lang=en-US,en")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_bin = os.getenv("CHROME_BINARY", "").strip()
    if chrome_bin:
        opts.binary_location = chrome_bin

    try:
        driver = webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001 - driver/browser launch failure
        raise WellfoundError(f"could not start Chrome/chromedriver: {exc}") from exc

    try:
        driver.set_page_load_timeout(60)
        # The homepage is what satisfies the device check; without it the role
        # page returns the DataDome interstitial instead of listings.
        driver.get(BASE + "/")
        time.sleep(8)
        driver.get(url)
        time.sleep(9)  # let the role listings render
        return driver.page_source
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass
