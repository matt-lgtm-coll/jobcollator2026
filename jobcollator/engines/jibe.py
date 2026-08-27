"""Engine for the Jibe / iCIMS "Careers Site" platform.

Confirmed on Medpace's career site (careers.medpace.com). Tell-tale signs:
a `cms.jibecdn.com`-hosted logo/assets, `apply_url`s pointing at an
`*.icims.com` subdomain, and a clean JSON API at `/api/jobs` that the page's
own React frontend calls to render the listing — no HTML scraping needed.

    GET {origin}/api/jobs?page=N&sortBy=relevance&descending=false&internal=false[&country=<name>]

`country` filters server-side against the exact country facet (confirmed:
`country=Denmark` on a company with 780 global postings returned exactly the
3 actually in Denmark) — much cheaper than paginating everything.

Job detail pages live at `{origin}/jobs/{req_id}?lang=<language>`.
"""
from urllib.parse import urlparse

from .base import FetchError, JobPosting

PAGE_SIZE = 10  # fixed by the platform — pageSize/limit query params are ignored
MAX_PAGES = 100  # safety valve: 100 * 10 = 1000 postings


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    parsed = urlparse(start_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    api_url = f"{base}/api/jobs"

    params = {"sortBy": "relevance", "descending": "false", "internal": "false"}
    if location_filter:
        params["country"] = location_filter

    postings: list[JobPosting] = []
    seen_ids: set[str] = set()
    total = None

    for page in range(1, MAX_PAGES + 1):
        resp = session.get(api_url, params={**params, "page": page}, timeout=30)
        if resp.status_code != 200:
            if page == 1:
                raise FetchError(f"{name}: GET {api_url} -> HTTP {resp.status_code}")
            break

        data = resp.json()
        if total is None:
            total = data.get("totalCount", 0)
        jobs = data.get("jobs", [])
        if not jobs:
            break

        for entry in jobs:
            job = entry.get("data", {})
            req_id = job.get("req_id") or job.get("slug")
            if not req_id or req_id in seen_ids:
                continue
            seen_ids.add(req_id)

            categories = job.get("categories") or []
            language = job.get("language", "en-us")

            postings.append(JobPosting(
                company=name,
                external_id=str(req_id),
                title=job.get("title", "").strip(),
                location=job.get("location_name", "") or job.get("full_location", "") or "",
                category=categories[0].get("name", "") if categories else "",
                url=f"{base}/jobs/{req_id}?lang={language}",
                posted_date=(job.get("posted_date") or "")[:10] or None,
            ))

        if len(jobs) < PAGE_SIZE or PAGE_SIZE * page >= total:
            break

    if total is None:
        raise FetchError(f"{name}: no response from {api_url}")

    return postings
