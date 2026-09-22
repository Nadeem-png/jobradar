"""Job feed search: query parsing, matching and relevance ranking.

Why this is not a plain AND-match
---------------------------------
The obvious implementation — require every token, sum field weights — ranks
badly on real job data. Searching "website security" used to drop 294 of 312
security jobs (any listing that never says the literal word "website",
e.g. "Security Architect II") and fill the results with web-developer jobs whose
long description happens to mention security once.

So matching is OR with relevance ranking, and the ranking leans on the signals
that actually separate "a job about X" from "a job that mentions X":

  * Field: a title hit is worth 10x a description mention. "website" occurs in
    136 listings here but only 8 titles — buried mentions are weak evidence.
  * Head noun: in an English noun phrase the last word is the thing being
    searched for ("website security" = security work, "python developer" = a
    developer role), so the final token is boosted.
  * Phrase: tokens appearing adjacent beat the same tokens scattered apart.
  * IDF: dampened, as a tie-breaker only. Raw IDF actively misleads here —
    "website" is rarer than "security" in a corpus of developer jobs, so on its
    own it would rank the qualifier above the head noun.

Query syntax: bare words, "quoted phrases" (matched as a unit and *required*),
and -exclusions.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Field weights. The title/description gap is deliberately wide: it is what
# stops a passing mention from outranking a listing that is actually about the term.
W_TITLE = 10.0
W_TAGS = 5.0
W_COMPANY = 2.0
W_DESCRIPTION = 1.0

# The last token of a multi-word query is the head noun — what the user wants.
# Large enough that the head noun in a title outranks a qualifier in a title:
# for "website security", a Security Engineer must beat a Web Designer.
HEAD_BOOST = 2.0
# Credit for a token appearing in more fields than its best one.
EXTRA_FIELD_FACTOR = 0.1
# Whole query appearing contiguously in one field.
PHRASE_FACTOR = 1.2
# Two adjacent query tokens appearing adjacent in one field.
ADJACENT_PAIR_FACTOR = 0.5
# How hard IDF is allowed to push. Low on purpose (see module docstring) — at
# 0.25 a rare qualifier ("website") still edged out the head noun ("security").
IDF_INFLUENCE = 0.15

# Multi-word queries only: drop results scoring below this fraction of the best
# hit. Without it, one common word carries a flood — "full stack" matched 1067 of
# 2216 jobs, mostly because "full" also matches the "Full-time" tag. Single-word
# queries keep every match, so searching one term still shows everything.
RELATIVE_FLOOR = 0.25

MAX_TOKENS = 8

# Curated equivalences, so "website security" also finds "Web Security Engineer"
# and "js" finds JavaScript. Each entry maps a token to the surface forms that
# should also count as a match for it. Deliberately small and hand-checked —
# generic stemming would conflate things like "security"/"secure".
_ALIASES: dict[str, tuple[str, ...]] = {
    "website": ("web site", "web"),
    "web": ("website",),
    "js": ("javascript",),
    "javascript": ("js",),
    "ts": ("typescript",),
    "typescript": ("ts",),
    "py": ("python",),
    "k8s": ("kubernetes",),
    "kubernetes": ("k8s",),
    "ml": ("machine learning",),
    "ai": ("artificial intelligence",),
    "infosec": ("information security", "security"),
    "appsec": ("application security",),
    "cyber": ("cybersecurity", "cyber security"),
    "security": ("cybersecurity", "infosec"),
    "devops": ("dev ops", "sre"),
    "sre": ("site reliability", "devops"),
    "qa": ("quality assurance", "tester"),
    "frontend": ("front end", "front-end"),
    "backend": ("back end", "back-end"),
    "fullstack": ("full stack", "full-stack"),
    "postgres": ("postgresql",),
    "postgresql": ("postgres",),
    "rails": ("ruby on rails",),
    "node": ("nodejs", "node.js"),
    "dotnet": (".net",),
    "pm": ("product manager",),
}


def _token_pattern(tok: str) -> str:
    """Whole-word pattern for one search token.

    Keeps "java" from matching "javascript" and "web" from matching "website",
    while allowing the common suffixes (plural s/es, js, trailing version
    digits, .js) so "api"→APIs and "python"→Python3 still hit. Spaces in a
    multi-word token become flexible separators ("full stack" ↔ "Full-Stack").
    Word boundaries are skipped next to non-word edges so "c++"/".net" work.
    """
    parts = [re.escape(p) for p in tok.split()]
    body = r"[\s_\-/]+".join(parts)
    start = r"(?<!\w)" if tok[:1].isalnum() else ""
    end = r"(?:s|es|js|\d+|\.js)?(?!\w)" if tok[-1:].isalnum() else ""
    return start + body + end


@dataclass
class Term:
    """One query term: the word the user typed plus its accepted variants."""
    raw: str
    quoted: bool
    patterns: list[re.Pattern] = field(default_factory=list)
    is_head: bool = False
    # Lowercased literal fragments each variant needs, as [(part, part, ...), ...].
    # Every pattern is escaped literals joined by separators, so a variant can
    # only match text containing all of its parts — a cheap necessary condition
    # that lets us skip the regex entirely for most jobs.
    anchors: list[tuple[str, ...]] = field(default_factory=list)

    def possible(self, blob: str) -> bool:
        """Fast reject: could any variant match this text at all?"""
        for parts in self.anchors:
            if all(p in blob for p in parts):
                return True
        return False

    def search(self, text: str):
        for pat in self.patterns:
            m = pat.search(text)
            if m:
                return m
        return None


@dataclass
class Query:
    terms: list[Term] = field(default_factory=list)
    # Exclusions reuse Term so they get the same fast reject + variant matching.
    excludes: list[Term] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.terms or self.excludes)


def _variants(word: str) -> list[str]:
    """A token plus its curated aliases (longest first so the best form wins)."""
    key = word.lower()
    out = [word]
    out.extend(_ALIASES.get(key, ()))
    # "full-stack" typed with punctuation should still reach the "fullstack" entry.
    squashed = re.sub(r"[^a-z0-9]+", "", key)
    if squashed != key:
        out.extend(a for a in _ALIASES.get(squashed, ()) if a not in out)
    return out


def _anchors(variant: str) -> tuple[str, ...]:
    """Lowercased literal fragments a variant needs present to have any chance."""
    return tuple(p for p in variant.lower().split() if p)


def _searchable(text: str) -> bool:
    """A token needs at least one letter or digit to mean anything.

    Without this, a stray quote or dash ('""', '-') became a term that matched
    nothing and silently emptied the whole result set.
    """
    return bool(re.search(r"[A-Za-z0-9]", text or ""))


def parse_query(q: str | None) -> Query:
    """Parse a raw query string into terms, phrases and exclusions.

    Tokens: "quoted phrases" stay whole and are required; -word excludes;
    everything else splits on whitespace. Capped to bound cost.
    """
    query = Query()
    if not q:
        return query

    for m in re.finditer(r'-"([^"]+)"|"([^"]+)"|(-?\S+)', q):
        neg_phrase, phrase, bare = m.group(1), m.group(2), m.group(3)

        if neg_phrase and _searchable(neg_phrase):
            text = neg_phrase.strip()
            query.excludes.append(
                Term(raw=text, quoted=True,
                     patterns=[re.compile(_token_pattern(text), re.I)],
                     anchors=[_anchors(text)])
            )
            continue
        if phrase:
            text = phrase.strip()
            if text and _searchable(text):
                query.terms.append(
                    Term(raw=text, quoted=True,
                         patterns=[re.compile(_token_pattern(text), re.I)],
                         anchors=[_anchors(text)])
                )
            continue

        word = (bare or "").strip()
        if word.startswith("-") and _searchable(word[1:]):
            text = word[1:]
            query.excludes.append(
                Term(raw=text, quoted=False,
                     patterns=[re.compile(_token_pattern(text), re.I)],
                     anchors=[_anchors(text)])
            )
            continue
        if not _searchable(word):
            continue
        variants = _variants(word)
        query.terms.append(
            Term(raw=word, quoted=False,
                 patterns=[re.compile(_token_pattern(v), re.I) for v in variants],
                 anchors=[_anchors(v) for v in variants])
        )
        if len(query.terms) >= MAX_TOKENS:
            break

    if query.terms:
        query.terms[-1].is_head = True
    return query


def _fields(job) -> tuple[tuple[str, float], ...]:
    return (
        (job.title or "", W_TITLE),
        (" ".join(job.tags or []), W_TAGS),
        (job.company or "", W_COMPANY),
        (job.description or "", W_DESCRIPTION),
    )


class _Doc:
    """A job's searchable text, prepared once per query rather than per lookup.

    ``blob`` is every field lowercased and concatenated, used only for the fast
    reject in :meth:`Term.possible`.
    """
    __slots__ = ("job", "fields", "blob")

    def __init__(self, job):
        self.job = job
        self.fields = _fields(job)
        self.blob = " \n ".join(text for text, _ in self.fields if text).lower()


def _phrase_pattern(terms: list[Term]) -> re.Pattern | None:
    """Pattern matching all terms contiguously, in order, in one field."""
    if len(terms) < 2:
        return None
    parts = [r"(?:" + "|".join(re.escape(v) for v in
                               ([t.raw] if t.quoted else _variants(t.raw))) + r")"
             for t in terms]
    return re.compile(r"(?<!\w)" + r"[\s_\-/]+".join(parts) + r"(?!\w)", re.I)


def _hits(query: Query, doc: "_Doc") -> dict | None:
    """Per-term field hits for one job, or None if it can't be a result."""
    for term in query.excludes:
        if not term.possible(doc.blob):
            continue  # can't possibly match — skip the regex
        for text, _ in doc.fields:
            if text and term.search(text):
                return None

    per_term: list[list[float]] = []
    matched = 0
    strong = False
    for term in query.terms:
        if not term.possible(doc.blob):
            if term.quoted:
                return None
            per_term.append([])
            continue
        weights = [w for text, w in doc.fields if text and term.search(text)]
        if weights:
            matched += 1
            # A hit anywhere but the description is real evidence about the role.
            if max(weights) > W_DESCRIPTION:
                strong = True
        elif term.quoted:
            # An explicit "phrase" is a requirement, not a hint.
            return None
        per_term.append(weights)

    if query.terms and matched == 0:
        return None
    # Weak-evidence rule: a job that only mentions *some* of the query deep in
    # its description isn't a result — that tail is what made "full stack" return
    # half the corpus. Matching the whole query still qualifies, wherever it hit.
    if matched < len(query.terms) and not strong:
        return None
    return {"per_term": per_term, "matched": matched}


def _score(query: Query, doc: "_Doc", hits: dict, idf: list[float], phrases: dict) -> float:
    total = 0.0
    for term, weights, term_idf in zip(query.terms, hits["per_term"], idf):
        if not weights:
            continue
        best = max(weights)
        extra = sum(weights) - best
        value = best + EXTRA_FIELD_FACTOR * extra
        if term.is_head and len(query.terms) > 1:
            value *= HEAD_BOOST
        total += value * term_idf

    # Phrase and adjacency bonuses: "website security" as a phrase should beat
    # the same two words scattered across a long description. Only worth scanning
    # when at least two terms actually hit — otherwise no phrase can exist.
    if hits["matched"] > 1 and phrases:
        whole, pairs = phrases["whole"], phrases["pairs"]
        for text, weight in doc.fields:
            if not text:
                continue
            if whole is not None and whole.search(text):
                total += PHRASE_FACTOR * len(query.terms) * weight
            else:
                for pair in pairs:
                    if pair is not None and pair.search(text):
                        total += ADJACENT_PAIR_FACTOR * weight
    return total


def search(jobs: list, q: str | None) -> tuple[list, dict[int, float]]:
    """Filter and rank ``jobs`` for query ``q``.

    Returns ``(kept_jobs, {job_id: relevance})``. ``kept_jobs`` keeps the input
    order — callers sort by relevance. An empty/whitespace query is a no-op.
    """
    query = parse_query(q)
    if not query:
        return jobs, {}

    # Pass 1: collect hits (also gives document frequency for IDF).
    collected: list[tuple[_Doc, dict]] = []
    doc_freq = [0] * len(query.terms)
    for job in jobs:
        doc = _Doc(job)
        hits = _hits(query, doc)
        if hits is None:
            continue
        for i, weights in enumerate(hits["per_term"]):
            if weights:
                doc_freq[i] += 1
        collected.append((doc, hits))

    if not query.terms:
        # Exclusions only ("-wordpress"): keep everything that survived.
        return [d.job for d, _ in collected], {}

    # Pass 2: dampened IDF, then score. Influence is low on purpose — a rare
    # qualifier must not outrank the head noun (see module docstring).
    n = max(1, len(collected))
    idf = [
        1.0 + IDF_INFLUENCE * (math.log(1 + n / (1 + df)) - 1.0)
        for df in doc_freq
    ]
    idf = [max(0.5, v) for v in idf]

    # Phrase patterns are query-level: compile once, not once per job.
    phrases = {}
    if len(query.terms) > 1:
        phrases = {
            "whole": _phrase_pattern(query.terms),
            "pairs": [_phrase_pattern([a, b])
                      for a, b in zip(query.terms, query.terms[1:])],
        }

    scores = [(doc.job, _score(query, doc, hits, idf, phrases))
              for doc, hits in collected]

    floor = 0.0
    if len(query.terms) > 1 and scores:
        floor = RELATIVE_FLOOR * max(s for _, s in scores)

    relevance: dict[int, float] = {}
    kept = []
    for job, score in scores:
        if score < floor:
            continue
        relevance[job.id] = score
        kept.append(job)
    return kept, relevance
