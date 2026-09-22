# JobRadar (Python)

Personal freelance/remote job aggregator with an AI apply-assistant. It pulls
remote jobs from many sources into one local feed, ranks them by an AI fit score,
drafts an editable proposal for each, offers one-click **assisted** apply (copy
proposal + open the job page — it never auto-submits), tracks applications on a
kanban board, and can ping you on Telegram when a strong match appears.

Runs locally with no build step (HTMX + Tailwind + Chart.js via CDN), or on a VPS
with Docker.

> **Status: complete (Phases 1–4).**

---

## Features

- **Aggregated feed** — RemoteOK, Remotive, We Work Remotely, SkipTheDrive,
  Jobgether, Underdog.io, Hacker News "Who is hiring", Reddit (`r/forhire`,
  `r/remotejs`), Upwork saved-search RSS, and LinkedIn job-alert emails (IMAP).
  Wellfound (AngelList) is available via headless Chrome. Plus a manual
  **"+ Add job"** form.
- **AI fit scoring + proposal drafting** (OpenAI) — each job gets a 0–10 score, a
  one-line reason, and a ready-to-edit proposal. Low-scoring jobs collapse into a
  "Low match" section.
- **Dashboard** — sidebar filters (search, source, status, min score), job cards
  with source/score badges, and a detail drawer with an auto-saving proposal and
  notes.
- **Assisted apply** — marks the job applied, copies the proposal to your
  clipboard, and opens the job URL. Never submits anything for you.
- **Kanban tracker** — `new → shortlisted → applied → replied → interview →
  won / lost`, drag-and-drop or per-card status menu.
- **Telegram alerts** — jobs scoring ≥ your threshold (default 8) ping you with
  title, company, score, reason, and URL.
- **Stats** — jobs per source per day, applications per week, reply/interview
  rates (Chart.js).
- **Keyboard shortcuts** on the feed and **CSV export** of the tracker.

## Tech stack

Python 3.11+ · FastAPI + Uvicorn · Jinja2 + HTMX + Tailwind + Chart.js (all via
CDN, no npm) · MySQL or SQLite via SQLAlchemy 2.x · APScheduler · httpx / feedparser /
BeautifulSoup · openai SDK · imap-tools. Dark theme.

---

## Quick start (local)

Requires **Python 3.11+**.

```bash
# 1. virtualenv
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

# 2. dependencies
pip install -r requirements.txt

# 3. secrets (optional to start; needed for AI + notifications)
copy .env.example .env      # Windows
# cp .env.example .env      # macOS/Linux

# 4. run
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000**. On startup the app creates the database, seeds
settings, starts the 10-minute fetch scheduler, and runs one fetch immediately in
the background — jobs appear within a few seconds.

### Sharing it with your team (same network)

By default uvicorn binds to `127.0.0.1`, which is **this machine only** — nobody
else can reach it, firewall rule or not. To let teammates in, bind to every
interface:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Teammates then open **http://&lt;your-LAN-IP&gt;:8000** (find it with `ipconfig` on
Windows or `ip addr` on Linux/macOS). Two things to get right first:

- **Set `JOBRADAR_ACCESS_KEY` in `.env`.** Without it the app runs open, and
  anyone who can reach the port sees your feed, résumé and tracker. With it, every
  page requires the key and the 30-day cookie is `httponly` + `samesite=lax`.
  Changing the key logs everyone out.
- **Allow the port through the firewall**, scoped to your own subnet rather than
  the whole world (PowerShell as Administrator):

  ```powershell
  New-NetFirewallRule -DisplayName "JobRadar 8000" -Direction Inbound `
    -Protocol TCP -LocalPort 8000 -Action Allow `
    -Profile Private,Domain -RemoteAddress 192.168.101.0/24
  ```

Only the web app is exposed this way — the database still listens on
`127.0.0.1`, and `/cron/fetch` refuses to run unless `CRON_SECRET` is set. Don't
port-forward this to the internet: it's plain HTTP with a single shared key, so
it belongs on a trusted LAN or behind a VPN / reverse proxy with TLS.

---

## Configuration

All secrets come from `.env` only (copy from `.env.example`). Nothing is required
to *see jobs* — the app runs without any keys, just without AI and notifications.

| Variable | Needed for | Notes |
|---|---|---|
| `OPENAI_API_KEY` | AI scoring & proposals | Without it, jobs fetch but stay unscored; a banner explains. |
| `OPENAI_MODEL` | (optional) | Defaults to `gpt-4o-mini`. Set e.g. `gpt-4o` to change. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram alerts | Create a bot with @BotFather; get your chat id from @userinfobot. Off until both are set. |
| `IMAP_HOST`, `IMAP_USER`, `IMAP_PASSWORD`, `IMAP_FOLDER` | LinkedIn email source | Auto-forward LinkedIn job alerts to that folder, then enable **LinkedIn** in Settings. `IMAP_FOLDER` defaults to `INBOX`. |
| `JOBRADAR_CONTACT` | polite scraping | Advertised in the scraper User-Agent. |
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DB` | database | Set these to use MySQL/MariaDB (default port 3306). The database is created automatically on first run. |
| `DATABASE_URL` | (optional) | Any SQLAlchemy URL; overrides the `MYSQL_*` vars. |
| `JOBRADAR_DB_PATH` | (optional) | SQLite file path — only used when no MySQL/`DATABASE_URL` is configured (SQLite is the fallback for quick local/dev runs). |

### Database

JobRadar uses **MySQL/MariaDB** when the `MYSQL_*` vars are set (as in `.env.example`),
and falls back to a local **SQLite** file otherwise. On startup it creates the
database (MySQL) and all tables automatically — no manual SQL needed. Point
`MYSQL_HOST`/`MYSQL_USER`/`MYSQL_PASSWORD`/`MYSQL_DB` at your server and run.

Everything else is configured in the app under **Settings**: keywords, blocked
keywords, minimum score, which sources are enabled, Upwork RSS URLs, the alert
threshold, and the profile text sent to the AI.

### Sources

- **RemoteOK / Remotive / We Work Remotely** — on by default, no config.
- **Hacker News** — the latest official "Ask HN: Who is hiring?" thread; each
  top-level comment mentioning REMOTE + a keyword becomes a job.
- **Reddit** — `r/forhire` + `r/remotejs`, only `[Hiring]` posts. Reddit may 403
  from datacenter/VPN IPs (works from home IPs); a block is logged and skipped.
- **Upwork** — paste saved-search RSS URLs in Settings. A feed that 403s / hits a
  login wall shows a warning banner instead of crashing.
- **LinkedIn email** — off by default; set the `IMAP_*` vars, forward alerts to the
  folder, and enable it in Settings.
- **SkipTheDrive** — on by default. Their RSS feed redirects to the homepage, so
  this reads the WordPress REST API (`/wp-json/wp/v2/job`) and takes the newest
  `SKIPTHEDRIVE_MAX_JOBS` imported jobs. The employer isn't a field, so it's
  recovered from the post slug (`<company>-<title>-<id>`).
- **Jobgether** — on by default. One request to their server-rendered
  `/search-offers` page yields ~50 offers with company, location, salary and
  skill tags. Tune with `JOBGETHER_KEYWORD` / `JOBGETHER_MAX_JOBS`.
- **Underdog.io** — on by default. Uses the public JSON API behind their startup
  job board. Underdog anonymises employers ("our hiring partner"), so jobs have
  no company name; salary bands and cities are included. Their API caps a page at
  20 jobs, so `UNDERDOG_MAX_PAGES` (default 3) covers the board.
- **Wellfound (AngelList)** — off by default; needs Chrome + `selenium`. Wellfound
  sits behind a DataDome device check that rejects plain HTTP, so this loads the
  homepage first (which clears the check) and then the public role search. Set
  `WELLFOUND_ROLE` / `WELLFOUND_REMOTE`. Against their ToS — personal use only.
- **Built In** — off by default, and honestly the weakest of the set:
  `builtin.com/jobs*` is blocked by their Cloudflare WAF for plain HTTP *and* for
  real Chrome (headless or not), so scraping is out. Their public GraphQL API is
  wired up instead, but its job resolver currently fails on most pages with an
  upstream `ats_details` error, so a cycle usually returns nothing and logs one
  line. It will start working unchanged if Built In fixes that.

### AI scoring

Each fetch cycle scores unscored jobs oldest-first. **Settings → AI scoring per
cycle** caps how many; `0` (the default) means no limit, so the backlog never
grows. Set a number if you'd rather keep cycles short — scoring is sequential and
makes two API calls per job, so a large first run can take a while (it only
delays the next fetch, it never overlaps one). A job that keeps failing to score
is retried a few times then set aside. Use **Re-score** in a job's drawer to
force one immediately. Manually added jobs are scored on add.

### Notifications

With Telegram configured, each cycle sends alerts for high-fit jobs (score ≥ the
Settings **Alert threshold**), at most once per job, capped per cycle so a first-run
backlog doesn't flood you. Use **Settings → Send test message** to verify setup.

### Keyboard shortcuts (feed)

`j` / `k` next / previous card · `s` shortlist · `i` ignore · `a` apply ·
`Enter` open the drawer. (Ignored while typing in a field.)

---

## Deploy on Vercel (serverless)

See **[DEPLOY_VERCEL.md](DEPLOY_VERCEL.md)** for the complete step-by-step
guide (hosted database, cron-based fetching, environment variables, limits).

## Deploy on a VPS (Docker)

Requires Docker + Docker Compose.

```bash
git clone <your-repo> jobradar && cd jobradar
cp .env.example .env        # fill in OPENAI_API_KEY, TELEGRAM_*, etc.
docker compose up -d --build
```

- The app listens on port **8000** (`http://<server>:8000`). Put it behind a
  reverse proxy (Caddy/Nginx) for TLS.
- The SQLite database persists in `./data` on the host (mounted to `/data` in the
  container via `JOBRADAR_DB_PATH`), so it survives rebuilds.
- Logs: `docker compose logs -f`. Stop: `docker compose down`.
- Update: `git pull && docker compose up -d --build`.

`.env` and the database are gitignored and excluded from the image.

---

## Project layout

```
jobradar/
  app/
    main.py            FastAPI app, routes, scheduler, lightweight migrations
    db.py              engine/session (MySQL or SQLite, chosen from env)
    models.py          SQLAlchemy models
    settings.py        DB-backed settings + seeding
    pipeline.py        run_fetch_cycle(): fetch -> dedupe -> filter -> insert -> score -> notify
    ai.py              OpenAI scoring + proposal drafting
    notify.py          Telegram notifications
    fetchers/          remoteok, remotive, wwr, hn, reddit, upwork_rss, linkedin_email
    templates/         Jinja2 + HTMX
    static/            app.css, app.js, keyboard.js
  requirements.txt  .env.example  .gitignore
  Dockerfile  docker-compose.yml  .dockerignore
  README.md
```

---

## How it works / ethics

- **Assisted apply only.** JobRadar never submits applications or sends messages on
  any platform — it copies your proposal and opens the page. This is non-negotiable
  (platform ToS).
- **Polite scraping.** One request per source per cycle, exponential backoff, and an
  honest User-Agent that includes a contact address. A few sources need a small
  bounded number of requests instead of one — Underdog.io because its API caps a
  page at 20 jobs (`UNDERDOG_MAX_PAGES`), Wellfound because the device check needs
  a homepage load first. Boards whose WAF rejects a non-browser User-Agent
  (Jobgether, Built In) get a browser UA with JobRadar's name and contact
  appended, so we stay identifiable rather than anonymous.
- **Resilient.** Every fetcher, the scoring step, and notifications are isolated —
  one failing source or a slow API never breaks the cycle or the app.
- **Local & private.** Everything runs on your machine (or your VPS); data lives in
  your own MySQL database (or a local SQLite file).
