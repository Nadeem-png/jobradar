"""Hacker News "Ask HN: Who is hiring?" fetcher.

Finds the latest "Who is hiring" story via the Algolia API, fetches its comment
tree, and turns each top-level comment that mentions REMOTE into a job. The
pipeline's keyword filter then drops comments that don't match the user's keywords
(spec: "REMOTE + >=1 keyword").
"""
from __future__ import annotations

from urllib.parse import urlencode

from .http import get
from .util import html_to_text, parse_dt

SOURCE = "hn"

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date?" + urlencode(
    {"query": "Ask HN: Who is hiring", "tags": "story"}
)
ITEM_URL = "https://hn.algolia.com/api/v1/items/{}"


def _latest_story_id() -> str | None:
    resp = get(SEARCH_URL)
    data = resp.json()
    hits = data.get("hits", [])

    def _id(hit):
        return hit.get("objectID") or (
            str(hit["story_id"]) if hit.get("story_id") else None
        )

    # The canonical monthly thread is always posted by the "whoishiring" bot;
    # prefer it over lookalike posts ("...freelance developers?", etc).
    for hit in hits:
        if hit.get("author") == "whoishiring" and "who is hiring" in (
            hit.get("title") or ""
        ).lower():
            return _id(hit)

    # Fallback: latest story whose title looks like the thread.
    for hit in hits:
        title = (hit.get("title") or "").lower()
        if "who is hiring" in title and "ask hn" in title:
            return _id(hit)
    return None


def fetch() -> list[dict]:
    story_id = _latest_story_id()
    if not story_id:
        return []

    resp = get(ITEM_URL.format(story_id))
    item = resp.json()

    jobs: list[dict] = []
    for comment in item.get("children", []) or []:
        raw = comment.get("text")
        if not raw:
            continue
        plain = html_to_text(raw)
        if "remote" not in plain.lower():
            continue  # spec: top-level comment must mention REMOTE

        comment_id = comment.get("id")
        if not comment_id:
            continue

        first_line = next((ln for ln in plain.splitlines() if ln.strip()), "").strip()
        company = (first_line[:200] or None)
        title = (first_line[:200] or "Who is hiring")

        jobs.append(
            {
                "source": SOURCE,
                "external_id": str(comment_id),
                "url": f"https://news.ycombinator.com/item?id={comment_id}",
                "title": title,
                "company": company,
                "description": plain,
                "tags": [],
                "budget": None,
                "location": None,
                "posted_at": parse_dt(comment.get("created_at")),
            }
        )
    return jobs
