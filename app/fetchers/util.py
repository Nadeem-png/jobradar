"""Normalization helpers shared by fetchers."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

_TAG_RE = re.compile(r"<[^>]+>")


def parse_dt(value) -> datetime | None:
    """Best-effort parse of a datetime from ISO strings or a struct_time.

    Returns a naive UTC datetime (SQLite stores naive), or None.
    """
    if value is None:
        return None

    # feedparser struct_time (published_parsed)
    if hasattr(value, "tm_year"):
        try:
            return datetime(*value[:6])
        except Exception:
            return None

    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            return None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Normalize trailing Z to +00:00 for fromisoformat.
        s = s.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
        except Exception:
            return None

    return None


def html_to_text(html: str | None) -> str:
    """Strip HTML to reasonably clean plain text."""
    if not html:
        return ""
    try:
        text = BeautifulSoup(html, "html.parser").get_text(separator="\n")
    except Exception:
        text = _TAG_RE.sub(" ", html)
    # Collapse excessive blank lines / whitespace.
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


# Local part must start with an alphanumeric (no leading dot/punctuation).
_EMAIL_RE = re.compile(r"[A-Za-z0-9_%+\-][A-Za-z0-9._%+\-]*@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Local-parts / domains that are never a real "email us to apply" contact.
_EMAIL_SKIP_LOCAL = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon")
_EMAIL_SKIP_DOMAINS = (
    "example.com", "example.org", "example.net", "domain.com", "yourcompany.com",
    "company.com", "email.com", "sentry.io", "wixpress.com", "sentry-next.wixpress.com",
)


def extract_emails(text: str | None) -> list[str]:
    """Pull real contact emails out of a job listing (deduped, noise filtered)."""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in _EMAIL_RE.findall(text):
        email = raw.strip().lower().strip(".")
        if email in seen:
            continue
        local, _, domain = email.partition("@")
        if any(s in local for s in _EMAIL_SKIP_LOCAL):
            continue
        # Skip the listed domains and any of their sub-domains (cdn.example.com, …).
        if any(domain == d or domain.endswith("." + d) for d in _EMAIL_SKIP_DOMAINS):
            continue
        if email.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            continue
        seen.add(email)
        out.append(email)
        if len(out) >= 5:  # a job rarely lists more than a couple; cap noise
            break
    return out


def clean_tags(tags) -> list[str]:
    """Normalize a tag collection into a de-duplicated list of strings."""
    if not tags:
        return []
    out: list[str] = []
    seen = set()
    for t in tags:
        if not t:
            continue
        s = str(t).strip()
        if not s:
            continue
        key = s.lower()
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out
