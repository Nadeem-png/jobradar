# JobRadar (Python) — Personal Freelance Job Aggregator & Apply Assistant

## Owner profile (use this in AI scoring and proposal prompts)
- Name: Nadeem Khan — Full Stack Developer (Lahore, Pakistan, works remote for US/EU clients)
- Backend: Laravel, PHP, Node.js, Python, REST APIs, MySQL, PostgreSQL, MongoDB
- Frontend: React, Next.js, JavaScript, Bootstrap, Tailwind
- Specialty: AI integration — real-time speech-to-text transcription, sentiment analysis,
  Claude API integration, AI dashboards, WebSockets/real-time apps, Asterisk/VoIP telephony
- Flagship projects:
  1. AI-Powered Call Center Dialer (Laravel + Asterisk AMI + real-time transcription +
     live sentiment analysis + AI wrap-up summaries)
  2. HR Management System (attendance, payroll, leave workflows, RBAC)
- Target: hourly remote freelance work, $20–50/hr, US and Europe clients

## Goal
A local web app where I see all matching freelance/remote jobs from many sources in one
feed, ranked by an AI fit score, each with a ready-to-edit AI-drafted proposal, and
one-click assisted apply + a kanban tracker for my applications.

## Tech stack (do not change without asking me)
- Python 3.11+
- FastAPI + Uvicorn
- Jinja2 templates + HTMX (via CDN) + Tailwind CSS (via CDN) — NO React, NO npm, no build step
- SQLite database via SQLAlchemy 2.x ORM (single file: jobradar.db)
- APScheduler (BackgroundScheduler) started inside the FastAPI app for periodic fetching
- httpx for HTTP calls, feedparser for RSS, beautifulsoup4 for HTML parsing
- anthropic official SDK, model "claude-sonnet-4-6", API key from env var ANTHROPIC_API_KEY
- imap-tools for reading email (Phase 3)
- python-dotenv for .env loading
- Dark theme by default, clean modern UI

## Project layout
```
jobradar/
  app/
    main.py            # FastAPI app, routes, scheduler startup
    models.py          # SQLAlchemy models
    db.py              # engine/session
    settings.py        # settings helpers (stored in DB)
    fetchers/
      __init__.py      # registry: list of enabled fetchers
      remoteok.py
      remotive.py
      wwr.py
      hn.py            # Phase 3
      reddit.py        # Phase 3
      upwork_rss.py    # Phase 3
      linkedin_email.py# Phase 3
    ai.py              # Claude scoring + proposal drafting
    notify.py          # Telegram (Phase 4)
    templates/         # Jinja2 + HTMX
    static/
  requirements.txt
  .env.example
  README.md
```

## Data model (SQLAlchemy)
Job:
- id (pk), source (str: remoteok|wwr|remotive|hn|reddit|upwork_rss|linkedin_email|manual)
- external_id (str), url (str), title, company, description (text)
- tags (JSON list), budget (str, nullable), location (str, nullable)
- posted_at (datetime, nullable), fetched_at (datetime)
- ai_score (int 0–10, nullable), ai_reason (str, nullable), ai_proposal (text, nullable)
- status (str): new | shortlisted | applied | replied | interview | won | lost | ignored
- applied_at (datetime, nullable), notes (text, default "")
- dedupe_hash (sha256 of normalized lowercase url) — UNIQUE index

Setting:
- key (pk), value (JSON) — keywords, blocked_keywords, min_score, sources_enabled,
  profile_text (the owner profile above, editable), upwork_rss_urls (list)

## Phase 1 — Core app + 3 easy sources (BUILD THIS FIRST)
1. FastAPI app skeleton, SQLAlchemy models, DB auto-create on startup, seed Settings:
   - keywords: ["laravel","php","python","next.js","nextjs","react","node","full stack",
     "api","ai","chatbot","openai","claude","llm","integration","dashboard","saas","fastapi"]
   - blocked_keywords: ["wordpress only","shopify only","unpaid","equity only"]
   - min_score: 6, all Phase-1 sources enabled
2. Fetchers — each module exposes fetch() -> list[dict] of normalized jobs:
   - remoteok.py: GET https://remoteok.com/api (JSON; send real User-Agent header;
     SKIP the first array element — it is legal metadata, not a job)
   - remotive.py: GET https://remotive.com/api/remote-jobs?category=software-dev (JSON)
   - wwr.py: feedparser on https://weworkremotely.com/categories/remote-programming-jobs.rss
3. Pipeline function run_fetch_cycle():
   - run all enabled fetchers (each wrapped in try/except — one failing source must
     never kill the cycle; log errors)
   - normalize, compute dedupe_hash, skip if hash exists
   - keyword filter: title+description must contain >=1 keyword and 0 blocked keywords
     (case-insensitive)
   - insert with status="new"
   - APScheduler runs this every 10 minutes; also run once on startup; also expose
     POST /fetch-now button in the UI
4. Dashboard (GET /):
   - Left sidebar: filters — source checkboxes, status dropdown, min AI score slider,
     text search (HTMX live-filtering: inputs trigger hx-get to /jobs-partial which
     returns the job list fragment)
   - Job cards: title, company, source badge, relative posted time, tags, budget,
     AI score badge (8+ green, 5–7 yellow, <5 gray, unscored = outline)
   - Click card → HTMX loads detail drawer: full description, ai_reason, editable
     proposal textarea (auto-saves on blur via hx-post), notes field
   - Card buttons: Shortlist, Ignore, Apply
5. Apply flow (POST /jobs/{id}/apply): sets status=applied + applied_at, response
   triggers small JS snippet that copies the proposal text to clipboard and opens the
   job URL in a new tab. Toast: "Proposal copied — paste it on the job page."
6. Tracker (GET /tracker): kanban columns new → shortlisted → applied → replied →
   interview → won / lost. Moving cards = HTMX buttons on each card ("move to →")
   or SortableJS via CDN for drag-drop if simple to wire to hx-post.
7. requirements.txt, .env.example, README with exact run commands:
   pip install -r requirements.txt && uvicorn app.main:app --reload

## Phase 2 — AI scoring + proposal drafting
1. ai.py: score_and_draft(job, profile_text) using anthropic SDK:
   - Prompt Claude with the profile + job title/description + instructions:
     a) score 0–10 fit for my skills and target (remote, US/EU-friendly, hourly, my stack;
        on-site-only or wrong-stack jobs score low)
     b) one-sentence reason
     c) proposal draft: max 150 words, first line addresses THEIR specific problem,
        mentions whichever ONE flagship project is most relevant, plain conversational
        English, no clichés like "results-driven", ends with one short question.
     Require STRICT JSON output: {"score": n, "reason": "...", "proposal": "..."}
   - Parse defensively: strip markdown fences, json.loads in try/except; on failure
     leave job unscored for retry next cycle. Respect API errors with backoff.
2. After each fetch cycle, score unscored jobs (max 10 per cycle, oldest first) so
   API costs stay tiny.
3. Settings page (GET/POST /settings): edit keywords, blocked keywords, min_score,
   toggle sources, edit profile_text, per-job "Re-score" button in the drawer.
4. Feed sort: ai_score desc NULLS LAST, then posted_at desc. Jobs under min_score
   collapse into a "Low match" section at the bottom.

## Phase 3 — More sources
1. hn.py — Hacker News "Who is hiring":
   - Find latest thread: https://hn.algolia.com/api/v1/search_by_date?query="Ask HN: Who is hiring"&tags=story
   - Fetch its comments: https://hn.algolia.com/api/v1/items/{story_id}
   - Each top-level comment containing "REMOTE" + >=1 keyword becomes a job
     (url = news.ycombinator.com/item?id={comment_id}, company = first line of comment)
2. reddit.py — GET https://www.reddit.com/r/forhire/new.json and /r/remotejs/new.json
   with a descriptive User-Agent. Only posts whose title contains "[Hiring]".
3. upwork_rss.py — Settings holds a list of Upwork saved-search RSS URLs I paste in
   (I create saved searches on Upwork and copy the RSS link). Parse with feedparser.
   If a feed returns 403/login-wall, mark that feed with a warning banner in Settings
   instead of crashing.
4. linkedin_email.py — IMAP poller with imap-tools. .env: IMAP_HOST, IMAP_USER,
   IMAP_PASSWORD, IMAP_FOLDER (I auto-forward LinkedIn job alert emails to this folder).
   Read unseen messages, BeautifulSoup the HTML, extract job title/company/URL pairs,
   create jobs with source=linkedin_email, mark email seen. Credentials only from .env.
5. Manual add: "+ Add job" form — paste URL + title + description; scored by AI like
   any other job.

## Phase 4 — Notifications & polish
1. notify.py — Telegram: .env TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. After scoring,
   any job with score >= alert threshold (default 8) sends a Telegram message:
   title, company, score, reason, url. Simple httpx POST to the Bot API, no heavy libs.
2. Stats page (/stats): jobs per source per day (last 14 days), applications per week,
   reply rate, interview rate. Render with Chart.js via CDN.
3. Keyboard shortcuts on the feed: j/k = next/prev card, s = shortlist, i = ignore,
   a = apply, Enter = open drawer. Small vanilla JS file.
4. CSV export of the tracker (GET /export.csv).
5. Dockerfile (python:3.12-slim) + docker-compose.yml. README section: deploy on a VPS
   with docker compose up -d.

## Hard rules
- NEVER auto-submit applications or auto-send messages on any platform. Assisted apply
  only (open URL + clipboard). This is non-negotiable — platform ToS.
- Be a polite scraper: one request per source per cycle, exponential backoff on errors,
  honest User-Agent "JobRadar personal aggregator (contact: nadeemmkhan441@gmail.com)".
- All secrets from .env only; commit .env.example, gitignore .env and jobradar.db.
- Every fetcher isolated with try/except + logging; the app must keep working when
  individual sources fail.
- After finishing each phase: give me exact commands to run and a short manual test
  checklist. Wait for my confirmation before starting the next phase.
