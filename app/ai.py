"""AI scoring + proposal drafting via the OpenAI SDK (Phase 2).

score_and_draft(job, profile_text) asks the model to (a) score fit 0-10, (b) give a
one-sentence reason, and (c) draft a short proposal, returning STRICT JSON. Output
is parsed defensively (strip fences, json.loads in try/except); on any failure the
job is left unscored so it retries next cycle.

Secrets come only from the environment (loaded from .env by main.py via
python-dotenv). If OPENAI_API_KEY is missing the app keeps working — scoring is
skipped with a clear log message and the per-job Re-score button reports it.
"""
from __future__ import annotations

import json
import logging
import os
import re

from sqlalchemy import select

log = logging.getLogger("jobradar.ai")

# Model from OPENAI_MODEL in .env; defaults to a small, cheap model.
DEFAULT_MODEL = "gpt-4o-mini"

# Keep costs tiny: short output, no thinking, a handful of jobs per cycle.
MAX_TOKENS = 1024
DESCRIPTION_CHAR_LIMIT = 6000
# Stop retrying a job that keeps failing to score, so a "poison" job can't burn
# API budget forever or block newer jobs from the oldest-first queue.
MAX_SCORE_ATTEMPTS = 5

SYSTEM_PROMPT = """You are an assistant helping a freelance full-stack developer \
triage remote job listings and draft application proposals.

You will be given the developer's profile and one job listing. Do three things:

1. Score the job from 0 to 10 for how well it fits the developer's skills and \
target: remote work, US/EU-friendly clients, hourly freelance in their stack. \
Score LOW (0-3) for jobs that are on-site only, not remote, require an unrelated \
or wrong tech stack, are unpaid/equity-only, or are not real paid development \
work. Score HIGH (8-10) only for strong matches in their stack and target.

2. Write a ONE-sentence reason for the score.

3. Draft a proposal, MAX 150 words:
   - the FIRST line must address THEIR specific problem or need from this listing
   - mention whichever ONE of the developer's flagship projects is most relevant
   - plain, conversational English
   - NO cliches like "results-driven", "I am passionate", "hit the ground running"
   - end with ONE short question.

Respond with STRICT JSON only. No markdown, no code fences, no commentary. \
Use exactly this shape:
{"score": <integer 0-10>, "reason": "<one sentence>", "proposal": "<proposal text>"}"""


class AIConfigError(RuntimeError):
    """Raised when the AI client cannot be configured (missing key/package)."""


class AIError(RuntimeError):
    """Raised when a scoring call fails or returns unusable output."""


_client = None


def api_key_present() -> bool:
    """True if an OPENAI_API_KEY is set (non-empty) in the environment."""
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


def model_name() -> str:
    return os.getenv("OPENAI_MODEL", DEFAULT_MODEL)


def get_client():
    """Return a cached OpenAI client, or raise AIConfigError with a clear message."""
    global _client
    if _client is not None:
        return _client

    if not api_key_present():
        raise AIConfigError(
            "OPENAI_API_KEY is not set. Add it to your .env file to enable AI "
            "scoring and proposal drafting."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency should be installed
        raise AIConfigError(
            "The 'openai' package is not installed. Run: "
            "pip install -r requirements.txt"
        ) from exc

    # SDK auto-retries 429/5xx with exponential backoff (spec: respect API errors
    # with backoff). timeout bounds a single call so a hang can't stall the cycle.
    _client = OpenAI(max_retries=4, timeout=60.0)
    return _client


def _extract_json(text: str) -> dict:
    """Parse a JSON object out of the model's text, tolerating markdown fences."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t.strip())
    # Fall back to the outermost {...} span if there is stray prose.
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        t = t[start : end + 1]
    return json.loads(t)


def score_and_draft(job, profile_text: str) -> dict:
    """Score one job and draft a proposal.

    ``job`` is any object with title/company/source/location/budget/tags/description
    attributes. Returns {"score": int 0-10, "reason": str, "proposal": str}.
    Raises AIConfigError (no key/package) or AIError (call/parse failure).
    """
    client = get_client()

    description = (job.description or "")[:DESCRIPTION_CHAR_LIMIT]
    tags = ", ".join(job.tags or [])
    user_content = (
        f"MY PROFILE:\n{profile_text}\n\n"
        f"JOB TO EVALUATE:\n"
        f"Title: {job.title}\n"
        f"Company: {job.company or 'Unknown'}\n"
        f"Source: {job.source}\n"
        f"Location: {job.location or 'Not specified'}\n"
        f"Budget: {job.budget or 'Not specified'}\n"
        f"Tags: {tags or 'none'}\n\n"
        f"Description:\n{description}\n"
    )

    try:
        resp = client.chat.completions.create(
            model=model_name(),
            max_tokens=MAX_TOKENS,
            temperature=0.4,
            # JSON mode: the model is constrained to return a valid JSON object.
            # (Requires the word "json" in the prompt — the system prompt has it.)
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
    except Exception as exc:  # noqa: BLE001 - surface as AIError for caller isolation
        raise AIError(f"OpenAI API call failed: {exc}") from exc

    text = (resp.choices[0].message.content or "") if resp.choices else ""

    try:
        data = _extract_json(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AIError(f"Could not parse JSON from model output: {exc}") from exc

    if "score" not in data:
        raise AIError("Model output missing 'score' field")
    try:
        score = int(round(float(data["score"])))
    except (TypeError, ValueError) as exc:
        raise AIError(f"Invalid score value: {data.get('score')!r}") from exc
    score = max(0, min(10, score))

    # The score is the essential output. A missing/empty proposal is acceptable
    # (models often decline to draft one for a poor-fit job) — don't fail the whole
    # scoring over it, or legitimately low-fit jobs would retry and stay unscored.
    reason = str(data.get("reason", "")).strip()
    proposal = str(data.get("proposal", "")).strip()

    return {"score": score, "reason": reason, "proposal": proposal}


RESUME_SYSTEM_PROMPT = """Extract the candidate's professional profile from their \
résumé text. Respond with STRICT JSON only, no markdown, in exactly this shape:
{"skills": ["..."], "titles": ["..."], "seniority": "junior|mid|senior|lead", "summary": "..."}
- skills: concrete technical skills, languages, frameworks, and tools the candidate \
knows (lowercase, deduped, most relevant first, max 40).
- titles: distinct job titles the candidate has held.
- seniority: your single best estimate.
- summary: 2-3 sentences on their experience, domains, and strengths."""


def extract_resume_profile(resume_text: str) -> dict:
    """Extract {skills, titles, seniority, summary} from résumé text via the model.

    Raises AIConfigError (no key) or AIError (call/parse failure).
    """
    client = get_client()
    text = (resume_text or "")[:12000]
    try:
        resp = client.chat.completions.create(
            model=model_name(),
            max_tokens=MAX_TOKENS,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": RESUME_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )
    except Exception as exc:  # noqa: BLE001
        raise AIError(f"OpenAI API call failed: {exc}") from exc

    content = (resp.choices[0].message.content or "") if resp.choices else ""
    try:
        data = _extract_json(content)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AIError(f"Could not parse résumé JSON: {exc}") from exc

    def _clean_list(v, cap):
        # The model may return a comma/newline string instead of a JSON list;
        # coerce it rather than iterating a string character-by-character.
        if isinstance(v, str):
            items = re.split(r"[,\n;]+", v)
        elif isinstance(v, (list, tuple)):
            items = v
        else:
            items = []
        out, seen = [], set()
        for item in items:
            s = str(item).strip()
            key = s.lower()
            if s and key not in seen:
                seen.add(key)
                out.append(s.lower())
            if len(out) >= cap:
                break
        return out

    return {
        "skills": _clean_list(data.get("skills"), 40),
        "titles": _clean_list(data.get("titles"), 12),
        "seniority": str(data.get("seniority", "")).strip().lower() or "mid",
        "summary": str(data.get("summary", "")).strip(),
    }


def score_new_jobs(limit: int | None = None) -> dict:
    """Score unscored jobs (oldest first). Called after each fetch.

    ``limit`` caps how many jobs one call will score:
      * ``None`` (default) — use the "AI scoring per cycle" setting.
      * ``0`` — no cap: score every unscored job.
      * a positive int — score at most that many.

    Each job is scored in its own try/except and committed independently so one
    failure never aborts the batch; failed jobs stay unscored and retry next cycle.
    """
    if not api_key_present():
        log.info("AI scoring skipped: OPENAI_API_KEY not set")
        return {"scored": 0, "failed": 0, "skipped_no_key": True}

    from .db import SessionLocal
    from .models import Job
    from .settings import get_setting

    db = SessionLocal()
    scored = failed = 0
    try:
        profile = get_setting(db, "profile_text", "") or ""

        if limit is None:
            try:
                limit = int(get_setting(db, "ai_score_limit", 0) or 0)
            except (TypeError, ValueError):
                limit = 0
        limit = max(0, limit)

        query = (
            select(Job)
            .where(Job.ai_score.is_(None))
            .where(Job.ai_attempts < MAX_SCORE_ATTEMPTS)
            .order_by(Job.fetched_at.asc(), Job.id.asc())
        )
        if limit:
            query = query.limit(limit)
        rows = db.execute(query).scalars().all()
        if rows:
            log.info(
                "AI scoring %d job(s)%s", len(rows),
                "" if limit else " (no per-cycle limit)",
            )
        for job in rows:
            try:
                result = score_and_draft(job, profile)
            except AIConfigError as exc:
                log.error("AI scoring aborted: %s", exc)
                break
            except Exception as exc:  # noqa: BLE001 - one job must not abort the batch
                failed += 1
                try:
                    job.ai_attempts = (job.ai_attempts or 0) + 1
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
                log.warning(
                    "scoring failed for job %s (attempt %s/%s): %s",
                    job.id, job.ai_attempts, MAX_SCORE_ATTEMPTS, exc,
                )
                continue

            try:
                job.ai_score = result["score"]
                job.ai_reason = result["reason"]
                # Don't clobber a proposal the user may have edited.
                if not job.ai_proposal:
                    job.ai_proposal = result["proposal"]
                db.commit()
                scored += 1
            except Exception:  # noqa: BLE001 - a bad commit must not abort the batch
                db.rollback()
                failed += 1
                log.exception("committing score for job %s failed", job.id)

        log.info("AI scoring: scored=%d failed=%d", scored, failed)
        return {"scored": scored, "failed": failed}
    finally:
        db.close()
