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
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import FetchError, JobPosting

MAX_PAGES = 60  # safety valve: 60 pages, far above any real employer's job count

# Novo Nordisk's instance of this platform spells September "Sept" (4
# letters — e.g. "1 Sept 2026") instead of the standard 3-letter abbreviation
# strptime's %b expects, so every September posting silently failed to parse
# and landed as posted_date=None. Normalize before parsing rather than
# assuming the site sticks to the standard AP/ISO month abbreviations.
_NONSTANDARD_MONTH_RE = re.compile(r"\bSept\b")

# For verifying a location_filter actually took effect (see fetch()) and, when
# it didn't, filtering client-side instead. Location text on this platform is
# freeform ("Kalundborg, Region Zealand, DK" — no country name at all, just a
# trailing ISO code) so a plain substring check on the country name isn't
# enough by itself; extend this as other countries come up.
_COUNTRY_CODE_HINTS = {"denmark": "dk"}


def _location_matches(location_text: str, location_filter: str) -> bool:
    text = (location_text or "").lower()
    needle = location_filter.lower()
    if needle in text:
        return True
    code = _COUNTRY_CODE_HINTS.get(needle)
    return bool(code) and text.rsplit(",", 1)[-1].strip() == code


def _search_root(start_url: str) -> str:
    """Normalize whatever URL is configured down to '<scheme>://<host>/search/'."""
    parsed = urlparse(start_url)
    return f"{parsed.scheme}://{parsed.netloc}/search/"


def _parse_date(text: str):
    text = text.strip()
    if not text:
        return None
    text = _NONSTANDARD_MONTH_RE.sub("Sep", text)
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


_TOTAL_RESULTS_RE = re.compile(r"of <b>(\d+)</b>")


def _fetch_pages(name: str, root: str, session, params: dict) -> list[JobPosting]:
    postings: list[JobPosting] = []
    seen_ids: set[str] = set()
    startrow = 0
    page_size = None  # discovered from the first page; pages differ by site (25/50/100)
    reported_total = None  # from the page's own "Results X – Y of <total>" label

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
        if reported_total is None:
            m = _TOTAL_RESULTS_RE.search(resp.text)
            if m:
                reported_total = int(m.group(1))

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

        startrow += len(rows)

        if reported_total is not None:
            # Trust the count the page itself reported for "how many pages
            # exist" over inferring it from row-count parity: confirmed on
            # Lundbeck that when the *last* page happens to exactly fill a
            # page (total is a multiple of page_size), asking for "page 2"
            # anyway got back a mix of stale duplicates and completely
            # unrelated postings — the site's own pagination breaks down
            # past the true end when combined with `locationsearch`, so the
            # fix is to never issue that request in the first place.
            if startrow >= reported_total:
                break
        elif len(rows) < page_size:
            # No parseable total (unexpected page structure) — fall back to
            # the row-count heuristic: a short page means this was the last.
            break

    return postings


def fetch(name: str, start_url: str, session, location_filter: str = None) -> list[JobPosting]:
    root = _search_root(start_url)
    # Confirmed on Lundbeck: whatever locale this tenant's board defaults to
    # without an explicit `locale` param doesn't honor `locationsearch` at
    # all (any value — real place name or gibberish — returns the same fixed
    # 10 postings), silently hiding real matches (including, concretely, a
    # "Vice President, Global Medical Safety" role in Copenhagen). Pinning
    # `locale=en_GB` — the locale Novo Nordisk's and LEO Pharma's own pages
    # already default to — fixes it, and is a no-op for tenants where it was
    # already the effective default.
    params = {"locale": "en_GB"}
    if location_filter:
        params["locationsearch"] = location_filter
    postings = _fetch_pages(name, root, session, params)

    if location_filter and postings:
        # Confirmed on Lundbeck: `locationsearch` is accepted but silently
        # ignored on some tenants — passing "Denmark", "Copenhagen", or even
        # a nonsense string like "Mars" all return the exact same fixed 10
        # results (real postings, just not filtered at all). Rather than
        # trust that the param worked, verify at least one returned posting
        # is actually in the requested place; if none are, the "filter" was
        # a no-op, so fetch everything and filter client-side instead.
        if not any(_location_matches(p.location, location_filter) for p in postings):
            postings = [p for p in _fetch_pages(name, root, session, {"locale": "en_GB"})
                        if _location_matches(p.location, location_filter)]

    return postings
