"""Vercel serverless entrypoint.

Vercel's Python runtime looks for an ASGI `app` in api/*.py; vercel.json
rewrites every route here. VERCEL=1 (set by the platform) switches JobRadar
into serverless mode: no APScheduler, fetching via /cron/fetch instead.
"""
import os
import sys

# Make the repo root importable (this file lives in api/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402,F401  (ASGI app picked up by Vercel)
