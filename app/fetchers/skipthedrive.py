"""SkipTheDrive fetcher — the WordPress REST API behind the job board.

The site's /feed/ redirects to the homepage, so RSS is out. Jobs instead live in
a custom ``job`` post type exposed at /wp-json/wp/v2/job (verified live: ~28k
posts). Category 59 ("imported-job") is the real firehose; 58 ("sponsored-job")
is paid placement and is skipped.

Company is not a field, but WordPress builds each slug as
"<company>-<title>-<post id>", so it is recovered by peeling the post id and
then the title's own slug tokens off the end of the slug.
"""
from __future__ import annotations

import html
import logging
import os
import re

from .http import get
from .util import html_to_text, parse_dt

log = logging.getLogger("jobradar.fetchers.skipthedrive")

SOURCE = "skipthedrive"
API_URL = "https://www.skipthedrive.com/wp-json/wp/v2/job"
IMPORTED_JOB_CATEGORY = 59
# Only the fields we use — the full payload carries a lot of WP bookkeeping.
_FIELDS = "id,link,slug,date_gmt,title,content"

# Tokens that show up as a slug prefix but aren't a company.
_NON_COMPANY_TOKENS = {"sponsored", "job", "sponsored-job", "featured"}
# Slug tokens that are artifacts of the title rather than words in it: WordPress
# slugifies the *escaped* title, so "&" survives as the literal "amp".
_SLUG_NOISE = {"amp", "nbsp", "quot", "8211", "8217"}
# Words that should stay upper-case when a slug is turned back into a name.
_ACRONYMS = {
    "ai", "ml", "hr", "it", "io", "ui", "ux", "api", "apis", "sdk", "saas",
    "crm", "erp", "b2b", "b2c", "us", "usa", "uk", "eu", "llc", "inc", "ltd",
    "cx", "qa", "bi", "iot", "vr", "ar", "3d", "pr", "seo", "sms", "tv",
}

_SALARY_RE = re.compile(
    r"\$\s?[\d,]+(?:\.\d+)?\s*(?:k|K)?\s*(?:[-–—]|to)\s*\$?\s?[\d,]+(?:\.\d+)?\s*"
    r"(?:k|K)?\s*(?:/\s*(?:hr|hour|yr|year|mo|month)|per\s+(?:hour|year|month))?"
)
_LOCATION_RE = re.compile(r"Location\s*:\s*([^\n]{2,160})", re.I)
# When a body puts everything on one line, stop the location before the next
# section heading so it doesn't swallow the job description.
_LOCATION_STOP_RE = re.compile(
    r"\s+(?:Job\s+(?:Summary|Description|Type|Title)|About\s+(?:the|us|our)|"
    r"Responsibilities|Requirements|Role\s+Overview|Compensation|Salary)\b",
    re.I,
)


def fetch() -> list[dict]:
    limit = _env_int("SKIPTHEDRIVE_MAX_JOBS", 50, lo=1, hi=100)
    resp = get(
        f"{API_URL}?per_page={limit}&categories={IMPORTED_JOB_CATEGORY}&_fields={_FIELDS}"
    )
    try:
        posts = resp.json()
    except Exception as exc:  # noqa: BLE001 - a WP error page instead of JSON
        log.warning("skipthedrive: response was not JSON (%s)", exc)
        return []
    if not isinstance(posts, list):
        log.warning("skipthedrive: unexpected payload type %s", type(posts).__name__)
        return []

    jobs: list[dict] = []
    for post in posts:
        try:
            url = (post.get("link") or "").strip()
            title = _plain(_rendered(post.get("title")))
            if not url or not title:
                continue

            body_html = _rendered(post.get("content"))
            description = html_to_text(body_html)

            jobs.append(
                {
                    "source": SOURCE,
                    "external_id": str(post.get("id") or url),
                    "url": url,
                    "title": title,
                    "company": _company_from_slug(post.get("slug") or "", title),
                    "description": description,
                    "tags": [],
                    "budget": _salary(description),
                    # SkipTheDrive is a remote-only board; the body usually
                    # restates it plus any regional restriction.
                    "location": _location(description) or "Remote",
                    "posted_at": parse_dt(post.get("date_gmt")),
                }
            )
        except Exception as exc:  # noqa: BLE001 - one bad post must not kill the batch
            log.warning("skipthedrive: skipping malformed post: %s", exc)

    if not jobs:
        log.info("skipthedrive: 0 jobs parsed (API shape change?)")
    return jobs


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        return default
    return max(lo, min(hi, value))


def _rendered(field) -> str:
    """WP wraps rendered strings as {"rendered": "..."}."""
    if isinstance(field, dict):
        return field.get("rendered") or ""
    return field or ""


def _plain(text: str) -> str:
    """Unescape entities and collapse whitespace (titles arrive HTML-escaped)."""
    return re.sub(r"\s+", " ", html.unescape(text or "")).strip()


def _slug_tokens(text: str) -> list[str]:
    """Slugify like WordPress does: non-alphanumerics collapse to separators."""
    return [t for t in re.split(r"[^a-z0-9]+", html.unescape(text or "").lower()) if t]


def _company_from_slug(slug: str, title: str) -> str | None:
    """Recover the company from a "<company>-<title>-<id>" slug.

    Walks the slug backwards while each token belongs to the title, then treats
    whatever remains at the front as the company.
    """
    tokens = _slug_tokens(slug)
    if not tokens:
        return None
    if tokens[-1].isdigit():  # trailing WP post id
        tokens.pop()

    allowed = set(_slug_tokens(title)) | _SLUG_NOISE
    cut = len(tokens)
    while cut > 0 and tokens[cut - 1] in allowed:
        cut -= 1
    company_tokens = tokens[:cut]

    # Nothing left (slug was just the title), or an implausibly long prefix.
    if not company_tokens or len(company_tokens) > 5:
        return None
    if all(t in _NON_COMPANY_TOKENS for t in company_tokens):
        return None
    return " ".join(
        t.upper() if t in _ACRONYMS else t.capitalize() for t in company_tokens
    )


def _salary(description: str) -> str | None:
    m = _SALARY_RE.search(description or "")
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else None


def _location(description: str) -> str | None:
    m = _LOCATION_RE.search(description or "")
    if not m:
        return None
    value = re.sub(r"\s+", " ", m.group(1))
    stop = _LOCATION_STOP_RE.search(value)
    if stop:
        value = value[: stop.start()]
    value = value.strip(" .;,")
    return value[:200] or None
