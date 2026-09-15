"""Engine for the Phenom People career-site platform.

Confirmed on Genmab's career site (careers.genmab.com). Tell-tale signs:
`phenomtrack.min.js`/`phenomapptrack.min.js` assets, and an
`eagerLoadRefineSearch` JSON blob embedded directly in the search-results
page's initial HTML (server-rendered — no JS execution needed to read it):

    GET {origin}/global/en/search-results?q<field>=<value>&from=<offset>

`q<field>` filters server-side (confirmed: `qcountry=Denmark` on a tenant
with hundreds of global postings correctly returned only the ~35 Danish
ones — including a posting whose *primary* displayed location was in the US,
correctly included because Denmark was one of its several valid locations).
`from` paginates in fixed steps of 10 (the platform's own page size — this
is `hits` in the embedded JSON, not configurable via a query param).

Every posting can list multiple valid locations (`multi_location`), and the
one that's actually displayed as "the" location is just whichever the
platform picked as primary — not necessarily the one that matched the
filter. So when filtering, this engine reports the *matching* location
(found via `multi_location`) rather than blindly trusting the primary one,
the same fix `attrax.py` needed for the same underlying problem.

Job detail pages live at `{origin}/global/en/job/<reqId>` — the slugged
title after it is cosmetic; the bare id resolves fine on its own.
"""
import json
from urllib.parse import urlparse

from .base import FetchError, JobPosting

PAGE_SIZE = 10  # fixed by the platform
MAX_PAGES = 100  # safety valve: 100 * 10 = 1000 postings


def _search_root(start_url: str) -> str:
    parsed = urlparse(start_url)
    return f"{parsed.scheme}://{parsed.netloc}/global/en/search-results"


def _extract_refine_search(html_text: str):
    """Pull the `eagerLoadRefineSearch` JSON object out of the page's inline
    script by brace-counting from its opening `{` — it's embedded, not a
    standalone document, so a plain json.loads(whole page) won't work."""
    marker = html_text.find("eagerLoadRefineSearch")
    if marker == -1:
        return None
    start = html_text.find("{", marker)
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(html_text)):
        ch = html_text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html_text[start:i + 1])
                except ValueError:
                    return None
    return None


def _job_locations(job: dict) -> list[str]:
    locs = job.get("multi_location")
    if locs:
        return locs
    single = job.get("cityStateCountry") or job.get("location")
    return [single] if single else []


def _matching_location(job: dict, location_filter: str):
    needle = location_filter.lower()
    for loc in _job_locations(job):
        if needle in loc.lower():
            return loc
    if needle in (job.get("country") or "").lower():
        return job.get("cityStateCountry") or job.get("location") or job.get("country")
    return None


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    root = _search_root(start_url)
    origin = f"{urlparse(root).scheme}://{urlparse(root).netloc}"

    params = {}
    if location_filter:
        params["qcountry"] = location_filter

    postings: list[JobPosting] = []
    seen_ids: set[str] = set()
    total = None

    for page in range(MAX_PAGES):
        offset = page * PAGE_SIZE
        page_params = {**params, "from": offset} if offset else params
        resp = session.get(root, params=page_params or None, timeout=30)
        if resp.status_code != 200:
            if page == 0:
                raise FetchError(f"{name}: GET {root} -> HTTP {resp.status_code}")
            break

        data = _extract_refine_search(resp.text)
        if data is None:
            if page == 0:
                raise FetchError(f"{name}: {root} doesn't look like a Phenom search page")
            break

        if total is None:
            total = data.get("totalHits", 0)
        jobs = (data.get("data") or {}).get("jobs") or []
        if not jobs:
            break

        for job in jobs:
            req_id = job.get("reqId") or job.get("jobId")
            if not req_id or req_id in seen_ids:
                continue
            seen_ids.add(req_id)

            if location_filter:
                location = _matching_location(job, location_filter)
                if location is None:
                    continue
            else:
                location = job.get("cityStateCountry") or job.get("location") or ""

            # Some tenants (e.g. Danaher's shared jobs.danaher.com) list
            # postings for several distinct operating companies under one
            # career site — `opco` names which one a posting actually
            # belongs to, which matters more here than the generic
            # `category` field, so prefer it when present.
            category = job.get("opco") or job.get("category") or ""

            postings.append(JobPosting(
                company=name,
                external_id=req_id,
                title=(job.get("title") or "").strip(),
                location=location,
                category=category,
                url=f"{origin}/global/en/job/{req_id}",
                posted_date=(job.get("postedDate") or "")[:10] or None,
            ))

        if offset + PAGE_SIZE >= total:
            break

    if total is None:
        raise FetchError(f"{name}: no response from {root}")

    return postings
