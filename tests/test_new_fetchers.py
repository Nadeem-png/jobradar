"""Offline tests for the five newest fetchers' pure parsing logic.

No pytest, no network: run it directly.

    python tests/test_new_fetchers.py

The HTML/JSON fixtures below are trimmed from live responses captured while
building these fetchers, so a markup or schema change upstream shows up here.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.fetchers import FETCHERS, SOURCE_LABELS  # noqa: E402
from app.fetchers.builtin import _normalize as builtin_normalize  # noqa: E402
from app.fetchers.builtin import _remote_label  # noqa: E402
from app.fetchers.jobgether import _parse_jobs as jobgether_parse  # noqa: E402
from app.fetchers.skipthedrive import (  # noqa: E402
    _company_from_slug, _location, _salary,
)
from app.fetchers.underdog import _normalize as underdog_normalize  # noqa: E402
from app.fetchers.underdog import _salary as underdog_salary  # noqa: E402
from app.fetchers.util import parse_relative_dt  # noqa: E402
from app.fetchers.wellfound import _parse_jobs as wellfound_parse  # noqa: E402

_FAILURES: list[str] = []


def check(got, want, label: str) -> None:
    if got != want:
        _FAILURES.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")
        print(f"  FAIL {label}\n     got:  {got!r}\n     want: {want!r}")
    else:
        print(f"  ok   {label}")


def truthy(value, label: str) -> None:
    check(bool(value), True, label)


# --------------------------------------------------------------------------- #
# SkipTheDrive — company recovered from the WordPress slug
# --------------------------------------------------------------------------- #
def test_skipthedrive() -> None:
    print("SkipTheDrive: company from slug")
    cases = [
        ("supabase-release-engineer-1398766", "Release Engineer", "Supabase"),
        ("solana-foundation-trading-growth-lead-1398762", "Trading Growth Lead",
         "Solana Foundation"),
        ("weekday-ai-personal-financial-advisor-private-wealth-1398775",
         "Personal Financial Advisor (Private Wealth)", "Weekday AI"),
        ("bjak-mobile-application-developer-ai-neobank-app-1398734",
         "Mobile Application Developer &#8211; AI Neobank App", "Bjak"),
        ("foundation-health-product-manager-1398747", "Product Manager",
         "Foundation Health"),
        ("neuroscale-founding-sdr-1398757", "Founding SDR", "Neuroscale"),
        ("uncapped-smb-account-executive-1398772", "SMB Account Executive",
         "Uncapped"),
        # WordPress slugifies the *escaped* title, so "&" survives as "amp".
        ("acme-staff-software-engineer-amp-ai-platform-9",
         "Staff Software Engineer &amp; AI Platform", "Acme"),
        # Paid placements carry a "sponsored-job" prefix, not a company.
        ("sponsored-job-at-home-video-recorder", "At-Home Video Recorder", None),
        # Slug is only the title: no company to recover.
        ("release-engineer-123", "Release Engineer", None),
        ("", "Whatever", None),
    ]
    for slug, title, want in cases:
        check(_company_from_slug(slug, title), want, f"slug {slug[:44] or '(empty)'!r}")

    print("SkipTheDrive: salary + location")
    check(_salary("pay $80 – $110/hour for this"), "$80 – $110/hour", "hourly range")
    check(_salary("Range: $100,000 - $130,000 per year"),
          "$100,000 - $130,000 per year", "annual range")
    check(_salary("no numbers here"), None, "no salary")
    check(_location("Location: Remote (US only)\nJob Summary: blah"),
          "Remote (US only)", "location on its own line")
    check(_location("Role Title: X Location: Remote – based in Ohio Job Summary: blah"),
          "Remote – based in Ohio", "location stops at next section")
    check(_location("nothing here"), None, "no location")


# --------------------------------------------------------------------------- #
# Relative freshness labels (Jobgether + Wellfound)
# --------------------------------------------------------------------------- #
def test_relative_dates() -> None:
    print("util.parse_relative_dt")
    now = datetime(2026, 7, 28, 12, 0, 0)
    check(parse_relative_dt("Today", now=now), now, "Today")
    check(parse_relative_dt("Just Now", now=now), now, "Just Now")
    check(parse_relative_dt("Yesterday", now=now), datetime(2026, 7, 27, 12, 0),
          "Yesterday")
    check(parse_relative_dt("3 days ago", now=now), datetime(2026, 7, 25, 12, 0),
          "3 days ago")
    check(parse_relative_dt("2 weeks ago", now=now), datetime(2026, 7, 14, 12, 0),
          "2 weeks ago")
    check(parse_relative_dt("5 hours ago", now=now), datetime(2026, 7, 28, 7, 0),
          "5 hours ago")
    check(parse_relative_dt("Featured", now=now), None, "label with no age")
    check(parse_relative_dt("", now=now), None, "empty label")
    check(parse_relative_dt(None, now=now), None, "None label")


# --------------------------------------------------------------------------- #
# Jobgether — search-offers card markup
# --------------------------------------------------------------------------- #
JOBGETHER_HTML = """
<div class="rounded-lg border text-primary shadow-sm bg-white">
 <div class="flex flex-col space-y-1.5 p-6">
  <div class="flex items-start justify-between gap-3">
   <div class="flex-1 relative w-full">
    <div class="flex flex-col md:flex-row items-start gap-3 mb-3">
     <div class="w-12 h-12"><img alt="AIDA Recruitment" src="x.webp"/></div>
     <div class="flex-1 min-w-0">
      <div class="flex justify-between items-center mb-1">
       <div class="flex flex-col md:flex-row md:items-center gap-2">
        <a class="order-1 font-semibold" href="/offer/6a67cdf51b23f4f87b3f20dd-part-665-android-developer"
           title="PART-665: Android Developer">PART-665: Android Developer</a>
        <div class="order-0 md:order-1 text-xs text-gray-500 block">Today</div>
       </div>
      </div>
      <p class="text-gray-500 text-sm leading-tight w-fit">
       <a class="inline" href="/remote-jobs/company-aida-projektai-mb">AIDA Recruitment</a>
      </p>
     </div>
    </div>
    <div class="flex flex-wrap items-center gap-4 text-sm mb-3">
     <div class="flex-wrap gap-4 text-xs text-gray-600 flex flex-row mb-1">
      <div class="flex items-center gap-1">
       <div class="whitespace-nowrap flex gap-1 items-center justify-center">
        Remote from
        <div class=""><span class="text-wrap truncate">Republic of Lithuania</span></div>
       </div>
      </div>
      <div class="flex items-center gap-1"><a href="/remote-jobs/full-time">Full time</a></div>
      <span>57 - 72 K</span>
     </div>
    </div>
    <div class="flex md:flex-row flex-col gap-4 justify-between">
     <div class="flex flex-wrap md:gap-2 gap-1">
      <a href="/remote-jobs/kotlin"><div class="line-clamp-1 tooltip">Kotlin</div></a>
      <a href="/remote-jobs/android-development"><div class="line-clamp-1 tooltip">Android Development</div></a>
      <a href="/remote-jobs/visualvm"><div class="line-clamp-1 tooltip">VisualVM</div></a>
     </div>
    </div>
   </div>
  </div>
 </div>
</div>
"""


def test_jobgether() -> None:
    print("Jobgether: card parsing")
    jobs = jobgether_parse(JOBGETHER_HTML)
    check(len(jobs), 1, "one card parsed")
    if not jobs:
        return
    job = jobs[0]
    check(job["source"], "jobgether", "source")
    check(job["title"], "PART-665: Android Developer", "title")
    check(job["company"], "AIDA Recruitment", "company")
    check(job["url"],
          "https://jobgether.com/offer/6a67cdf51b23f4f87b3f20dd-part-665-android-developer",
          "url")
    check(job["external_id"], "6a67cdf51b23f4f87b3f20dd", "external_id is the offer id")
    check(job["location"], "Republic of Lithuania", "location after 'Remote from'")
    check(job["budget"], "57 - 72 K", "salary chip")
    check(job["tags"][:3], ["Kotlin", "Android Development", "VisualVM"], "skill chips")
    truthy("Full Time" in job["tags"], "job type in tags")
    truthy(job["posted_at"] is not None, "posted_at from 'Today'")
    # Cards carry no body text; the keyword gate needs something to match on.
    truthy("Kotlin" in job["description"], "skills reach the description")
    truthy("AIDA Recruitment" in job["description"], "company reaches the description")

    check(jobgether_parse(""), [], "empty html -> no jobs")
    check(jobgether_parse("<div>nothing</div>"), [], "no cards -> no jobs")
    check(len(jobgether_parse(JOBGETHER_HTML, max_jobs=0)), 0, "max_jobs respected")


# --------------------------------------------------------------------------- #
# Wellfound — role-search startup card markup
# --------------------------------------------------------------------------- #
WELLFOUND_HTML = """
<div class="mb-6 w-full rounded border bg-white">
 <div class="w-full space-y-2 px-4 pb-2 pt-4">
  <div class="flex-col">
   <div class="flex w-full" data-testid="startup-header">
    <a class="content-center" href="/company/archesys"><img alt="Archesys logo"/></a>
    <div class="pl-2 flex flex-col">
     <div class="flex space-x-2">
      <a href="/company/archesys"><h2 class="inline text-md font-semibold">Archesys</h2></a>
      <div class="flex items-center text-sm">Actively Hiring</div>
     </div>
     <span class="text-xs text-neutral-1000">Improving the government services that impact everyday lives</span>
     <span class="text-xs italic text-neutral-500">11-50 Employees</span>
    </div>
   </div>
  </div>
 </div>
 <div class="mb-4 w-full px-4">
  <div class="min-h-[50px] items-end justify-between rounded-2xl px-2 py-2 sm:flex">
   <div class="w-full pb-1 sm:pb-0">
    <div class="mb-1 flex items-start">
     <a class="mr-2 text-sm font-semibold" href="/jobs/4468480-software-engineer">Software Engineer</a>
     <span class="whitespace-nowrap rounded-lg px-2 py-1 text-[10px]">Full-time</span>
    </div>
    <div class="sm:flex sm:space-x-2">
     <div class="flex items-center text-neutral-500"><span class="pl-1 text-xs">$100k – $137k • No equity</span></div>
     <div class="flex items-center text-neutral-500"><span class="pl-1 text-xs">Remote only • Atlanta</span></div>
     <div class="flex items-center text-neutral-500"><span class="pl-1 text-xs">4 years of exp</span></div>
    </div>
    <span class="text-xs lowercase text-dark-a md:hidden">3 days ago</span>
   </div>
  </div>
 </div>
</div>
"""


def test_wellfound() -> None:
    print("Wellfound: card parsing")
    jobs = wellfound_parse(WELLFOUND_HTML)
    check(len(jobs), 1, "one listing parsed")
    if not jobs:
        return
    job = jobs[0]
    check(job["source"], "wellfound", "source")
    check(job["title"], "Software Engineer", "title")
    check(job["company"], "Archesys", "company from startup header")
    check(job["url"], "https://wellfound.com/jobs/4468480-software-engineer", "url")
    check(job["external_id"], "4468480", "external_id is the listing id")
    check(job["budget"], "$100k – $137k • No equity", "salary")
    check(job["location"], "Remote only • Atlanta", "location")
    truthy(job["posted_at"] is not None, "posted_at from '3 days ago'")
    truthy("Full-time" in job["tags"], "job type in tags")
    truthy("Archesys" in job["description"], "company reaches the description")

    print("Wellfound: block detection")
    check(wellfound_parse('<html><body><script src="https://ct.captcha-delivery.com/i.js">'
                          "</script></body></html>"), [], "DataDome interstitial -> no jobs")
    check(wellfound_parse("<title>Just a moment...</title>"), [],
          "Cloudflare challenge -> no jobs")
    check(wellfound_parse(""), [], "empty html -> no jobs")
    # /jobs/signup is not a listing.
    check(wellfound_parse('<div data-testid="startup-header">'
                          '<a href="/company/x"><h2>X</h2></a></div>'
                          '<a href="/jobs/signup">Sign up</a>'), [],
          "non-listing /jobs links ignored")


# --------------------------------------------------------------------------- #
# Underdog.io — JSON API rows
# --------------------------------------------------------------------------- #
UNDERDOG_ROW = {
    "id": 247,
    "title": "Staff Software Engineer &amp; AI Platform",
    "description": "<p>Our hiring partner is seeking a <b>Staff Software Engineer</b>.</p>",
    "slug": "staff-software-engineer-ai-platform",
    "webflow_slug": "staff-software-engineer-ai-platform-amp-infrastructure",
    "min_salary": 165000,
    "max_salary": 285000,
    "objCategories": [{"id": 1, "name": "Engineering"}],
    "objCities": [{"id": 6, "name": "Philadelphia", "state_abbreviation": "PA"}],
    "created_at": "2026-07-08T06:38:25.969602",
    "is_expired": False,
}


def test_underdog() -> None:
    print("Underdog.io: row normalisation")
    job = underdog_normalize(UNDERDOG_ROW)
    truthy(job is not None, "row normalised")
    if job is None:
        return
    check(job["source"], "underdog", "source")
    check(job["title"], "Staff Software Engineer & AI Platform", "title unescaped")
    check(job["url"],
          "https://underdog.io/jobs/staff-software-engineer-ai-platform-amp-infrastructure",
          "url uses webflow_slug")
    check(job["external_id"], "247", "external_id")
    check(job["company"], None, "company is None (Underdog anonymises partners)")
    check(job["location"], "Philadelphia, PA", "city + state")
    check(job["budget"], "$165k – $285k", "salary range")
    check(job["tags"], ["Engineering"], "categories as tags")
    check(job["posted_at"], datetime(2026, 7, 8, 6, 38, 25, 969602), "posted_at")
    truthy("Staff Software Engineer" in job["description"], "description text")

    print("Underdog.io: edge cases")
    check(underdog_normalize({**UNDERDOG_ROW, "is_expired": True}), None,
          "expired row dropped")
    check(underdog_normalize({**UNDERDOG_ROW, "webflow_slug": "", "slug": ""}), None,
          "row with no slug dropped")
    check(underdog_normalize({**UNDERDOG_ROW, "title": ""}), None,
          "row with no title dropped")
    remote = underdog_normalize({
        **UNDERDOG_ROW,
        "objCities": [{"name": "Remote", "state_abbreviation": "REMOTE"}],
    })
    check(remote["location"], "Remote", "Remote city drops the REMOTE state code")
    check(underdog_salary(180000, 180000), "$180k", "equal min/max collapses")
    check(underdog_salary(0, 0), None, "zero salary -> None")
    check(underdog_salary(None, 250000), "$250k", "max only")
    check(underdog_salary(1_500_000, None), "$1.5M", "millions")


# --------------------------------------------------------------------------- #
# Built In — GraphQL rows
# --------------------------------------------------------------------------- #
BUILTIN_ROW = {
    "id": 3849782,
    "title": "(Senior) Quality Assurance Engineer",
    "url": "/job/senior-quality-assurance-engineer/2611125",
    "location": "Other US Location",
    "originalLocation": "Taipei",
    "remoteStatus": "FULLY_REMOTE",
    "experienceLevel": "Senior",
    "isHybrid": False,
    "bodySummary": "<p>Own the <b>test strategy</b>.</p>",
    "company": {"name": "SHOPLINE"},
}


def test_builtin() -> None:
    print("Built In: row normalisation")
    job = builtin_normalize(BUILTIN_ROW)
    truthy(job is not None, "row normalised")
    if job is None:
        return
    check(job["source"], "builtin", "source")
    check(job["title"], "(Senior) Quality Assurance Engineer", "title")
    check(job["url"],
          "https://builtin.com/job/senior-quality-assurance-engineer/2611125",
          "relative url absolutised")
    check(job["external_id"], "3849782", "external_id")
    check(job["company"], "SHOPLINE", "company")
    check(job["location"], "Other US Location", "location")
    check(job["tags"], ["Fully Remote", "Senior"], "remote + experience as tags")
    truthy("test strategy" in job["description"], "bodySummary text")

    print("Built In: edge cases")
    check(builtin_normalize({**BUILTIN_ROW, "url": ""}), None, "row with no url dropped")
    check(builtin_normalize({**BUILTIN_ROW, "title": ""}), None,
          "row with no title dropped")
    absolute = builtin_normalize({**BUILTIN_ROW, "url": "https://builtin.com/job/x/1"})
    check(absolute["url"], "https://builtin.com/job/x/1", "absolute url kept as-is")
    no_company = builtin_normalize({**BUILTIN_ROW, "company": None})
    check(no_company["company"], None, "missing company -> None")
    check(_remote_label("NOT_REMOTE", False), None, "NOT_REMOTE is not a tag")
    check(_remote_label("FULLY_REMOTE", False), "Fully Remote", "enum prettified")
    check(_remote_label("FULLY_REMOTE", True), "Hybrid", "hybrid flag wins")
    check(_remote_label(None, False), None, "missing status -> None")


# --------------------------------------------------------------------------- #
# Registry wiring
# --------------------------------------------------------------------------- #
def test_registry() -> None:
    print("Registry wiring")
    for source in ("skipthedrive", "jobgether", "underdog", "wellfound", "builtin"):
        truthy(source in FETCHERS, f"{source} in FETCHERS")
        truthy(callable(FETCHERS.get(source)), f"{source} fetch is callable")
        truthy(source in SOURCE_LABELS, f"{source} has a UI label")

    from app.main import SOURCE_COLORS
    from app.settings import DEFAULTS
    for source in ("skipthedrive", "jobgether", "underdog", "wellfound", "builtin"):
        truthy(source in SOURCE_COLORS, f"{source} has a stats colour")
        truthy(source in DEFAULTS["sources_enabled"], f"{source} has an enabled default")

    # Every registered fetcher should be labelled, or the UI shows a raw key.
    unlabelled = [s for s in FETCHERS if s not in SOURCE_LABELS]
    check(unlabelled, [], "no fetcher is missing a label")


def main() -> int:
    for test in (test_skipthedrive, test_relative_dates, test_jobgether,
                 test_wellfound, test_underdog, test_builtin, test_registry):
        test()
        print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)} check(s)")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
