"""SQLAlchemy models for JobRadar."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Integer, String, Text, DateTime, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

# On MySQL, plain TEXT caps at 64KB; a long job description could overflow it and
# fail the insert. Use LONGTEXT there, plain Text on SQLite.
_LONG_TEXT = Text().with_variant(LONGTEXT, "mysql")

# Canonical status values for a job / application.
STATUSES = [
    "new",
    "shortlisted",
    "applied",
    "replied",
    "interview",
    "won",
    "lost",
    "ignored",
]

# Columns rendered as kanban tracker columns, in order.
TRACKER_COLUMNS = [
    "new",
    "shortlisted",
    "applied",
    "replied",
    "interview",
    "won",
    "lost",
]


def utcnow() -> datetime:
    # Naive UTC to match the DateTime columns (which store naive values). Returning
    # a tz-aware value here would make in-memory rows uncomparable with rows loaded
    # from SQLite when sorting (offset-naive vs offset-aware TypeError).
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("dedupe_hash", name="uq_jobs_dedupe_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    source: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(512))
    company: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str] = mapped_column(_LONG_TEXT, default="")

    tags: Mapped[list] = mapped_column(JSON, default=list)
    budget: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Contact emails extracted from the listing (for direct, assisted outreach).
    contact_emails: Mapped[list] = mapped_column(JSON, default=list)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ai_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_proposal: Mapped[str | None] = mapped_column(Text, nullable=True)
    # How many times AI scoring has failed for this job; caps retries so a
    # permanently-failing ("poison") job can't burn API budget or starve others.
    ai_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # When a Telegram alert was sent for this job (Phase 4) — prevents duplicates.
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    dedupe_hash: Mapped[str] = mapped_column(String(64), index=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(JSON)
