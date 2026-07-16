"""LinkedIn job-alert email fetcher (IMAP).

The user auto-forwards LinkedIn job-alert emails into a folder. This reads unseen
messages via imap-tools, BeautifulSoups the HTML, extracts job title/company/URL
triples, and marks the messages seen. Credentials come only from .env:
IMAP_HOST, IMAP_USER, IMAP_PASSWORD, IMAP_FOLDER (default INBOX).
"""
from __future__ import annotations

import logging
import os
import re

from bs4 import BeautifulSoup

log = logging.getLogger("jobradar.fetchers.linkedin_email")

SOURCE = "linkedin_email"

_JOB_ID_RE = re.compile(r"/jobs/view/(\d+)")
_SKIP_TITLES = {
    "view job", "view jobs", "see all jobs", "view all jobs", "see more jobs",
    "unsubscribe", "see all", "jobs", "linkedin",
}


def fetch() -> list[dict]:
    host = os.getenv("IMAP_HOST")
    user = os.getenv("IMAP_USER")
    password = os.getenv("IMAP_PASSWORD")
    folder = os.getenv("IMAP_FOLDER", "INBOX")

    if not (host and user and password):
        log.info("linkedin_email: IMAP credentials not set in .env; skipping")
        return []

    from imap_tools import AND, MailBox

    jobs: dict[str, dict] = {}
    with MailBox(host).login(user, password, initial_folder=folder) as mailbox:
        for msg in mailbox.fetch(AND(seen=False), mark_seen=True, bulk=True):
            html = msg.html or ""
            if not html:
                continue
            for job in _extract_jobs(html):
                jobs.setdefault(job["external_id"], job)

    log.info("linkedin_email: extracted %d job(s) from unseen mail", len(jobs))
    return list(jobs.values())


def _extract_jobs(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, dict] = {}

    for a in soup.find_all("a", href=True):
        m = _JOB_ID_RE.search(a["href"])
        if not m:
            continue
        job_id = m.group(1)
        title = a.get_text(" ", strip=True)
        if not title or len(title) < 3 or title.lower() in _SKIP_TITLES:
            continue
        if job_id in out:
            continue  # first anchor for an id is usually the title link

        company, context = _nearby(a, title)
        out[job_id] = {
            "source": SOURCE,
            "external_id": job_id,
            "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
            "title": title[:300],
            "company": company,
            "description": context or title,
            "tags": [],
            "budget": None,
            "location": None,
            "posted_at": None,
        }
    return list(out.values())


def _nearby(anchor, title: str) -> tuple[str | None, str]:
    """Best-effort: company = the line right after the title in the enclosing block."""
    block = anchor.find_parent(["td", "div", "table"]) or anchor
    text = block.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

    company = None
    for i, line in enumerate(lines):
        if line == title and i + 1 < len(lines):
            nxt = lines[i + 1]
            if nxt.lower() != title.lower():
                company = nxt.split("·")[0].split("•")[0].strip()[:200] or None
            break
    return company, text[:2000]
