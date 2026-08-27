"""Engine for Workable-hosted career sites (apply.workable.com).

Confirmed on Ascendis Pharma's career site
(apply.workable.com/ascendis-pharma). Workable exposes a clean, public JSON
API used by its own frontend:

    GET  {origin}/api/v3/accounts/<account>/jobs/filters
         -> facet values, including every {countryCode, country, region, city}
            combination postings currently exist in
    POST {origin}/api/v3/accounts/<account>/jobs
         body: {"location": [{"countryCode": "DK"}, ...]}  (omit for no filter)
         -> {"total": N, "results": [...], "nextPage": <opaque cursor or null>}

Pagination is cursor-based: pass the previous response's `nextPage` value
back as `{"token": ...}` to get the next page; a missing/empty `nextPage`
means you're on the last page.

Job detail pages live at `{origin}/<account>/j/<shortcode>/`.
"""
from urllib.parse import urlparse

from .base import FetchError, JobPosting

MAX_PAGES = 100  # safety valve: Workable pages ~10 postings at a time


def _account_slug(start_url: str) -> str:
    segments = [s for s in urlparse(start_url).path.split("/") if s]
    if not segments:
        raise FetchError(f"could not find an account slug in {start_url}")
    return segments[0]


def _matching_country_codes(filters: dict, location_filter: str) -> list[str]:
    needle = location_filter.lower()
    codes = set()
    for loc in filters.get("locations", []):
        if needle in (loc.get("country") or "").lower():
            code = loc.get("countryCode")
            if code:
                codes.add(code)
    return sorted(codes)


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    parsed = urlparse(start_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    account = _account_slug(start_url)
    jobs_url = f"{origin}/api/v3/accounts/{account}/jobs"

    body = {}
    if location_filter:
        filters_resp = session.get(f"{jobs_url}/filters", timeout=30)
        if filters_resp.status_code != 200:
            raise FetchError(f"{name}: GET {jobs_url}/filters -> HTTP {filters_resp.status_code}")
        codes = _matching_country_codes(filters_resp.json(), location_filter)
        if not codes:
            return []  # legitimately zero postings match the filter
        body["location"] = [{"countryCode": c} for c in codes]

    postings: list[JobPosting] = []
    seen_ids: set[str] = set()
    token = None
    total = None

    for _ in range(MAX_PAGES):
        payload = {**body, "token": token} if token else dict(body)
        resp = session.post(jobs_url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
        if resp.status_code != 200:
            if token is None:
                raise FetchError(f"{name}: POST {jobs_url} -> HTTP {resp.status_code}")
            break

        data = resp.json()
        if total is None:
            total = data.get("total", 0)
        results = data.get("results", [])
        if not results:
            break

        for job in results:
            shortcode = job.get("shortcode")
            if not shortcode or shortcode in seen_ids:
                continue
            seen_ids.add(shortcode)

            location = job.get("location") or {}
            location_text = ", ".join(p for p in (location.get("city"), location.get("country")) if p)
            department = job.get("department") or []

            postings.append(JobPosting(
                company=name,
                external_id=shortcode,
                title=job.get("title", "").strip(),
                location=location_text,
                category=department[-1] if department else "",
                url=f"{origin}/{account}/j/{shortcode}/",
                posted_date=(job.get("published") or "")[:10] or None,
            ))

        token = data.get("nextPage")
        if not token:
            break

    if total is None:
        raise FetchError(f"{name}: no response from {jobs_url}")

    return postings
