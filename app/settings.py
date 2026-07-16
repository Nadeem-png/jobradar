"""Settings helpers. Settings live in the DB (Setting table) as JSON values."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Setting

# The owner profile from SPEC_PYTHON.md. Editable via Settings page in Phase 2.
DEFAULT_PROFILE_TEXT = (
    "Nadeem Khan - Full Stack Developer (Lahore, Pakistan, works remote for US/EU "
    "clients).\n"
    "Backend: Laravel, PHP, Node.js, Python, REST APIs, MySQL, PostgreSQL, MongoDB.\n"
    "Frontend: React, Next.js, JavaScript, Bootstrap, Tailwind.\n"
    "Specialty: AI integration - real-time speech-to-text transcription, sentiment "
    "analysis, Claude API integration, AI dashboards, WebSockets/real-time apps, "
    "Asterisk/VoIP telephony.\n"
    "Flagship projects:\n"
    "  1. AI-Powered Call Center Dialer (Laravel + Asterisk AMI + real-time "
    "transcription + live sentiment analysis + AI wrap-up summaries).\n"
    "  2. HR Management System (attendance, payroll, leave workflows, RBAC).\n"
    "Target: hourly remote freelance work, $20-50/hr, US and Europe clients."
)

# Default settings seeded on first startup.
DEFAULTS: dict[str, object] = {
    "keywords": [
        "laravel", "php", "python", "next.js", "nextjs", "react", "node",
        "full stack", "api", "ai", "chatbot", "openai", "claude", "llm",
        "integration", "dashboard", "saas", "fastapi",
        # Generic dev terms so title-only listings ("Software Engineer",
        # "Web Developer") from boards like Rozee/Mustakbil aren't dropped.
        "developer", "software engineer", "backend", "frontend", "web",
    ],
    "blocked_keywords": [
        "wordpress only", "shopify only", "unpaid", "equity only",
    ],
    "min_score": 6,
    "sources_enabled": {
        "remoteok": True,
        "remotive": True,
        "wwr": True,
        "jobicy": True,
        "arbeitnow": True,
        "themuse": True,
        # Indeed's public RSS is blocked (403) and needs a Publisher API key —
        # off by default so it doesn't log a failure every cycle.
        "indeed": False,
        # Pakistani boards. Mustakbil serves plain HTML (works). Rozee blocks
        # datacenter IPs but usually works from home connections. Bayt sits
        # behind a Cloudflare JS challenge and Indeed PK is blocked like
        # indeed.com — both off by default.
        "mustakbil": True,
        "rozee": True,
        "bayt": False,
        "indeed_pk": False,
        "hn": True,
        "reddit": True,
        "upwork_rss": True,
        # LinkedIn email is off until IMAP creds are configured in .env.
        "linkedin_email": False,
        # LinkedIn via Selenium (public guest search, no login). Off by default:
        # needs Chrome + selenium and can hit an authwall on some networks.
        "linkedin_web": False,
    },
    "profile_text": DEFAULT_PROFILE_TEXT,
    "upwork_rss_urls": [],
    "upwork_feed_warnings": {},
    # Phase 4: Telegram alerts fire for jobs scoring >= this value.
    "alert_threshold": 8,
    # Phase 5: uploaded résumé profile (drives the Resume tab's relevance matching).
    "resume_filename": "",
    "resume_skills": [],
    "resume_titles": [],
    "resume_seniority": "",
    "resume_summary": "",
    "resume_uploaded_at": "",
}


def seed_settings(db: Session) -> None:
    """Insert any missing default settings. Existing values are left untouched."""
    existing = {row[0] for row in db.execute(select(Setting.key)).all()}
    changed = False
    for key, value in DEFAULTS.items():
        if key not in existing:
            db.add(Setting(key=key, value=value))
            changed = True
    if changed:
        db.commit()


def get_setting(db: Session, key: str, default=None):
    row = db.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key, default)
    return row.value


def set_setting(db: Session, key: str, value) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def all_settings(db: Session) -> dict:
    out = dict(DEFAULTS)
    for row in db.execute(select(Setting)).scalars():
        out[row.key] = row.value
    # Merge sources_enabled sub-keys so sources added in later phases pick up their
    # default even when the stored dict predates them.
    out["sources_enabled"] = effective_sources_enabled(db)
    return out


def effective_sources_enabled(db: Session) -> dict:
    """Stored sources_enabled merged over the defaults (new sources get defaults)."""
    merged = dict(DEFAULTS["sources_enabled"])
    stored = get_setting(db, "sources_enabled", {}) or {}
    if isinstance(stored, dict):
        merged.update(stored)
    return merged
