"""Telegram notifications (Phase 4).

After scoring, any job with ai_score >= the alert threshold (Settings, default 8)
gets a Telegram message: title, company, score, reason, url. Sending is a simple
httpx POST to the Bot API — no heavy libraries. Credentials come only from .env:
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.

Each job is notified at most once (Job.notified_at guards against duplicates), and
every failure is caught so notifications never break scoring or the fetch cycle.
"""
from __future__ import annotations

import logging
import os

import httpx

from .models import utcnow

log = logging.getLogger("jobradar.notify")

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
DEFAULT_THRESHOLD = 8
# Cap per batch so first-time setup with a big backlog doesn't flood the chat.
BATCH_LIMIT = 20
# Telegram rejects messages over 4096 chars; keep well under to avoid a hard 400.
MAX_MESSAGE_CHARS = 3900
# Stop a batch after this many consecutive send failures (likely a global outage).
MAX_CONSECUTIVE_FAILURES = 3


def telegram_enabled() -> bool:
    return bool(
        (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        and (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    )


def alert_threshold(db) -> int:
    from .settings import get_setting

    try:
        return int(get_setting(db, "alert_threshold", DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD


def send_message(text: str) -> bool:
    """POST a message to Telegram. Returns True on success, never raises."""
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not (token and chat_id):
        return False
    try:
        resp = httpx.post(
            API_URL.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": False,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPStatusError as exc:
        # NOTE: never log the exception object/URL — it contains the bot token.
        log.warning("telegram send failed: HTTP %s", exc.response.status_code)
        return False
    except Exception as exc:  # noqa: BLE001 - best-effort; log type only, not the URL
        log.warning("telegram send failed: %s", type(exc).__name__)
        return False


def format_job(job) -> str:
    score = job.ai_score if job.ai_score is not None else "?"
    lines = [f"⭐ {score}/10 — {job.title}"]
    if job.company:
        lines.append(job.company)
    if job.ai_reason:
        lines.append(job.ai_reason)
    lines.append(job.url)
    text = "\n".join(lines)
    # Guard against Telegram's 4096-char hard limit (a long ai_reason would 400).
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[: MAX_MESSAGE_CHARS - 1] + "…"
    return text


def maybe_notify(db, job) -> bool:
    """Notify for one job if enabled, scored >= threshold, and not yet notified."""
    if not telegram_enabled():
        return False
    if job.ai_score is None or job.notified_at is not None:
        return False
    if job.ai_score < alert_threshold(db):
        return False
    if send_message(format_job(job)):
        job.notified_at = utcnow()
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        return True
    return False


def notify_new_matches(limit: int = BATCH_LIMIT) -> dict:
    """Batch pass: notify scored, high-fit, not-yet-notified jobs. Own DB session.

    Also picks up jobs scored before Telegram was configured, and retries any that
    failed to send previously (notified_at stays NULL on failure).
    """
    if not telegram_enabled():
        return {"sent": 0, "skipped_disabled": True}

    from sqlalchemy import select

    from .db import SessionLocal
    from .models import Job

    db = SessionLocal()
    sent = 0
    try:
        threshold = alert_threshold(db)
        rows = (
            db.execute(
                select(Job)
                .where(Job.ai_score.is_not(None))
                .where(Job.ai_score >= threshold)
                .where(Job.notified_at.is_(None))
                .where(Job.status != "ignored")
                .order_by(Job.ai_score.desc(), Job.fetched_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        failures = 0
        for job in rows:
            if send_message(format_job(job)):
                job.notified_at = utcnow()
                db.commit()
                sent += 1
                failures = 0
            else:
                # Skip this job (don't let one bad message block the rest), but
                # bail out of the batch if failures pile up (likely an outage).
                failures += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    log.warning("telegram: stopping batch after %d failures", failures)
                    break
        if sent:
            log.info("telegram: sent %d alert(s)", sent)
        return {"sent": sent}
    finally:
        db.close()
