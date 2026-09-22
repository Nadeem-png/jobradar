"""Fetcher registry.

Each fetcher module exposes ``SOURCE`` (str) and ``fetch() -> list[dict]`` of
normalized jobs. FETCHERS maps source key -> fetch callable. Phase 1 ships three
sources; Phase 3 sources get added here later.
"""
from __future__ import annotations

from typing import Callable

from . import (
    arbeitnow, bayt, builtin, hn, indeed, indeed_pk, jobgether, jobicy,
    linkedin_email, linkedin_selenium, mustakbil, reddit, remoteok, remotive,
    rozee, skipthedrive, themuse, underdog, upwork_rss, wellfound, wwr,
)

# source key -> fetch callable. Order here is the display order in Settings.
FETCHERS: dict[str, Callable[[], list[dict]]] = {
    remoteok.SOURCE: remoteok.fetch,
    remotive.SOURCE: remotive.fetch,
    wwr.SOURCE: wwr.fetch,
    jobicy.SOURCE: jobicy.fetch,
    arbeitnow.SOURCE: arbeitnow.fetch,
    themuse.SOURCE: themuse.fetch,
    skipthedrive.SOURCE: skipthedrive.fetch,
    jobgether.SOURCE: jobgether.fetch,
    underdog.SOURCE: underdog.fetch,
    wellfound.SOURCE: wellfound.fetch,
    builtin.SOURCE: builtin.fetch,
    indeed.SOURCE: indeed.fetch,
    hn.SOURCE: hn.fetch,
    reddit.SOURCE: reddit.fetch,
    upwork_rss.SOURCE: upwork_rss.fetch,
    linkedin_email.SOURCE: linkedin_email.fetch,
    linkedin_selenium.SOURCE: linkedin_selenium.fetch,
    # Pakistani job boards
    mustakbil.SOURCE: mustakbil.fetch,
    rozee.SOURCE: rozee.fetch,
    bayt.SOURCE: bayt.fetch,
    indeed_pk.SOURCE: indeed_pk.fetch,
}

# Human-friendly labels for the UI.
SOURCE_LABELS: dict[str, str] = {
    "remoteok": "RemoteOK",
    "remotive": "Remotive",
    "wwr": "We Work Remotely",
    "jobicy": "Jobicy",
    "arbeitnow": "Arbeitnow",
    "themuse": "The Muse",
    "skipthedrive": "SkipTheDrive",
    "jobgether": "Jobgether",
    "underdog": "Underdog.io",
    "wellfound": "Wellfound (AngelList)",
    "builtin": "Built In",
    "indeed": "Indeed",
    "hn": "Hacker News",
    "reddit": "Reddit",
    "upwork_rss": "Upwork",
    "linkedin_email": "LinkedIn (email)",
    "linkedin_web": "LinkedIn (web)",
    "mustakbil": "Mustakbil (PK)",
    "rozee": "Rozee.pk",
    "bayt": "Bayt (PK)",
    "indeed_pk": "Indeed PK",
    "manual": "Manual",
}
