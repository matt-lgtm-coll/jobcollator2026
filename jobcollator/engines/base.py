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


class FetchError(RuntimeError):
    """Raised when an engine cannot retrieve postings for a company."""
