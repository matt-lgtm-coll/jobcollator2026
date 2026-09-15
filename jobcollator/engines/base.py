"""Shared types for job-source engines.

An engine is a module with a single entry point:

    fetch(name: str, start_url: str, session: requests.Session,
          location_filter: str | None = None) -> list[JobPosting]

It knows how to talk to one particular career-site platform (e.g. the SAP
SuccessFactors "Jobs2Web" template, or Workday) and turn whatever that
platform returns into a flat list of JobPosting rows. Everything downstream
(storage, dedupe, the HTML report) only ever deals with JobPosting objects,
so adding support for a new ATS platform never touches the rest of the tool.

`location_filter`, if given (e.g. "Denmark"), should be applied *server-side*
where the platform allows it (a search param, a facet id lookup) rather than
fetched-then-discarded client-side — the whole point is cutting the number of
requests, not just the size of the result set. An empty list is a legitimate
result (zero matching postings right now); engines should only raise
FetchError when the fetch itself failed, not when it legitimately found
nothing.
"""
import html as html_module
import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class JobPosting:
    company: str
    external_id: str      # stable id for this posting *within* the company
    title: str
    location: str
    category: str
    url: str               # absolute URL to the job description
    posted_date: Optional[str]  # ISO 'YYYY-MM-DD' if known, else None
    soft_match: bool = False
    # True when this posting isn't structurally tagged with `location_filter`
    # (so it's excluded from the real, confirmed count and doesn't count as
    # "new" in notifications) but its *title* mentions the filtered-for place
    # as an alternative — e.g. "Senior Director, Computational Drug Discovery
    # (DK/US)" filed under Cambridge, MA with no Denmark location tag at all.
    # Confirmed real and valuable (Zealand Pharma, flagged by the user):
    # companies frequently post one req covering either of two sites without
    # configuring the ATS's structured multi-location field for it. Shown
    # separately in the report rather than folded into the main count, since
    # it's a hint, not a confirmed match.


class FetchError(RuntimeError):
    """Raised when an engine cannot retrieve postings for a company."""


# Country name -> common abbreviation, for engines that want to also catch a
# location only mentioned in a posting's *title* (see JobPosting.soft_match).
# Extend as new countries come up.
COUNTRY_ABBREVIATIONS = {"denmark": "dk"}


def title_mentions_location(title: str, location_filter: str) -> bool:
    """Whole-word check: does `title` mention `location_filter` or its
    abbreviation (e.g. "Denmark" / "DK")? Word-boundary matched so "DK"
    doesn't false-positive inside "DKK" or a name like "Djokovic"."""
    needle = location_filter.lower()
    words = {needle}
    abbr = COUNTRY_ABBREVIATIONS.get(needle)
    if abbr:
        words.add(abbr)
    title_lower = title.lower()
    return any(re.search(rf"\b{re.escape(w)}\b", title_lower) for w in words)


# For interest-tag matching against a posting's full description (see
# tagging.py) — a real network call, so callers cache the result rather than
# fetching it on every report render the way title/category matching works.
MAX_JD_CHARS = 8000  # plenty for any therapeutic-area mention; caps storage/noise


def strip_html(raw_html: str) -> str:
    """Crude but sufficient for keyword matching: drop script/style blocks
    and all other tags, unescape entities, collapse whitespace, cap length."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_JD_CHARS]


def fetch_description_default(session, url: str) -> str:
    """Generic fallback for JobPosting.url pointing at a server-rendered job
    detail page: fetch it and strip the HTML down to plain text. Works for
    every engine confirmed so far *except* Workday and Workable, whose job
    detail pages are JS-rendered shells with no description in the raw
    HTML at all — those define their own `fetch_description` instead (see
    workday.py / workable.py), which tagging.backfill_descriptions() prefers
    over this default when an engine provides one."""
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return strip_html(resp.text)
