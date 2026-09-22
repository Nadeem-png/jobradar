"""Offline tests for feed search (app/search.py).

No pytest, no network, no database — jobs are plain stand-ins.

    python tests/test_search.py

The headline case is the one that prompted this module: searching
"website security" must return security roles, not web-developer jobs whose
description happens to mention security once.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.search import parse_query, search  # noqa: E402

_FAILURES: list[str] = []


class J:
    """Minimal stand-in for a Job row (search only touches these fields)."""
    _next = 1

    def __init__(self, title="", company=None, tags=None, description=""):
        self.id = J._next
        J._next += 1
        self.title = title
        self.company = company
        self.tags = tags or []
        self.description = description

    def __repr__(self):
        return f"J({self.title!r})"


def check(got, want, label):
    if got != want:
        _FAILURES.append(label)
        print(f"  FAIL {label}\n     got:  {got!r}\n     want: {want!r}")
    else:
        print(f"  ok   {label}")


def truthy(value, label):
    check(bool(value), True, label)


def ranked(jobs, q):
    """Titles ordered best-first."""
    kept, rel = search(jobs, q)
    kept = sorted(kept, key=lambda j: -rel.get(j.id, 0.0))
    return [j.title for j in kept]


# --------------------------------------------------------------------------- #
# The reported bug
# --------------------------------------------------------------------------- #
def test_website_security() -> None:
    print("The 'website security' bug")
    sec_title = J(title="Security Architect II",
                  description="Threat modelling, IAM, incident response.")
    appsec = J(title="Application Security Engineer",
               description="Secure code review for our platform.")
    web_dev = J(title="Senior Web Developer",
                description="Build our website. Follow security best practices.")
    designer = J(title="Website Designer",
                 description="Design marketing pages. Security training provided.")
    web_sec = J(title="Web Security Engineer",
                description="Pen-test customer web applications.")
    unrelated = J(title="Accountant", description="Bookkeeping and payroll.")
    jobs = [web_dev, designer, sec_title, appsec, web_sec, unrelated]

    order = ranked(jobs, "website security")
    print(f"     order: {order}")
    # The job that is literally about website security must win.
    check(order[0], "Web Security Engineer", "'Web Security Engineer' ranks first")

    # Security roles must beat web-dev jobs that merely mention security. A
    # web-dev job may be ranked below or dropped by the relevance floor — both
    # are correct; what must never happen is it sitting above a security role.
    def rank_of(title):
        return order.index(title) if title in order else len(order)

    for sec in ("Security Architect II", "Application Security Engineer"):
        for noise in ("Senior Web Developer", "Website Designer"):
            truthy(rank_of(sec) < rank_of(noise), f"{sec!r} beats {noise!r}")
    truthy("Accountant" not in order, "unrelated job excluded")

    # The old strict-AND behaviour dropped these entirely — the regression guard.
    truthy("Security Architect II" in order,
           "security job kept even though it never says 'website'")

    print("'web security' behaves the same")
    order2 = ranked(jobs, "web security")
    check(order2[0], "Web Security Engineer", "'web security' ranks the same job first")
    sec_at = order2.index("Security Architect II") if "Security Architect II" in order2 else len(order2)
    dev_at = order2.index("Senior Web Developer") if "Senior Web Developer" in order2 else len(order2)
    truthy(sec_at < dev_at, "security role beats web dev for 'web security'")

    print("single-term search still works")
    order = ranked(jobs, "security")
    truthy(order[0] in ("Security Architect II", "Application Security Engineer",
                        "Web Security Engineer"),
           "'security' puts a security role first")
    truthy("Accountant" not in order, "'security' excludes unrelated job")


# --------------------------------------------------------------------------- #
# Ranking rules
# --------------------------------------------------------------------------- #
def test_ranking() -> None:
    print("Field weighting: title beats description")
    a = J(title="Python Developer", description="Build services.")
    b = J(title="Office Manager", description="We use python for reporting.")
    check(ranked([b, a], "python")[0], "Python Developer", "title hit ranks first")

    print("Head noun is what the user wants")
    dev = J(title="Laravel Developer", description="PHP work.")
    designer = J(title="Laravel Designer", description="Visuals. Some developer help.")
    order = ranked([designer, dev], "laravel developer")
    check(order[0], "Laravel Developer", "head noun 'developer' decides")

    print("Phrase adjacency beats scattered tokens")
    together = J(title="Machine Learning Engineer", description="Models.")
    apart = J(title="Engineer", description="Our machine shop is learning new tools.")
    check(ranked([apart, together], "machine learning")[0],
          "Machine Learning Engineer", "adjacent phrase ranks first")

    print("Tags are real evidence")
    tagged = J(title="Backend Engineer", tags=["Kubernetes", "Go"])
    mentioned = J(title="Analyst", description="We run kubernetes somewhere.")
    check(ranked([mentioned, tagged], "kubernetes")[0], "Backend Engineer",
          "tag hit ranks above description mention")


# --------------------------------------------------------------------------- #
# Query syntax
# --------------------------------------------------------------------------- #
def test_syntax() -> None:
    print("Quoted phrases are required, not hints")
    exact = J(title="Machine Learning Engineer")
    partial = J(title="Learning Designer", description="Machine tools.")
    order = ranked([exact, partial], '"machine learning"')
    check(order, ["Machine Learning Engineer"], "quoted phrase excludes non-phrase")

    print("Flexible separators inside a phrase")
    hyphen = J(title="Full-Stack Developer")
    check(ranked([hyphen], '"full stack"'), ["Full-Stack Developer"],
          "'full stack' matches 'Full-Stack'")

    print("Exclusions")
    wp = J(title="WordPress Developer", description="Themes.")
    plain = J(title="Backend Developer", description="APIs.")
    check(ranked([wp, plain], "developer -wordpress"), ["Backend Developer"],
          "-wordpress removes the WordPress job")
    check(ranked([wp, plain], '-"wordpress developer"'), ["Backend Developer"],
          "quoted exclusion works")
    # Exclusion-only query: keep everything else, no ranking.
    kept, rel = search([wp, plain], "-wordpress")
    check([j.title for j in kept], ["Backend Developer"], "exclusion-only query filters")
    check(rel, {}, "exclusion-only query has no relevance scores")

    print("Whole-word matching")
    java = J(title="Java Developer")
    js = J(title="JavaScript Developer")
    check(ranked([java, js], "java"), ["Java Developer"],
          "'java' does not match 'JavaScript'")
    web = J(title="Web Developer")
    site = J(title="Website Developer")
    # "web" is an alias of "website", so both are legitimate hits here; the point
    # is that the plain pattern is whole-word (checked via 'java' above).
    truthy(len(ranked([web, site], "web")) == 2, "'web' matches web and website")

    print("Common suffixes still hit")
    check(ranked([J(title="API Engineer")], "api"), ["API Engineer"], "api")
    check(ranked([J(title="APIs Engineer")], "api"), ["APIs Engineer"], "api -> APIs")
    check(ranked([J(title="Python3 Developer")], "python"), ["Python3 Developer"],
          "python -> Python3")

    print("Punctuation-heavy tokens")
    check(ranked([J(title="C++ Engineer")], "c++"), ["C++ Engineer"], "c++")
    check(ranked([J(title=".NET Developer")], ".net"), [".NET Developer"], ".net")

    print("Aliases")
    check(ranked([J(title="JavaScript Developer")], "js"), ["JavaScript Developer"],
          "js -> JavaScript")
    check(ranked([J(title="Kubernetes Admin")], "k8s"), ["Kubernetes Admin"],
          "k8s -> Kubernetes")
    check(ranked([J(title="Cybersecurity Analyst")], "security"),
          ["Cybersecurity Analyst"], "security -> Cybersecurity")

    print("Degenerate queries are no-ops")
    all_jobs = [J(title="A"), J(title="B")]
    for bad in ("", "   ", None, '""', "-", "!!!", '  "" - '):
        kept, rel = search(all_jobs, bad)
        check(len(kept), 2, f"query {bad!r} returns everything")
    check(parse_query("a b c d e f g h i j k").terms.__len__(), 8,
          "token count is capped at 8")


# --------------------------------------------------------------------------- #
# Result-set shape
# --------------------------------------------------------------------------- #
def test_result_shape() -> None:
    print("Multi-term floor trims the weak tail")
    strong = J(title="Full Stack Developer", description="React and Node.")
    # "full" also matches "Full-time" — the noise class that flooded results.
    noise = [J(title=f"Nurse {i}", tags=["Full-time"], description="Care.")
             for i in range(20)]
    kept, rel = search([strong] + noise, "full stack")
    titles = [j.title for j in sorted(kept, key=lambda j: -rel.get(j.id, 0.0))]
    check(titles[0], "Full Stack Developer", "real match ranks first")
    truthy(len(kept) < 21, f"weak 'Full-time' tail trimmed ({len(kept)} of 21 kept)")

    print("Single-term search keeps every match (no floor)")
    title_hit = J(title="Security Engineer")
    desc_hit = J(title="Developer", description="We care about security.")
    kept, _ = search([title_hit, desc_hit], "security")
    check(len(kept), 2, "single term keeps description-only matches")

    print("Every kept job has a score; scores are positive")
    jobs = [J(title="Python Developer"), J(title="Analyst", description="python")]
    kept, rel = search(jobs, "python")
    check(sorted(rel.keys()), sorted(j.id for j in kept), "scores cover exactly the kept set")
    truthy(all(v > 0 for v in rel.values()), "all scores positive")

    print("Empty corpus and no-match queries")
    check(search([], "python"), ([], {}), "empty corpus")
    kept, rel = search([J(title="Chef")], "python")
    check((kept, rel), ([], {}), "no matches -> empty")

    print("Missing/None fields don't crash")
    sparse = J(title="Engineer")
    sparse.company = None
    sparse.tags = None
    sparse.description = None
    kept, _ = search([sparse], "engineer")
    check(len(kept), 1, "job with None company/tags/description still matches")


def main() -> int:
    for test in (test_website_security, test_ranking, test_syntax, test_result_shape):
        test()
        print()
    if _FAILURES:
        print(f"FAILED: {len(_FAILURES)} check(s)")
        return 1
    print("All search checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
