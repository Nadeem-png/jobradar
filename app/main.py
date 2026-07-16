"""JobRadar FastAPI app: routes, templates, scheduler startup.

Phase 1: core app + dashboard + apply flow + kanban tracker + 3 fetchers.
Phase 2: AI scoring + proposal drafting, Settings page, re-score, low-match section.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import ai, notify, resume
from .db import Base, SessionLocal, engine, ensure_database_exists, get_db
from .fetchers import FETCHERS, SOURCE_LABELS
from .models import STATUSES, TRACKER_COLUMNS, Job, utcnow
from .pipeline import dedupe_hash, run_fetch_cycle
from .settings import all_settings, get_setting, seed_settings, set_setting

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("jobradar")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

scheduler = BackgroundScheduler(timezone="UTC")


# --------------------------------------------------------------------------- #
# Template helpers
# --------------------------------------------------------------------------- #
def relative_time(dt: datetime | None) -> str:
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    # Stored datetimes are naive UTC; make comparable.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def safe_url(url: str | None) -> str:
    """Only allow http(s) links to reach href/window.open — blocks javascript: etc."""
    u = (url or "").strip()
    return u if u.lower().startswith(("http://", "https://")) else "#"


def score_class(score: int | None) -> str:
    if score is None:
        return "unscored"
    if score >= 8:
        return "high"
    if score >= 5:
        return "mid"
    return "low"


templates.env.filters["relative_time"] = relative_time
templates.env.filters["safe_url"] = safe_url
templates.env.globals["score_class"] = score_class
templates.env.globals["SOURCE_LABELS"] = SOURCE_LABELS
templates.env.globals["TRACKER_COLUMNS"] = TRACKER_COLUMNS
templates.env.globals["STATUSES"] = STATUSES
# All sources that have a real fetcher, for the Settings toggles (in registry order).
FETCH_SOURCES = list(FETCHERS.keys())
templates.env.globals["FETCH_SOURCES"] = FETCH_SOURCES
# ai_status() is read live in templates so the AI banner reflects the current env.
templates.env.globals["ai_status"] = lambda: {
    "key_present": ai.api_key_present(),
    "model": ai.model_name(),
}
templates.env.globals["telegram_enabled"] = notify.telegram_enabled


# --------------------------------------------------------------------------- #
# Startup / shutdown
# --------------------------------------------------------------------------- #
def _initial_fetch() -> None:
    """Run one fetch cycle on startup, off the event loop thread."""
    try:
        run_fetch_cycle()
    except Exception:  # noqa: BLE001
        log.exception("initial fetch cycle failed")


def _ensure_schema() -> None:
    """Lightweight migration: add columns introduced after a DB was first created.

    create_all() only creates missing tables; it won't ALTER an existing one, so
    add ai_attempts / notified_at to older databases. Dialect-agnostic (works on
    SQLite and MySQL) via the SQLAlchemy inspector; the ALTER syntax is common to
    both. On a fresh DB, create_all already made every column, so this is a no-op.
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if not insp.has_table("jobs"):
        return
    cols = {c["name"] for c in insp.get_columns("jobs")}
    with engine.begin() as conn:
        if "ai_attempts" not in cols:
            conn.execute(
                text("ALTER TABLE jobs ADD COLUMN ai_attempts INTEGER NOT NULL DEFAULT 0")
            )
            log.info("migrated jobs table: added ai_attempts column")
        if "notified_at" not in cols:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN notified_at DATETIME"))
            log.info("migrated jobs table: added notified_at column")
        if "contact_emails" not in cols:
            conn.execute(text("ALTER TABLE jobs ADD COLUMN contact_emails JSON"))
            log.info("migrated jobs table: added contact_emails column")


def _backfill_contact_emails() -> int:
    """Fill contact_emails for jobs that predate the column (NULL), from their text."""
    from .fetchers.util import extract_emails

    db = SessionLocal()
    try:
        rows = (
            db.execute(select(Job).where(Job.contact_emails.is_(None)))
            .scalars()
            .all()
        )
        for job in rows:
            job.contact_emails = extract_emails(f"{job.title}\n{job.description}")
        if rows:
            db.commit()
            log.info("backfilled contact_emails for %d job(s)", len(rows))
        return len(rows)
    finally:
        db.close()


def _serverless() -> bool:
    """True on serverless hosts (Vercel sets VERCEL=1) where there is no
    long-lived process: the scheduler and background threads are skipped and
    fetching happens via /cron/fetch instead."""
    return os.getenv("VERCEL") == "1" or os.getenv("JOBRADAR_SERVERLESS") == "1"


def _init_storage() -> None:
    """Create the database/tables/columns and seed settings. Idempotent."""
    ensure_database_exists()  # MySQL: create the database if it doesn't exist yet
    Base.metadata.create_all(bind=engine)
    _ensure_schema()
    try:
        _backfill_contact_emails()
    except Exception:  # noqa: BLE001
        log.exception("contact_emails backfill failed")

    db = SessionLocal()
    try:
        seed_settings(db)
    finally:
        db.close()


if _serverless():
    # Serverless runtimes (Vercel) may not fire ASGI lifespan events, so make
    # sure storage exists as soon as the module is imported.
    _init_storage()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_storage()

    if _serverless():
        # Serverless (Vercel etc.): no long-lived process, so no APScheduler and
        # no startup fetch thread — fetching runs via the /cron/fetch endpoint
        # (platform cron) or a manual "Fetch now".
        log.info("serverless mode: scheduler disabled; use /cron/fetch")
        yield
        return

    # Periodic fetch every 10 minutes.
    scheduler.add_job(
        run_fetch_cycle,
        "interval",
        minutes=10,
        id="fetch_cycle",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    scheduler.start()
    log.info("scheduler started (fetch every 10 min)")

    # Kick off an immediate fetch in the background so startup isn't blocked.
    threading.Thread(target=_initial_fetch, daemon=True).start()

    yield

    scheduler.shutdown(wait=False)


app = FastAPI(title="JobRadar", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Reject oversized request bodies (e.g. huge résumé uploads) before they're parsed
# to disk/RAM. A bit above the 5 MB résumé cap to allow multipart overhead.
MAX_REQUEST_BYTES = 8 * 1024 * 1024


@app.middleware("http")
async def _limit_request_body(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_REQUEST_BYTES:
        return Response("Request body too large.", status_code=413)
    return await call_next(request)


# --------------------------------------------------------------------------- #
# Access-key gate (login)
# --------------------------------------------------------------------------- #
# Set JOBRADAR_ACCESS_KEY in .env to require a key before anything is shown.
# Leave it unset to run open (e.g. purely local use). JOBRADAR_DISABLE_AUTH=1
# force-disables it (used by the test suite).
AUTH_COOKIE = "jr_auth"
AUTH_COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days


def _access_key() -> str:
    return (os.getenv("JOBRADAR_ACCESS_KEY") or "").strip()


def auth_enabled() -> bool:
    return bool(_access_key()) and os.getenv("JOBRADAR_DISABLE_AUTH", "") != "1"


templates.env.globals["auth_enabled"] = auth_enabled  # nav shows Logout when on


def _auth_token() -> str:
    """Cookie value derived from the key — stateless, and rotating the key in
    .env instantly invalidates every issued cookie."""
    return hmac.new(_access_key().encode(), b"jobradar-cookie-v1", hashlib.sha256).hexdigest()


def _is_authed(request: Request) -> bool:
    return hmac.compare_digest(request.cookies.get(AUTH_COOKIE, ""), _auth_token())


def _safe_next(raw: str | None) -> str:
    """Only allow same-site relative redirect targets."""
    nxt = (raw or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//") and ":" not in nxt:
        return nxt
    return "/"


# /cron/fetch is exempt from the login gate: it enforces its own CRON_SECRET
# (and refuses to run when the secret is unset).
_AUTH_EXEMPT = ("/login", "/static/", "/favicon.ico", "/cron/fetch")


@app.middleware("http")
async def _require_access_key(request: Request, call_next):
    if auth_enabled():
        path = request.url.path
        exempt = any(path == p or path.startswith(p) for p in _AUTH_EXEMPT)
        if not exempt and not _is_authed(request):
            # HTMX partial requests can't render a redirect into their target —
            # tell HTMX to do a full-page redirect instead.
            if request.headers.get("hx-request"):
                return Response(status_code=401, headers={"HX-Redirect": "/login"})
            target = _safe_next(request.url.path)
            return RedirectResponse(f"/login?next={quote(target)}", status_code=303)
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not auth_enabled() or _is_authed(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request, "login.html",
        {"error": None, "next": _safe_next(request.query_params.get("next"))},
    )


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, key: str = Form(""), next: str = Form("/")):
    if not auth_enabled():
        return RedirectResponse("/", status_code=303)
    target = _safe_next(next)
    if hmac.compare_digest(key.strip(), _access_key()):
        resp = RedirectResponse(target, status_code=303)
        resp.set_cookie(
            AUTH_COOKIE, _auth_token(), max_age=AUTH_COOKIE_MAX_AGE,
            httponly=True, samesite="lax", path="/",
        )
        return resp
    return templates.TemplateResponse(
        request, "login.html",
        {"error": "Wrong access key — try again.", "next": target},
        status_code=401,
    )


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(AUTH_COOKIE, path="/")
    return resp


# --------------------------------------------------------------------------- #
# Query helpers
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Country / region filter
# --------------------------------------------------------------------------- #
# Locations are free text ("Remote (US)", "Worldwide", "Lahore, Pakistan", "EMEA"),
# so each region is a set of case-insensitive alias words/phrases plus optional
# case-sensitive short tokens (US, UK, EU…) that would false-positive lowercased
# ("join us", "ukulele"). Matched against location + title + tags in Python.
COUNTRY_FILTERS: dict[str, dict] = {
    "worldwide": {"label": "Worldwide / Anywhere",
                  "terms": ["worldwide", "anywhere", "global", "international"], "cs": []},
    # NOTE: no bare "america" term — it would false-match "South/Latin America".
    "usa": {"label": "USA",
            "terms": ["usa", "united states", "north america", "northern america"],
            "cs": ["US", "U.S."]},
    "canada": {"label": "Canada", "terms": ["canada", "canadian"], "cs": []},
    "uk": {"label": "UK", "terms": ["united kingdom", "britain", "england", "london"],
           "cs": ["UK", "U.K."]},
    "europe": {"label": "Europe", "terms": ["europe", "european", "emea"], "cs": ["EU"]},
    "germany": {"label": "Germany", "terms": ["germany", "german", "berlin", "munich", "deutschland"], "cs": []},
    "australia": {"label": "Australia / NZ", "terms": ["australia", "new zealand", "sydney", "melbourne"],
                  "cs": ["ANZ"]},
    "india": {"label": "India", "terms": ["india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad"], "cs": []},
    "pakistan": {"label": "Pakistan", "terms": ["pakistan", "lahore", "karachi", "islamabad"], "cs": []},
    "middle_east": {"label": "Middle East / UAE", "terms": ["dubai", "middle east", "saudi", "qatar", "abu dhabi"],
                    "cs": ["UAE", "GCC"]},
    "latam": {"label": "Latin America", "terms": ["latin america", "south america", "brazil", "mexico", "argentina"],
              "cs": ["LATAM"]},
    "asia": {"label": "Asia / APAC", "terms": ["asia", "singapore", "japan", "philippines", "vietnam", "indonesia"],
             "cs": ["APAC"]},
}
templates.env.globals["COUNTRY_FILTERS"] = COUNTRY_FILTERS


def _matches_country(job: Job, key: str) -> bool:
    spec = COUNTRY_FILTERS.get(key)
    if not spec:
        return True
    raw = " ".join(filter(None, [job.location or "", job.title or "", " ".join(job.tags or [])]))
    low = raw.lower()
    for term in spec["terms"]:
        if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", low):
            return True
    for tok in spec["cs"]:
        # Case-sensitive whole-token match on the original text (US, UK, EU, …).
        if re.search(r"(?<![A-Za-z])" + re.escape(tok) + r"(?![A-Za-z])", raw):
            return True
    return False


# --------------------------------------------------------------------------- #
# Date-posted filter options ("show jobs posted within the last N days")
# --------------------------------------------------------------------------- #
POSTED_WITHIN_OPTIONS = [
    ("", "Any time"), ("1", "24 hours"), ("3", "3 days"), ("7", "7 days"),
    ("14", "14 days"), ("30", "30 days"), ("90", "90 days"),
]
templates.env.globals["POSTED_WITHIN_OPTIONS"] = POSTED_WITHIN_OPTIONS


# --------------------------------------------------------------------------- #
# Employment-type filter
# --------------------------------------------------------------------------- #
# Employment type is free text scattered across title/tags/budget/description
# ("Full-time", tag "full_time", "part time", "Contract / Part-time"), so each
# type is a regex alternation matched with a leading word boundary. Types marked
# open_end skip the trailing boundary so "contractor"/"freelancer" also match.
EMPLOYMENT_TYPES: dict[str, dict] = {
    "full_time": {"label": "Full-time", "pattern": r"full[\s_-]?time"},
    "part_time": {"label": "Part-time", "pattern": r"part[\s_-]?time"},
    "contract": {"label": "Contract", "pattern": r"contract|c2c|1099", "open_end": True},
    "freelance": {"label": "Freelance", "pattern": r"freelance|upwork", "open_end": True},
    "temporary": {"label": "Temporary", "pattern": r"temporary|interim"},
    "permanent": {"label": "Permanent", "pattern": r"permanent"},
    "internship": {"label": "Internship", "pattern": r"intern(ship)?s?|working student|werkstudent"},
    "seasonal": {"label": "Seasonal", "pattern": r"seasonal"},
    "volunteer": {"label": "Volunteer", "pattern": r"volunteer"},
}
templates.env.globals["EMPLOYMENT_TYPES"] = EMPLOYMENT_TYPES


def _matches_employment(job: Job, key: str) -> bool:
    spec = EMPLOYMENT_TYPES.get(key)
    if not spec:
        return True
    if key == "freelance" and job.source == "upwork_rss":
        return True
    hay = " ".join(
        filter(None, [job.title or "", " ".join(job.tags or []),
                      job.budget or "", job.description or ""])
    ).lower()
    tail = "" if spec.get("open_end") else r"(?!\w)"
    return re.search(r"(?<!\w)(?:" + spec["pattern"] + r")" + tail, hay) is not None


# --------------------------------------------------------------------------- #
# Experience-level filter
# --------------------------------------------------------------------------- #
# Seniority is read from the TITLE and TAGS (titles nearly always carry it, and
# descriptions cause false positives like "work with senior stakeholders").
# "no_experience" additionally searches the description, where that phrasing lives.
EXPERIENCE_LEVELS: dict[str, dict] = {
    "no_experience": {"label": "No experience",
                      "pattern": r"no (?:prior )?experience(?: required| needed)?",
                      "deep": True},
    "internship": {"label": "Internship", "pattern": r"intern(ship)?s?|working student|werkstudent"},
    "entry": {"label": "Entry-level", "pattern": r"entry[\s_-]?level|junior|jr\.?|graduate|early[\s-]career"},
    "associate": {"label": "Associate", "pattern": r"associate"},
    "mid": {"label": "Mid-level", "pattern": r"mid[\s_-]?level|intermediate|mid[\s_-]?senior"},
    "senior": {"label": "Senior", "pattern": r"senior|sr\.?"},
    "principal": {"label": "Principal / Lead", "pattern": r"principal|staff engineer|lead|architect"},
    "manager": {"label": "Manager", "pattern": r"manager"},
    "director": {"label": "Director", "pattern": r"director|head of"},
    "executive": {"label": "Executive", "pattern": r"executive|vice president|vp|chief|cto|ceo|cfo|coo|c[\s-]?level"},
}
templates.env.globals["EXPERIENCE_LEVELS"] = EXPERIENCE_LEVELS


def _matches_experience(job: Job, key: str) -> bool:
    spec = EXPERIENCE_LEVELS.get(key)
    if not spec:
        return True
    parts = [job.title or "", " ".join(job.tags or [])]
    if spec.get("deep"):
        parts.append(job.description or "")
    hay = " ".join(parts).lower()
    return re.search(r"(?<!\w)(?:" + spec["pattern"] + r")(?!\w)", hay) is not None


# "N+ years ... experience" / "experience ... N years" within one clause. Used by
# the max-years slider: jobs demanding more than the user's years are hidden.
_YEARS_BEFORE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)[^.\n;]{0,40}?(?:experience|exp(?!\w))")
_YEARS_AFTER = re.compile(
    r"experience[^.\n;]{0,40}?(\d{1,2})\s*\+?\s*(?:years?|yrs?)(?!\w)")


def _required_years(job: Job) -> int | None:
    """Highest years-of-experience requirement stated in the listing, or None."""
    text = f"{job.title or ''}\n{job.description or ''}".lower()
    found = [int(m) for m in _YEARS_BEFORE.findall(text)]
    found += [int(m) for m in _YEARS_AFTER.findall(text)]
    found = [n for n in found if 0 < n <= 30]
    return max(found) if found else None


# Slider position meaning "no years filter" (rightmost = Any).
MAX_YEARS_ANY = 11


# A job counts as freelance if it's from Upwork or its title/tags/budget signal
# contract/hourly/part-time work. Matched in Python so JSON tags work on any DB.
# Distinctive substrings — safe to match anywhere in the text.
_FREELANCE_SUBSTR = ("/hr", "per hour", "1099", "c2c", "corp to corp")
# Word terms — matched on a start boundary so 'contract' hits 'contractor'/'contracts'
# but not 'subcontract', and (having dropped 'gig') we don't match 'gigabit'.
_FREELANCE_WORDS = (
    "freelance", "contract", "hourly", "part-time", "part time", "short-term",
    "short term", "temporary", "consultant", "consulting", "upwork",
)


def _is_freelance(job: Job) -> bool:
    if job.source == "upwork_rss":
        return True
    hay = " ".join(
        filter(None, [job.title or "", " ".join(job.tags or []), job.budget or ""])
    ).lower()
    if any(term in hay for term in _FREELANCE_SUBSTR):
        return True
    return any(re.search(r"(?<!\w)" + re.escape(t), hay) for t in _FREELANCE_WORDS)


def _search_tokens(q: str) -> list[str]:
    """Split a search query into tokens: "quoted phrases" stay whole, everything
    else splits on whitespace. Capped at 8 tokens to bound query cost."""
    tokens = [
        (m.group(1) or m.group(2)).strip()
        for m in re.finditer(r'"([^"]+)"|(\S+)', q or "")
    ]
    return [t for t in tokens if t][:8]


def _token_pattern(tok: str) -> str:
    """Whole-word pattern for a search token, so "java" doesn't match
    "javascript" and "laravel" only matches real Laravel mentions. Allows the
    common suffixes (plural s/es, js, trailing version digits, .js) so
    "api"→APIs and "python"→Python3 still hit. Multi-word (quoted) tokens
    treat their spaces as flexible separators ("full stack" ↔ "Full-Stack").
    Boundaries are skipped next to non-word edges so "c++"/".net" work."""
    parts = [re.escape(p) for p in tok.split()]
    body = r"[\s_\-/]+".join(parts)
    start = r"(?<!\w)" if tok[:1].isalnum() else ""
    end = r"(?:s|es|js|\d+|\.js)?(?!\w)" if tok[-1:].isalnum() else ""
    return start + body + end


# Field weights for search relevance: a title hit should outrank a mention
# buried in the description; tags are curated so they rank high too.
_SEARCH_FIELDS = (("title", 3.0), ("tags", 2.0), ("company", 1.5), ("description", 1.0))


def _search_relevance(job: Job, pats: list[re.Pattern]) -> float | None:
    """Relevance score when EVERY token matches somewhere (None otherwise).
    Tokens may hit different fields; per-token score is the sum of the weights
    of the fields it appears in."""
    fields = (
        (job.title or "", 3.0),
        (" ".join(job.tags or []), 2.0),
        (job.company or "", 1.5),
        (job.description or "", 1.0),
    )
    total = 0.0
    for pat in pats:
        s = sum(w for text, w in fields if text and pat.search(text))
        if not s:
            return None
        total += s
    return total


def _filtered_jobs(
    db: Session,
    *,
    sources: list[str] | None,
    status: str | None,
    min_score: int | None,
    q: str | None,
    freelance: bool = False,
    country: str | None = None,
    posted_within: int | None = None,
    etypes: list[str] | None = None,
    levels: list[str] | None = None,
    max_years: int | None = None,
) -> list[Job]:
    stmt = select(Job)

    if sources:
        stmt = stmt.where(Job.source.in_(sources))
    if status in (None, "", "active"):
        # Default feed hides ignored jobs.
        stmt = stmt.where(Job.status != "ignored")
    elif status != "all":
        stmt = stmt.where(Job.status == status)
    if min_score:
        stmt = stmt.where(Job.ai_score.is_not(None), Job.ai_score >= min_score)
    if posted_within and posted_within > 0:
        # "Posted within the last N days" — jobs without a posted_at fall back to
        # when we first fetched them (better than silently hiding them).
        cutoff = utcnow() - timedelta(days=posted_within)
        stmt = stmt.where(func.coalesce(Job.posted_at, Job.fetched_at) >= cutoff)

    jobs = list(db.execute(stmt).scalars())
    if freelance:
        jobs = [j for j in jobs if _is_freelance(j)]
    if country and country in COUNTRY_FILTERS:
        jobs = [j for j in jobs if _matches_country(j, country)]
    if etypes:
        wanted = [t for t in etypes if t in EMPLOYMENT_TYPES]
        if wanted:  # OR semantics: keep jobs matching ANY selected type
            jobs = [j for j in jobs if any(_matches_employment(j, t) for t in wanted)]
    if levels:
        wanted_lv = [l for l in levels if l in EXPERIENCE_LEVELS]
        if wanted_lv:  # OR semantics across selected levels
            jobs = [j for j in jobs if any(_matches_experience(j, l) for l in wanted_lv)]
    if max_years is not None and 0 <= max_years < MAX_YEARS_ANY:
        # Keep jobs requiring <= max_years, and jobs stating no requirement.
        jobs = [j for j in jobs
                if (req := _required_years(j)) is None or req <= max_years]

    # Search: whole-word tokens over title + tags + company + description, with
    # a relevance score so title/tag hits rank above description mentions.
    relevance: dict[int, float] = {}
    if q:
        tokens = _search_tokens(q)
        if tokens:
            pats = [re.compile(_token_pattern(t), re.I) for t in tokens]
            kept = []
            for j in jobs:
                score = _search_relevance(j, pats)
                if score is not None:
                    relevance[j.id] = score
                    kept.append(j)
            jobs = kept

    # Sort: ai_score desc NULLS LAST, then posted_at desc, then fetched_at desc.
    jobs.sort(
        key=lambda j: (
            j.ai_score if j.ai_score is not None else -1,
            j.posted_at or j.fetched_at or datetime.min,
            j.fetched_at or datetime.min,
        ),
        reverse=True,
    )
    if relevance:
        # Stable sort: relevance is primary, the ai-score/date order above breaks ties.
        jobs.sort(key=lambda j: relevance.get(j.id, 0.0), reverse=True)
    return jobs


PAGE_SIZE = 25


def _paginate(jobs: list[Job], page: int) -> tuple[list[Job], dict]:
    """Return (page_slice, {page, pages, total}) — page clamped to a valid range."""
    total = len(jobs)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    start = (page - 1) * PAGE_SIZE
    return jobs[start : start + PAGE_SIZE], {"page": page, "pages": pages, "total": total}


def _jobs_fragment(
    request: Request, db: Session, jobs: list[Job], page: int = 1
) -> HTMLResponse:
    page_jobs, pg = _paginate(jobs, page)
    return templates.TemplateResponse(
        request,
        "_jobs.html",
        {"jobs": page_jobs, "page": pg["page"], "pages": pg["pages"], "total": pg["total"]},
    )


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
def _render_feed(request: Request, db: Session, *, freelance: bool):
    settings = all_settings(db)
    enabled = settings.get("sources_enabled", {})
    # Show a checkbox for every source that's enabled OR actually has jobs, so
    # 'manual' (and any disabled-but-populated source) isn't filtered away when the
    # sidebar submits its checked sources during HTMX live-filtering.
    present = {row[0] for row in db.execute(select(Job.source).distinct()).all()}
    sources = [s for s in SOURCE_LABELS if enabled.get(s) or s in present]
    sources += [s for s in present if s not in SOURCE_LABELS]
    sources = sources or list(SOURCE_LABELS.keys())

    jobs = _filtered_jobs(
        db, sources=None, status=None, min_score=None, q=None, freelance=freelance
    )
    page_jobs, pg = _paginate(jobs, 1)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "jobs": page_jobs,
            "page": pg["page"],
            "pages": pg["pages"],
            "total": pg["total"],
            "sources": sources,
            "settings": settings,
            "freelance": freelance,
        },
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    return _render_feed(request, db, freelance=False)


@app.get("/freelancing", response_class=HTMLResponse)
def freelancing(request: Request, db: Session = Depends(get_db)):
    return _render_feed(request, db, freelance=True)


@app.get("/jobs-partial", response_class=HTMLResponse)
def jobs_partial(request: Request, db: Session = Depends(get_db)):
    qp = request.query_params
    sources = qp.getlist("source")
    status = qp.get("status")
    q = qp.get("q")
    min_score_raw = qp.get("min_score")
    min_score = int(min_score_raw) if (min_score_raw and min_score_raw.isdigit()) else None
    freelance = qp.get("freelance") == "1"
    country = qp.get("country") or None
    pw_raw = qp.get("posted_within")
    posted_within = int(pw_raw) if (pw_raw and pw_raw.isdigit()) else None
    etypes = qp.getlist("etype")
    levels = qp.getlist("explevel")
    my_raw = qp.get("max_years")
    max_years = int(my_raw) if (my_raw and my_raw.isdigit()) else None
    page_raw = qp.get("page")
    page = int(page_raw) if (page_raw and page_raw.isdigit()) else 1

    # Request came from the filter form but zero sources are checked -> show none
    # (without the marker, an empty sources list means "no source filter").
    if qp.get("flt") == "1" and not sources:
        return _jobs_fragment(request, db, [], 1)

    jobs = _filtered_jobs(
        db, sources=sources, status=status, min_score=min_score, q=q,
        freelance=freelance, country=country, posted_within=posted_within,
        etypes=etypes, levels=levels, max_years=max_years,
    )
    return _jobs_fragment(request, db, jobs, page)


@app.get("/jobs/{job_id}/drawer", response_class=HTMLResponse)
def job_drawer(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        return HTMLResponse("<div class='drawer-empty'>Job not found.</div>", status_code=404)
    return templates.TemplateResponse(
        request, "_drawer.html", {"job": job}
    )


@app.post("/jobs/{job_id}/rescore", response_class=HTMLResponse)
def rescore(job_id: int, request: Request, db: Session = Depends(get_db)):
    """Re-run AI scoring + proposal drafting for one job, then re-render the drawer."""
    job = db.get(Job, job_id)
    if not job:
        return HTMLResponse("<div class='drawer-empty'>Job not found.</div>", status_code=404)

    error = None
    if not ai.api_key_present():
        error = "OPENAI_API_KEY is not set — add it to your .env to enable AI scoring."
    else:
        profile = get_setting(db, "profile_text", "") or ""
        try:
            result = ai.score_and_draft(job, profile)
            job.ai_score = result["score"]
            job.ai_reason = result["reason"]
            # Re-score is an explicit request to regenerate the proposal.
            job.ai_proposal = result["proposal"]
            job.ai_attempts = 0  # explicit retry clears the failure counter
            db.commit()
            # High-fit alerts are sent by the batch pass (notify_new_matches),
            # not inline, so the request thread never blocks on Telegram.
        except ai.AIConfigError as exc:
            error = str(exc)
        except ai.AIError as exc:
            # Keep provider/internal detail in the logs, not the UI.
            log.warning("re-score failed for job %s: %s", job_id, exc)
            error = "Scoring failed — check your OpenAI key/quota and the server logs."
        except Exception:  # noqa: BLE001
            log.exception("re-score failed for job %s", job_id)
            error = "Scoring failed — check the server logs."

    resp = templates.TemplateResponse(
        request, "_drawer.html", {"job": job, "rescore_error": error}
    )
    if not error:
        resp.headers["HX-Trigger"] = _toast(f"Re-scored: {job.ai_score}/10")
    return resp


# --------------------------------------------------------------------------- #
# Manual add (Phase 3)
# --------------------------------------------------------------------------- #
@app.get("/jobs/new", response_class=HTMLResponse)
def add_job_form(request: Request):
    return templates.TemplateResponse(request, "_add_form.html", {})


@app.post("/jobs/add", response_class=HTMLResponse)
def add_job(
    request: Request,
    url: str = Form(""),
    title: str = Form(""),
    company: str = Form(""),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    url = (url or "").strip()
    title = (title or "").strip()
    if not url or not title:
        resp = _jobs_fragment(
            request, db, _filtered_jobs(db, sources=None, status=None, min_score=None, q=None)
        )
        resp.headers["HX-Trigger"] = _toast("URL and title are required.")
        return resp
    if not url.lower().startswith(("http://", "https://")):
        resp = _jobs_fragment(
            request, db, _filtered_jobs(db, sources=None, status=None, min_score=None, q=None)
        )
        resp.headers["HX-Trigger"] = _toast("URL must start with http:// or https://")
        return resp

    dh = dedupe_hash(url)
    existing = db.execute(select(Job.id).where(Job.dedupe_hash == dh)).first()
    if existing:
        resp = _jobs_fragment(
            request, db, _filtered_jobs(db, sources=None, status=None, min_score=None, q=None)
        )
        resp.headers["HX-Trigger"] = _toast("That job is already in your feed.")
        return resp

    from .fetchers.util import extract_emails

    clean_desc = (description or "").strip()
    job = Job(
        source="manual",
        external_id=None,
        url=url,
        title=title,
        company=(company or "").strip() or None,
        description=clean_desc,
        tags=[],
        contact_emails=extract_emails(f"{title}\n{clean_desc}"),
        status="new",
        notes="",
        dedupe_hash=dh,
    )
    db.add(job)
    db.commit()

    # Score immediately if possible; otherwise the next fetch cycle will pick it up.
    msg = "Added job."
    if ai.api_key_present():
        try:
            result = ai.score_and_draft(job, get_setting(db, "profile_text", "") or "")
            job.ai_score = result["score"]
            job.ai_reason = result["reason"]
            job.ai_proposal = result["proposal"]
            db.commit()
            msg = f"Added and scored: {job.ai_score}/10."
        except Exception:  # noqa: BLE001 - leave unscored for the cycle to retry
            log.exception("inline scoring of manual job %s failed", job.id)
            msg = "Added job (scoring will run on the next cycle)."

    jobs = _filtered_jobs(db, sources=None, status=None, min_score=None, q=None)
    resp = _jobs_fragment(request, db, jobs)
    resp.headers["HX-Trigger"] = json.dumps(
        {"toast": {"message": msg}, "closeDrawer": True}
    )
    return resp


# --------------------------------------------------------------------------- #
# Job mutations
# --------------------------------------------------------------------------- #
def _job_card_response(request: Request, job: Job) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_job_card.html", {"job": job}
    )


@app.post("/jobs/{job_id}/shortlist", response_class=HTMLResponse)
def shortlist(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job:
        job.status = "shortlisted"
        db.commit()
    return _job_card_response(request, job)


@app.post("/jobs/{job_id}/ignore", response_class=HTMLResponse)
def ignore(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job:
        job.status = "ignored"
        db.commit()
    # Removing from the feed: return empty so HTMX swaps the card away, and ask the
    # client to re-render the list so the "N jobs · page X of Y" header stays true.
    resp = HTMLResponse("")
    resp.headers["HX-Trigger"] = json.dumps({"refreshFeed": True})
    return resp


@app.post("/jobs/{job_id}/apply", response_class=HTMLResponse)
def apply(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        return HTMLResponse("Job not found", status_code=404)
    job.status = "applied"
    job.applied_at = utcnow()
    db.commit()
    # Response carries the proposal + url; a tiny JS snippet copies to clipboard
    # and opens the job in a new tab. NEVER auto-submits (assisted apply only).
    return templates.TemplateResponse(
        request, "_apply.html", {"job": job}
    )


@app.post("/jobs/{job_id}/proposal", response_class=HTMLResponse)
def save_proposal(
    job_id: int, proposal: str = Form(""), db: Session = Depends(get_db)
):
    job = db.get(Job, job_id)
    if job:
        job.ai_proposal = proposal
        db.commit()
    return HTMLResponse("<span class='save-hint'>Saved</span>")


@app.post("/jobs/{job_id}/notes", response_class=HTMLResponse)
def save_notes(job_id: int, notes: str = Form(""), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job:
        job.notes = notes
        db.commit()
    return HTMLResponse("<span class='save-hint'>Saved</span>")


@app.post("/jobs/{job_id}/move", response_class=HTMLResponse)
def move(
    job_id: int,
    request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if job and status in STATUSES:
        job.status = status
        if status == "applied" and job.applied_at is None:
            job.applied_at = utcnow()
        db.commit()
    # Re-render the whole board so the card lands in the right column.
    return _tracker_board(request, db)


# --------------------------------------------------------------------------- #
# Fetch now
# --------------------------------------------------------------------------- #
@app.post("/fetch-now", response_class=HTMLResponse)
def fetch_now():
    # Run the cycle (fetch + AI scoring) off the request thread so the HTTP
    # request can't hang for minutes while the model is slow. The pipeline lock makes
    # this a no-op if a cycle is already running. The button uses hx-swap="none",
    # so the user's current filtered/paginated view is left untouched (no mixed pages).
    if _serverless():
        # No background threads on serverless (the runtime freezes after the
        # response, killing them mid-cycle) — run synchronously instead. The
        # pipeline commits per source, so even a platform timeout keeps progress.
        summary = run_fetch_cycle()
        inserted = summary.get("inserted", 0) if isinstance(summary, dict) else 0
        resp = HTMLResponse("")
        resp.headers["HX-Trigger"] = _toast(f"Fetched — {inserted} new job(s). Refresh to see them.")
        return resp
    threading.Thread(target=run_fetch_cycle, daemon=True).start()
    resp = HTMLResponse("")
    resp.headers["HX-Trigger"] = _toast(
        "Fetching in the background — refresh in a moment to see new jobs."
    )
    return resp


@app.get("/cron/fetch")
def cron_fetch(request: Request):
    """Scheduled-fetch endpoint for serverless deployments (Vercel Cron).

    Protected by CRON_SECRET (Vercel sends it as "Authorization: Bearer <secret>"
    automatically when the env var is set). Refuses to run unconfigured so the
    login gate can safely exempt this path.
    """
    secret = (os.getenv("CRON_SECRET") or "").strip()
    if not secret:
        return Response("CRON_SECRET is not configured.", status_code=403)
    auth = request.headers.get("authorization", "")
    if not hmac.compare_digest(auth, f"Bearer {secret}"):
        return Response("Unauthorized.", status_code=401)
    summary = run_fetch_cycle()
    return {
        "ok": True,
        "fetched": summary.get("fetched"),
        "inserted": summary.get("inserted"),
        "skipped": summary.get("skipped"),
        "errors": summary.get("errors"),
    }


def _toast(message: str) -> str:
    return json.dumps({"toast": {"message": message}})


# --------------------------------------------------------------------------- #
# Tracker (kanban)
# --------------------------------------------------------------------------- #
def _tracker_board(request: Request, db: Session) -> HTMLResponse:
    columns: dict[str, list[Job]] = {c: [] for c in TRACKER_COLUMNS}
    stmt = select(Job).where(Job.status.in_(TRACKER_COLUMNS))
    for job in db.execute(stmt).scalars():
        columns[job.status].append(job)
    for jobs in columns.values():
        jobs.sort(
            key=lambda j: (j.applied_at or j.fetched_at or datetime.min),
            reverse=True,
        )
    return templates.TemplateResponse(
        request,
        "_tracker_board.html",
        {"columns": columns},
    )


@app.get("/tracker", response_class=HTMLResponse)
def tracker(request: Request, db: Session = Depends(get_db)):
    columns: dict[str, list[Job]] = {c: [] for c in TRACKER_COLUMNS}
    stmt = select(Job).where(Job.status.in_(TRACKER_COLUMNS))
    for job in db.execute(stmt).scalars():
        columns[job.status].append(job)
    for jobs in columns.values():
        jobs.sort(
            key=lambda j: (j.applied_at or j.fetched_at or datetime.min),
            reverse=True,
        )
    return templates.TemplateResponse(
        request, "tracker.html", {"columns": columns}
    )


# --------------------------------------------------------------------------- #
# Stats (Phase 4)
# --------------------------------------------------------------------------- #
# Dark-theme categorical palette (validated for the app's panel surface, in
# registry order so each source keeps a fixed hue).
SOURCE_COLORS = {
    "remoteok": "#3987e5",
    "remotive": "#199e70",
    "wwr": "#c98500",
    "hn": "#008300",
    "reddit": "#9085e9",
    "upwork_rss": "#e66767",
    "linkedin_email": "#d55181",
    "manual": "#d95926",
    "jobicy": "#14b8a6",
    "arbeitnow": "#eab308",
    "themuse": "#a855f7",
    "indeed": "#2557a7",
    "mustakbil": "#0e9f6e",
    "rozee": "#84cc16",
    "bayt": "#f472b6",
    "indeed_pk": "#60a5fa",
    "linkedin_web": "#0a66c2",
}
_APPLIED_OR_BEYOND = ("applied", "replied", "interview", "won", "lost")
_REPLIED_STATUSES = ("replied", "interview", "won")
_INTERVIEW_STATUSES = ("interview", "won")


def _iso(dt: datetime | None) -> str:
    return dt.isoformat(timespec="seconds") if dt else ""


def _csv_safe(value) -> str:
    """Neutralize spreadsheet formula injection (leading = + - @ / tab / CR)."""
    s = "" if value is None else str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


@app.get("/stats", response_class=HTMLResponse)
def stats(request: Request, db: Session = Depends(get_db)):
    from collections import defaultdict

    now = utcnow()
    today = now.date()

    # --- Jobs per source per day, last 14 days ---
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    day_keys = [d.isoformat() for d in days]
    day_set = set(day_keys)
    day_labels = [d.strftime("%b %d") for d in days]
    start = datetime.combine(days[0], datetime.min.time())

    per = defaultdict(lambda: defaultdict(int))
    for source, fetched in db.execute(
        select(Job.source, Job.fetched_at).where(Job.fetched_at >= start)
    ).all():
        if not fetched:
            continue
        key = fetched.date().isoformat()
        if key in day_set:
            per[source][key] += 1

    ordered_sources = [s for s in SOURCE_COLORS if s in per] + [
        s for s in per if s not in SOURCE_COLORS
    ]
    source_datasets = [
        {
            "label": SOURCE_LABELS.get(s, s),
            "data": [per[s].get(k, 0) for k in day_keys],
            "color": SOURCE_COLORS.get(s, "#8fa3bd"),
        }
        for s in ordered_sources
    ]

    # --- Applications per week, last 8 weeks (weeks start Monday) ---
    this_monday = today - timedelta(days=today.weekday())
    weeks = [this_monday - timedelta(weeks=i) for i in range(7, -1, -1)]
    weeks_index = {w: i for i, w in enumerate(weeks)}
    week_labels = [w.strftime("%b %d") for w in weeks]
    week_counts = [0] * len(weeks)
    for (applied,) in db.execute(
        select(Job.applied_at).where(Job.applied_at.is_not(None))
    ).all():
        if not applied:
            continue
        wk = applied.date() - timedelta(days=applied.date().weekday())
        if wk in weeks_index:
            week_counts[weeks_index[wk]] += 1

    # --- Funnel rates ---
    def _count(*where) -> int:
        return int(db.execute(select(func.count()).select_from(Job).where(*where)).scalar() or 0)

    threshold = int(get_setting(db, "alert_threshold", 8) or 8)
    # Denominator must be a superset of the numerators (which count by status), so
    # dragging a card straight to replied/interview can't push the rate over 100%.
    applied_total = _count(
        or_(Job.applied_at.is_not(None), Job.status.in_(_APPLIED_OR_BEYOND))
    )
    replied = _count(Job.status.in_(_REPLIED_STATUSES))
    interviews = _count(Job.status.in_(_INTERVIEW_STATUSES))
    total_jobs = _count()
    scored_jobs = _count(Job.ai_score.is_not(None))
    high_fit = _count(Job.ai_score.is_not(None), Job.ai_score >= threshold)

    tiles = {
        "total_jobs": total_jobs,
        "scored_jobs": scored_jobs,
        "high_fit": high_fit,
        "applied": applied_total,
        "reply_rate": round(100 * replied / applied_total) if applied_total else 0,
        "interview_rate": round(100 * interviews / applied_total) if applied_total else 0,
        "threshold": threshold,
    }

    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "day_labels": day_labels,
            "source_datasets": source_datasets,
            "week_labels": week_labels,
            "week_counts": week_counts,
            "tiles": tiles,
        },
    )


@app.get("/export.csv")
def export_csv(db: Session = Depends(get_db)):
    rows = (
        db.execute(select(Job).where(Job.status.in_(TRACKER_COLUMNS)))
        .scalars()
        .all()
    )
    rows.sort(key=lambda j: (j.applied_at or j.fetched_at or datetime.min), reverse=True)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["id", "source", "title", "company", "url", "ai_score", "status",
         "applied_at", "posted_at", "fetched_at", "ai_reason", "notes"]
    )
    for j in rows:
        writer.writerow(
            [j.id, _csv_safe(j.source), _csv_safe(j.title), _csv_safe(j.company or ""),
             _csv_safe(j.url), (j.ai_score if j.ai_score is not None else ""),
             _csv_safe(j.status), _iso(j.applied_at), _iso(j.posted_at),
             _iso(j.fetched_at), _csv_safe(j.ai_reason or ""), _csv_safe(j.notes or "")]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=jobradar_tracker.csv"},
    )


# --------------------------------------------------------------------------- #
# Résumé module (Phase 5)
# --------------------------------------------------------------------------- #
def _resume_matched_jobs(db: Session, skills: list[str]) -> list[Job]:
    """Active jobs that match >=1 résumé skill, ranked by match count then score.

    Each returned job carries a transient `match_hits` list of matched skills.
    """
    if not skills:
        return []
    jobs = _filtered_jobs(db, sources=None, status=None, min_score=None, q=None)
    scored: list[tuple[int, int, Job]] = []
    for job in jobs:
        haystack = f"{job.title}\n{job.description}\n{' '.join(job.tags or [])}"
        hits = resume.matched_skills(haystack, skills)
        if hits:
            job.match_hits = hits
            scored.append((len(hits), job.ai_score if job.ai_score is not None else -1, job))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [job for _, _, job in scored]


@app.get("/resume", response_class=HTMLResponse)
def resume_page(request: Request, db: Session = Depends(get_db)):
    settings = all_settings(db)
    skills = settings.get("resume_skills") or []
    matched = _resume_matched_jobs(db, skills)
    page_raw = request.query_params.get("page")
    page = int(page_raw) if (page_raw and page_raw.isdigit()) else 1
    page_jobs, pg = _paginate(matched, page)
    return templates.TemplateResponse(
        request,
        "resume.html",
        {
            "settings": settings,
            "jobs": page_jobs,
            "page": pg["page"],
            "pages": pg["pages"],
            "total": pg["total"],
            "has_resume": bool(settings.get("resume_filename")),
            "error": request.query_params.get("error"),
        },
    )


@app.post("/resume/upload")
def resume_upload(
    request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    # Sync def on purpose: FastAPI runs it in a threadpool, so the blocking file
    # parsing and OpenAI call never stall the async event loop.
    from urllib.parse import quote

    def _err(msg: str):
        return RedirectResponse(url=f"/resume?error={quote(msg)}", status_code=303)

    # Bounded read: never pull more than the cap (+1) into memory.
    data = file.file.read(resume.MAX_RESUME_BYTES + 1)
    if not data:
        return _err("No file received.")
    if len(data) > resume.MAX_RESUME_BYTES:
        return _err("File too large (max 5 MB).")

    try:
        text = resume.parse_resume(file.filename or "resume", data)
    except resume.ResumeError as exc:
        return _err(str(exc))
    except Exception:  # noqa: BLE001
        log.exception("résumé parse failed")
        return _err("Could not read that file. Try a PDF, .docx, or .txt.")

    # Prefer AI extraction; fall back to the keyword vocabulary without a key.
    profile = None
    if ai.api_key_present():
        try:
            profile = ai.extract_resume_profile(text)
        except Exception:  # noqa: BLE001
            log.exception("AI résumé extraction failed; using heuristic")
    if not profile or not profile.get("skills"):
        heuristic = resume.heuristic_profile(text)
        if not profile:
            profile = heuristic
        elif not profile.get("skills"):
            profile["skills"] = heuristic["skills"]

    set_setting(db, "resume_filename", file.filename or "resume")
    set_setting(db, "resume_skills", profile.get("skills") or [])
    set_setting(db, "resume_titles", profile.get("titles") or [])
    set_setting(db, "resume_seniority", profile.get("seniority") or "")
    set_setting(db, "resume_summary", profile.get("summary") or "")
    set_setting(db, "resume_uploaded_at", utcnow().isoformat(timespec="seconds"))
    return RedirectResponse(url="/resume", status_code=303)


@app.post("/resume/clear")
def resume_clear(db: Session = Depends(get_db)):
    for key in (
        "resume_filename", "resume_skills", "resume_titles",
        "resume_seniority", "resume_summary", "resume_uploaded_at",
    ):
        set_setting(db, key, [] if key in ("resume_skills", "resume_titles") else "")
    return RedirectResponse(url="/resume", status_code=303)


@app.post("/resume/use-profile")
def resume_use_profile(db: Session = Depends(get_db)):
    """Set the AI scoring profile from the résumé so future scores are tailored."""
    summary = get_setting(db, "resume_summary", "") or ""
    skills = get_setting(db, "resume_skills", []) or []
    titles = get_setting(db, "resume_titles", []) or []
    parts = []
    if summary:
        parts.append(summary)
    if titles:
        parts.append("Roles: " + ", ".join(titles))
    if skills:
        parts.append("Skills: " + ", ".join(skills))
    profile_text = "\n".join(parts).strip()
    if profile_text:
        set_setting(db, "profile_text", profile_text)
    return RedirectResponse(url="/settings?saved=1", status_code=303)


# --------------------------------------------------------------------------- #
# Settings (Phase 2)
# --------------------------------------------------------------------------- #
def _parse_list(raw: str) -> list[str]:
    """Split a textarea (newline- and/or comma-separated) into a clean list."""
    parts: list[str] = []
    seen = set()
    for chunk in (raw or "").replace("\r", "\n").replace(",", "\n").split("\n"):
        item = chunk.strip()
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            parts.append(item)
    return parts


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    settings = all_settings(db)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"settings": settings, "saved": request.query_params.get("saved") == "1"},
    )


@app.post("/settings")
async def settings_save(request: Request, db: Session = Depends(get_db)):
    form = await request.form()

    set_setting(db, "keywords", _parse_list(form.get("keywords", "")))
    set_setting(db, "blocked_keywords", _parse_list(form.get("blocked_keywords", "")))

    raw_min = (form.get("min_score") or "").strip()
    try:
        min_score = max(0, min(10, int(raw_min)))
    except (TypeError, ValueError):
        min_score = 6
    set_setting(db, "min_score", min_score)

    # Checkboxes only appear in the form when checked.
    checked = set(form.getlist("sources_enabled"))
    set_setting(
        db,
        "sources_enabled",
        {s: (s in checked) for s in FETCH_SOURCES},
    )

    set_setting(db, "upwork_rss_urls", _parse_list(form.get("upwork_rss_urls", "")))
    set_setting(db, "profile_text", (form.get("profile_text") or "").strip())

    raw_alert = (form.get("alert_threshold") or "").strip()
    try:
        alert = max(0, min(10, int(raw_alert)))
    except (TypeError, ValueError):
        alert = 8
    set_setting(db, "alert_threshold", alert)

    return RedirectResponse(url="/settings?saved=1", status_code=303)


@app.post("/notify/test", response_class=HTMLResponse)
def notify_test():
    """Send a test Telegram message so the user can verify their setup."""
    if not notify.telegram_enabled():
        return HTMLResponse(
            "<span class='text-amber-300'>Set TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID in .env first.</span>"
        )
    ok = notify.send_message("✅ JobRadar test message — notifications are working.")
    return HTMLResponse(
        "<span class='text-emerald-400'>Sent — check Telegram.</span>"
        if ok
        else "<span class='text-red-400'>Send failed — check the token/chat id and logs.</span>"
    )
