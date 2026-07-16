"""Fetch pipeline: run enabled fetchers, normalize, dedupe, keyword-filter, insert.

Hard rule: every fetcher is isolated with try/except + logging; one failing
source must never kill the cycle.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading

from sqlalchemy import select

from . import ai, notify
from .db import SessionLocal
from .fetchers import FETCHERS
from .fetchers.util import extract_emails
from .models import Job
from .settings import effective_sources_enabled, get_setting

log = logging.getLogger("jobradar.pipeline")

# Serializes fetch cycles across the startup thread, the scheduler, and /fetch-now
# so they never double-fetch, double-score, or write concurrently.
_cycle_lock = threading.Lock()

# Sources whose upstream request is already scoped to developer jobs (their
# listings expose only a title, so the keyword gate would wrongly drop e.g.
# "Software Engineer"). Blocked keywords still apply.
PRE_FILTERED_SOURCES = {"rozee"}


def _log_source_failure(source: str, exc: Exception) -> None:
    """One-line warning for expected 4xx blocks; full traceback for the rest.

    A permanently-blocked site (Rozee/Bayt on datacenter IPs) would otherwise dump
    a multi-line traceback every 10-minute cycle and drown out real errors.
    """
    import httpx

    if isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500:
        log.warning(
            "fetcher %s blocked: HTTP %s (expected on some networks; toggle it off "
            "in Settings to silence)", source, exc.response.status_code,
        )
    else:
        log.warning("fetcher %s failed: %s", source, exc, exc_info=True)


def _empty_summary(**extra) -> dict:
    base = {"fetched": 0, "inserted": 0, "skipped": 0, "errors": {}, "per_source": {}}
    base.update(extra)
    return base


def dedupe_hash(url: str) -> str:
    """sha256 of the normalized (stripped, lowercased) url."""
    normalized = (url or "").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# Common suffixes a keyword may carry: plural 's', a version number (python3),
# 'js' (reactjs/nodejs), or '.js'. This keeps 'ai' from matching 'email'/'training'
# while still matching 'APIs', 'Python3', 'ReactJS', 'integrations', 'LLMs', etc.
_KEYWORD_SUFFIX = r"(?:s|js|\d+|\.js)?"


def _matches_whole_word(haystack: str, term: str) -> bool:
    """Boundary match at the start; allow a common tech suffix at the end.

    Uses lookarounds (not \\b) so terms with punctuation like 'next.js' match cleanly.
    """
    pattern = r"(?<!\w)" + re.escape(term) + _KEYWORD_SUFFIX + r"(?!\w)"
    return re.search(pattern, haystack) is not None


def _passes_keyword_filter(text: str, keywords: list[str], blocked: list[str]) -> bool:
    """title+description must contain >=1 keyword and 0 blocked keywords.

    Blocked keywords are matched as substrings (they're phrases like 'wordpress
    only'); keywords are matched on word boundaries for accuracy.
    """
    haystack = text.lower()
    if any(b.lower() in haystack for b in blocked if b):
        return False
    if not keywords:
        return True
    return any(_matches_whole_word(haystack, k.strip().lower()) for k in keywords if k and k.strip())


def run_fetch_cycle() -> dict:
    """Run one full fetch cycle. Returns a small summary dict for logging/UI.

    Guarded by a process-wide lock: if a cycle is already running (scheduler,
    startup, or a prior /fetch-now), this call is a no-op that returns busy=True.
    """
    if not _cycle_lock.acquire(blocking=False):
        log.info("fetch cycle already running; skipping this trigger")
        return _empty_summary(busy=True)
    try:
        return _run_fetch_cycle_locked()
    finally:
        _cycle_lock.release()


def _run_fetch_cycle_locked() -> dict:
    db = SessionLocal()
    summary: dict = {
        "fetched": 0,
        "inserted": 0,
        "skipped": 0,
        "errors": {},
        "per_source": {},
    }
    # Hashes already present in the DB, plus any inserted this cycle. Guards
    # against duplicate urls appearing twice within one batch.
    seen: set[str] = {
        row[0] for row in db.execute(select(Job.dedupe_hash)).all()
    }
    try:
        sources_enabled = effective_sources_enabled(db)
        keywords = get_setting(db, "keywords", []) or []
        blocked = get_setting(db, "blocked_keywords", []) or []

        for source, fetch in FETCHERS.items():
            # Only run sources explicitly enabled (default off if unspecified).
            if not sources_enabled.get(source, False):
                continue

            # Sources whose upstream query already scopes to developer jobs; the
            # keyword gate is skipped for them because they expose only a title
            # (blocked keywords still apply).
            src_keywords = [] if source in PRE_FILTERED_SOURCES else keywords

            # The WHOLE per-source unit (fetch + normalize + insert + commit) is
            # isolated: one bad source/row must never kill the rest of the cycle.
            try:
                raw_jobs = fetch()
                inserted_here = 0
                for raw in raw_jobs:
                    summary["fetched"] += 1
                    if _insert_one(db, raw, src_keywords, blocked, seen, summary):
                        inserted_here += 1
                # Commit per source so one source's rows persist even if a later
                # source misbehaves.
                db.commit()
            except Exception as exc:  # noqa: BLE001 - isolate each source
                db.rollback()
                _log_source_failure(source, exc)
                summary["errors"][source] = str(exc)
                continue

            summary["per_source"][source] = {
                "fetched": len(raw_jobs),
                "inserted": inserted_here,
            }
            log.info(
                "source %s: fetched=%d inserted=%d", source, len(raw_jobs), inserted_here
            )
    finally:
        db.close()

    log.info(
        "fetch cycle done: fetched=%d inserted=%d skipped=%d errors=%s",
        summary["fetched"], summary["inserted"], summary["skipped"], summary["errors"],
    )

    # Phase 2: after fetching, AI-score up to 10 unscored jobs (oldest first).
    # Wrapped so a scoring failure never breaks the fetch cycle.
    try:
        summary["scoring"] = ai.score_new_jobs(limit=10)
    except Exception:  # noqa: BLE001
        log.exception("AI scoring phase failed")
        summary["scoring"] = {"scored": 0, "failed": 0, "error": True}

    # Phase 4: send Telegram alerts for high-fit jobs (isolated like everything else).
    try:
        summary["notify"] = notify.notify_new_matches()
    except Exception:  # noqa: BLE001
        log.exception("notification phase failed")
        summary["notify"] = {"sent": 0, "error": True}

    return summary


def _insert_one(db, raw: dict, keywords, blocked, seen: set[str], summary) -> bool:
    """Normalize one raw job and insert it if new + passes filters.

    Returns True if a row was inserted.
    """
    url = (raw.get("url") or "").strip()
    if not url:
        summary["skipped"] += 1
        return False

    title = (raw.get("title") or "").strip()
    description = raw.get("description") or ""

    if not _passes_keyword_filter(f"{title}\n{description}", keywords, blocked):
        summary["skipped"] += 1
        return False

    dh = dedupe_hash(url)
    if dh in seen:
        summary["skipped"] += 1
        return False

    ext = raw.get("external_id")
    job = Job(
        source=raw.get("source", "manual"),
        # Column is String(255); rozee/bayt use full URLs as ids, which can exceed
        # it and would abort the commit under MySQL strict mode.
        external_id=(str(ext)[:255] if ext else None),
        url=url[:1024],
        title=title[:512],
        company=raw.get("company"),
        description=description,
        tags=raw.get("tags") or [],
        budget=raw.get("budget"),
        location=raw.get("location"),
        contact_emails=extract_emails(f"{title}\n{description}"),
        posted_at=raw.get("posted_at"),
        status="new",
        notes="",
        dedupe_hash=dh,
    )
    db.add(job)
    seen.add(dh)
    summary["inserted"] += 1
    return True
