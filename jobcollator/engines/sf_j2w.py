"""Engine for the SAP SuccessFactors "Jobs2Web" career-site template.

This is the platform behind careers.novonordisk.com, jobs.lundbeck and
jobs.leo-pharma.com (and a lot of other large enterprises — it's a very
common off-the-shelf SuccessFactors Recruiting Marketing product). Tell-tale
signs of this engine on a career site:

  - a `/search/` page that server-renders a `<table id="searchresults">`
  - `/job/<slug>/<numeric-id>/` URLs for individual postings
  - `/services/jobs/options/facetValues/` used by the filter dropdowns

The `/search/` results page is plain server-rendered HTML (no JS execution
needed), so this engine just paginates it with `requests` + BeautifulSoup.
`/services/` is disallowed in robots.txt on these sites, but `/search/` and
`/job/` are not — this engine only ever touches those two.
"""
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import FetchError, JobPosting

MAX_PAGES = 60  # safety valve: 60 pages, far above any real employer's job count


def _search_root(start_url: str) -> str:
    """Normalize whatever URL is configured down to '<scheme>://<host>/search/'."""
    parsed = urlparse(start_url)
    return f"{parsed.scheme}://{parsed.netloc}/search/"


def _parse_date(text: str):
    text = text.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d %b %Y").date().isoformat()
    except ValueError:
        return None


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    root = _search_root(start_url)
    params = {"locationsearch": location_filter} if location_filter else {}
    postings: list[JobPosting] = []
    seen_ids: set[str] = set()
    startrow = 0
    page_size = None  # discovered from the first page; pages differ by site (25/50/100)

    for _ in range(MAX_PAGES):
        page_params = {**params, "startrow": startrow} if startrow else params
        resp = session.get(root, params=page_params or None, timeout=30)
        if resp.status_code != 200:
            if startrow == 0:
                raise FetchError(f"{name}: GET {root} -> HTTP {resp.status_code}")
            break

        if startrow == 0 and 'id="search-wrapper"' not in resp.text:
            # Not even a recognizable search page (wrong URL / platform changed) —
            # as opposed to a valid page that simply has zero matching postings.
            raise FetchError(f"{name}: {root} doesn't look like a Jobs2Web search page")

        soup = BeautifulSoup(resp.text, "lxml")
        rows = soup.select("table#searchresults tbody tr.data-row")
        if not rows:
            break
        if page_size is None:
            page_size = len(rows)

        for row in rows:
            link = row.select_one("td.colTitle a.jobTitle-link")
            if not link or not link.get("href"):
                continue
            href = link["href"].strip()
            job_url = urljoin(root, href)
            external_id = urlparse(job_url).path.rstrip("/").rsplit("/", 1)[-1]
            if external_id in seen_ids:
                continue
            seen_ids.add(external_id)

            location_el = row.select_one("td.colLocation span.jobLocation")
            category_el = row.select_one("td.colFacility span.jobFacility")
            date_el = row.select_one("td.colDate span.jobDate")

            postings.append(JobPosting(
                company=name,
                external_id=external_id,
                title=link.get_text(strip=True),
                location=location_el.get_text(strip=True) if location_el else "",
                category=category_el.get_text(strip=True) if category_el else "",
                url=job_url,
                posted_date=_parse_date(date_el.get_text()) if date_el else None,
            ))

        if len(rows) < page_size:
            # Short page — this was the last one.
            break
        startrow += page_size

    return postings
