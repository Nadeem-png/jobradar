# Deploying JobRadar on Vercel — no database needed

JobRadar deploys to Vercel with **zero database setup**: on Vercel it uses its
own built-in SQLite storage automatically. The repo already contains everything
Vercel needs (`api/index.py`, `vercel.json`, `.vercelignore`) and the app
detects Vercel (`VERCEL=1`) and adapts by itself.

**One tradeoff to know (honest):** Vercel's storage is temporary. When the app
has had no visitors for a while it "goes cold" and stored data resets — the
job feed refills with one click of **Fetch now** (or the daily cron), but
tracker moves / settings changes / uploaded résumé won't survive long-term.
For browsing and applying to jobs, that's fine. If you ever want permanent
storage, add a `DATABASE_URL` env var later (see the last section) — no code
changes needed.

---

## Step 1 — Push the project to GitHub

Vercel deploys from a Git repository. From the project folder:

```powershell
cd C:\Users\Administrator\Documents\jobradar
git init
git add .
git commit -m "JobRadar"
```

`.gitignore` already excludes `.env` and `*.db` — your secrets and local
database are never uploaded. Create an empty **private** repo at
<https://github.com/new> (name it `jobradar`), then:

```powershell
git remote add origin https://github.com/<your-username>/jobradar.git
git branch -M main
git push -u origin main
```

## Step 2 — Import the project on Vercel

1. Go to <https://vercel.com> → **Sign up / Log in with GitHub**.
2. **Add New… → Project** → pick your `jobradar` repository → **Import**.
3. Framework preset: **Other**. Leave build settings at their defaults
   (`vercel.json` handles everything).
4. **Before clicking Deploy**, open the **Environment Variables** section.

## Step 3 — Add environment variables (no database ones!)

| Name | Value | Why |
|---|---|---|
| `JOBRADAR_ACCESS_KEY` | your login key (e.g. `JR-...`) | **Required** — the site is on the public internet; this is the key screen |
| `OPENAI_API_KEY` | your OpenAI key | AI scoring + proposals |
| `CRON_SECRET` | any long random string | protects the scheduled-fetch endpoint |
| `OPENAI_MODEL` | `gpt-4o-mini` | optional |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | your bot | optional alerts |

Generate random values quickly:
`python -c "import secrets; print(secrets.token_urlsafe(24))"`

## Step 4 — Deploy

Click **Deploy**. In ~1 minute you get a URL like
`https://jobradar-xxxx.vercel.app`. Open it → the **JobRadar access-key
screen** appears → enter your `JOBRADAR_ACCESS_KEY` → the app opens.

## Step 5 — Fill the feed

The feed starts empty. Click **Fetch now** in the header and wait up to a
minute — jobs from all the working sources flow in. (Alternatively:
`curl -H "Authorization: Bearer <CRON_SECRET>" https://jobradar-xxxx.vercel.app/cron/fetch`)

A daily cron (06:00 UTC, the most Vercel's free plan allows) refetches
automatically. On the Pro plan you can change `vercel.json` to
`"schedule": "*/10 * * * *"` for 10-minute fetching like your PC.

## Step 6 — One-time settings

On the deployed site → **Settings** → turn **OFF**:

- **LinkedIn (web)** — needs Chrome/Selenium, which can't run on Vercel.
- **Rozee.pk** — blocked from datacenter IPs (403).

Everything else works: RemoteOK, Remotive, We Work Remotely, Jobicy,
Arbeitnow, The Muse, Hacker News, Upwork RSS, **Mustakbil (PK)**, manual add.

> Note: because storage is temporary, redo this settings tweak if the app has
> been cold — or just leave them on; they fail gracefully and only add noise.

## Updating the app later

```powershell
git add .
git commit -m "changes"
git push
```

Every push to `main` redeploys automatically.

## Troubleshooting

- **Login screen loops** → make sure `JOBRADAR_ACCESS_KEY` is set in Vercel →
  Settings → Environment Variables, then **Redeploy**.
- **"Fetch now" seems to time out** → normal on the free plan for a full
  cycle; progress is saved per source. Click it again.
- **Feed suddenly empty** → the app went cold and storage reset (expected in
  no-database mode). Click **Fetch now**.
- Function logs: Vercel → your project → **Deployments** → click one →
  **Functions** tab.

---

## Optional: permanent storage later

If the resets ever bother you (e.g. you rely on the Tracker), add ONE env var
— no code changes:

1. Create a free Postgres at <https://neon.tech> → copy the connection string.
2. In Vercel → Settings → Environment Variables, add:
   `DATABASE_URL = postgresql+psycopg2://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`
3. **Redeploy.** Data now persists forever.

## Honest recommendation

Vercel + no database is the easiest way to get a public URL. But JobRadar is a
long-running app at heart — on a $5 VPS or Railway/Render with the included
`Dockerfile`, everything works with no compromises: 10-minute scheduler,
LinkedIn via Chrome, permanent MySQL storage. Ask and I'll write that guide.
